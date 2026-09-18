"""The loop: contextual action space, Jev decides, loops are treated as missing state.

Overworld: Jev picks a *destination* and the harness walks. Battle: Jev picks an
*intention* and the harness drives the menus. Claude (supervisor) only sets sub-goals.
"""
from __future__ import annotations
import json, time, traceback
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from typesafe_sdk import (Choice, TypeSafeAPIConnectionError, TypeSafeAPITimeoutError,
                          TypeSafeClient, TypeSafeInternalServerError, TypeSafeRateLimitError)

import battle, ram, route, world
from journal import Journal

STUCK_LOG = Path(__file__).with_name("stuck.jsonl")
CASES = Path(__file__).with_name("stuck")        # one folder per reproducible case, for replay.py / repair.py
ARROWS = {"up": "haut", "down": "bas", "left": "gauche", "right": "droite"}
JEV_USD_PER_MTOK = 0.041      # derived from the competitor's panel: $8.24 for 201.11M tokens
LOOP_AFTER, LOOP_CELLS = 30, 3   # free-walking decisions spent on so few cells: a harness hole, frozen for the repairer at once
CLAUDE_TRIES = 3               # calls per milestone without a new fact; beyond that it is a harness hole
KINDS = {"champion d'arène": " — le champion d'arène : lui parler lance le combat pour le badge",
         "dresseur": " — un dresseur : lui parler lance un combat", "objet à ramasser": " — un objet à ramasser",
         "guide de l'arène": " — le guide de l'arène, il donne un conseil"}
FEED_LESSONS = False          # lessons are logged (journal, carnet) but no longer shown to Jev: two of five were false
STEP_BY_STEP = True           # Jev decides every single step; the pathfinder only *describes* where each direction leads
STALL_AFTER = 120 if STEP_BY_STEP else 14   # Jev decisions without progress before Claude is asked (a town is ~40 steps wide)
FACING = {0x00: "down", 0x04: "up", 0x08: "left", 0x0C: "right"}
# DNS gone, timeout, 429, 502: the link dropped, Jev did not answer wrongly. Never a harness bug.
TRANSIENT = (TypeSafeAPIConnectionError, TypeSafeAPITimeoutError,
             TypeSafeRateLimitError, TypeSafeInternalServerError)
LINK_TRIES = 6                # attempts before the outage is reported upwards
LINK_WAIT = 2                 # seconds before retrying, doubled each time: 2, 4, 8, 16, 32
# The pad exists even when the harness fails to describe what is on screen.
RAW_BUTTONS = {"up": "Appuyer sur HAUT.", "down": "Appuyer sur BAS.", "left": "Appuyer sur GAUCHE.",
               "right": "Appuyer sur DROITE.", "a": "Appuyer sur A (valider, parler, avancer le texte).",
               "b": "Appuyer sur B (annuler, revenir en arrière).", "start": "Ouvrir/fermer le menu (START)."}
BLIND_CASE_AT = 3             # same fault while describing the situation before it is frozen for the repairer


class Lead(NamedTuple):
    """Where one direction leads. Named, not a bare tuple: these four facts are read back in
    five places, and an anonymous tuple that gains a field crashes every one of them at once —
    a ValueError inside the *description* of the situation, which is how the game once stopped."""
    pas: int                  # steps left to that destination once this button is pressed
    cible: str                # the named destination ("sortie_2", "parler_1", "bord_est"…)
    texte: str                # how that destination is worded to Jev
    premier: str              # the button itself, or "face:<direction>" when it only turns the hero


def map_name(map_id: int) -> str:
    return ram.MAPS.get(map_id, f"carte {map_id}")


def fault(error: BaseException) -> str:
    """Identity of a failure: its class and the harness function it broke in.

    The missing field, until now: a failure had no identity beyond its *wording*.
    « Name or service not known » and « Network is unreachable » are one and the same
    outage, seen twice; counted on `repr()`, the message change reset the repeat counter
    and froze the same frozen situation as a second case for the repairer to chew on."""
    inside = [f for f in traceback.extract_tb(error.__traceback__)
              if Path(f.filename).parent == Path(__file__).parent]
    where = f"{Path(inside[-1].filename).name}:{inside[-1].name}" if inside else "?"
    return f"{type(error).__name__}@{where}"


def brief(state: dict) -> dict:
    """What Jev reads. Every token here is paid on every decision."""
    team = [f"{m['espece']} niv.{m['niveau']} {m['pv']}/{m['pv_max']} PV" + (f" ({m['statut']})" if m["statut"] else "")
            for m in state["equipe"]]
    out = {"carte": state["carte"], "equipe": team, "badges": len(state["badges"]),
           "argent": state["argent"], "sac": [f"{i['nom']} ×{i['quantite']}" for i in state["sac"]],
           "pokedex": "obtenu" if state["pokedex"] else "pas encore obtenu"}
    if state["combat"]:
        foe = state["combat"]["adversaire"]
        out["combat"] = (f"{state['type_combat']} contre {foe['espece']} niv.{foe['niveau']} "
                         f"({'/'.join(foe['types'])}) {foe['pv']}/{foe['pv_max']} PV")
    if state["dialogue"]:
        out["texte_a_l_ecran"] = state["dialogue"]
    if state["menu"]:
        out["menu"] = state["menu"]["options"]
    if state["quantite"]:
        q = state["quantite"]
        out["choix_de_quantite"] = (f"{q['objet'] or 'objet'} ×{q['nombre']}"
                                    + (f" pour {q['prix']} ¥" if q["prix"] else ""))
    return out


def weak(state: dict) -> str | None:
    team = state["equipe"]
    if not team:
        return None
    alive = [m for m in team if m["pv"] > 0]
    if len(alive) <= len(team) / 2 and len(team) > 1 or sum(m["pv"] for m in team) < 0.35 * sum(m["pv_max"] for m in team):
        return "Équipe affaiblie : aller soigner au Centre Pokémon (parler à l'infirmière, répondre OUI) avant tout combat."
    return None


class Link:
    """Jev at the far end of a link that can drop.

    A network outage is not a situation to decide about: there is nothing on screen that
    changed. So the harness waits instead of dying — and it waits by *ticking the emulator*,
    so the live view keeps breathing — then asks the same question again. The wait is not
    counted as Jev's latency. Only after `LINK_TRIES` does the caller hear about it."""
    def __init__(self, client, game):
        self.client, self.game = client, game
        self.offline: str | None = None        # the missing field: "Jev is unreachable, and since when"
        self.waited = 0.0                      # seconds spent waiting during the last call

    def system_one(self, **question):
        self.waited, started = 0.0, time.time()
        for attempt in range(LINK_TRIES):
            try:
                answer = self.client.system_one(**question)
            except TRANSIENT as error:
                self.offline = (f"Jev injoignable depuis {round(time.time() - started)} s "
                                f"({type(error).__name__}), essai {attempt + 1}/{LINK_TRIES}")
                if attempt + 1 == LINK_TRIES:
                    raise
                self.wait(LINK_WAIT * 2 ** attempt)
            else:
                self.offline = None
                return answer

    def wait(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            spin = time.time()
            self.game.tick(10)                 # frames, not sleep: the browser keeps getting images
            time.sleep(max(0.0, 0.15 - (time.time() - spin)))
        self.waited += seconds


class Agent:
    def __init__(self, game, goal: str, *, supervisor=None, final_goal: str | None = None, stuck_after: int = 3):
        self.game, self.goal, self.stuck_after = game, goal, stuck_after
        self.final_goal = final_goal or goal
        self.supervisor = supervisor
        self.driver = battle.Driver(game)
        self.recent: list[tuple] = []          # (signature, action)
        self.history: list[dict] = []
        self.visited: list[str] = []           # maps, in order of first visit
        self.dialogues: list[str] = []
        self.heard: dict[str, str] = {}        # "map:npc" -> what they said
        self.talking_to: str | None = None
        self.noops: set[tuple] = set()         # (signature, action) that changed nothing
        self.barred: dict[tuple, dict] = {}    # (carte_id, sortie/bord) -> tries that never arrived, and why
        self.barring: tuple | None = None      # the attempt whose text is still being read
        self.defeats: dict[int, str | None] = {}   # carte_id -> the Pokémon that laid the team out
        self.blackout: int | None = None       # map we were knocked out on, until the game lands us back
        self.plans: dict[str, list[str]] = {}
        self.heading: dict[str, int] = {}          # step-by-step: destinations the last step got closer to -> steps left
        self.leads: dict[str, dict[str, int]] = {}
        self.aimed: set[str] = set()           # destinations on the known way to the zone aimed at
        self.intents: dict[str, str | None] = {}   # step-by-step: which named destination a button completes
        self.subgoals: list[dict] = []         # what Claude asked for, newest last
        self.next_area: str | None = None
        self.carnet, self.atlas = route.Carnet(), route.Atlas()
        self.journal = Journal()
        game.fast_options()                    # measured: about one second saved per battle start
        self.damage: dict[str, int] = {}       # "foe:move" -> HP it removed last time: a fact Jev can weigh
        self.pending_hit: tuple | None = None
        self.on_progress = None                # called (done, total) while a plan plays out — for the live view
        self.seen_items: set[str] = set()
        self.milestone: str | None = None
        self.facts_mark: tuple | None = None
        self.claude_tries = 0
        self.source = "carnet"                 # who worded the current sub-goal: carnet, Claude, manuel
        self.stuck_events = 0
        self.progress_mark: tuple | None = None
        self.since_progress, self.stall_limit = 0, STALL_AFTER
        self.faults: Counter = Counter()        # fault fingerprint -> times the description of the situation broke
        self.blind: str | None = None           # the harness could not describe *this* situation: raw buttons only
        self.jev = {"decisions": 0, "tokens": 0, "cout_usd": 0.0, "ms_total": 0}
        self.played_s = 0.0
        self._link: Link | None = None

    def link(self, client) -> Link:
        """Every question to Jev goes through the same patient link."""
        if self._link is None or self._link.client is not client:
            self._link = Link(client, self.game)
        return self._link

    @property
    def offline(self) -> str | None:
        return self._link.offline if self._link else None

    def frozen_case(self, fingerprint: str) -> Path | None:
        """The case already frozen for this exact fault, if there is one."""
        for folder in sorted(CASES.glob("[0-9]*")):
            try:
                if json.loads((folder / "case.json").read_text(encoding="utf-8")).get("empreinte") == fingerprint:
                    return folder
            except (OSError, json.JSONDecodeError):
                continue
        return None

    def open_case(self, kind: str, state: dict, detail: dict, fingerprint: str | None = None) -> Path:
        """Freeze the situation so it can be replayed: emulator state, memory, what Jev was offered.

        The same fault is frozen once: a second folder for it costs the repairer a whole
        intervention on a duplicate, and its three tries."""
        CASES.mkdir(exist_ok=True)
        if fingerprint:
            already = self.frozen_case(fingerprint)
            if already:
                return already
        number = 1 + max([int(p.name[:4]) for p in CASES.iterdir() if p.name[:4].isdigit()], default=0)
        folder = CASES / f"{number:04d}-{kind}"
        folder.mkdir()
        self.game.save(str(folder / "case.state"))
        self.game.pyboy.screen.image.save(folder / "case.png")
        (folder / "case.json").write_text(json.dumps({
            "genre": kind, "jalon": self.milestone, "objectif": self.goal, "etat": brief(state),
            "carte": state["carte"], "position": state["position"],
            **({"empreinte": fingerprint} if fingerprint else {}), **detail,
            "journal_recent": [f"{h['carte']} {h['position']}: {h['bouton']}" for h in self.history[-25:] if not h["auto"]],
            "memoire": self.dump()}, ensure_ascii=False, indent=1), encoding="utf-8")
        return folder

    # ---- memory that survives a restart ---------------------------------
    def dump(self) -> dict:
        return {"goal": self.goal, "final_goal": self.final_goal, "visited": self.visited,
                "dialogues": self.dialogues[-12:], "heard": self.heard, "subgoals": self.subgoals[-8:],
                "next_area": self.next_area, "stuck_events": self.stuck_events, "jev": self.jev,
                "played_s": self.played_s, "seen_items": sorted(self.seen_items),
                "milestone": self.milestone, "atlas": self.atlas.edges, "atlas_vu": self.atlas.seen,
                "claude": self.supervisor.stats if self.supervisor else None}

    def restore(self, saved: dict) -> None:
        self.goal, self.visited = saved.get("goal", self.goal), saved.get("visited", [])
        self.dialogues, self.heard = saved.get("dialogues", []), saved.get("heard", {})
        self.subgoals, self.next_area = saved.get("subgoals", []), saved.get("next_area")
        self.stuck_events, self.played_s = saved.get("stuck_events", 0), saved.get("played_s", 0.0)
        self.jev.update(saved.get("jev", {}))
        self.seen_items = {i for i in saved.get("seen_items", []) if isinstance(i, str)}
        self.milestone, self.atlas.edges = saved.get("milestone"), saved.get("atlas", {})
        self.atlas.seen = saved.get("atlas_vu", {k: list(v) for k, v in self.atlas.edges.items()})
        if self.supervisor and saved.get("claude"):
            self.supervisor.stats.update({k: v for k, v in saved["claude"].items() if k != "modele"})
        self.progress_mark = None                          # fresh session: ask Claude where we stand

    # ---- action space ---------------------------------------------------
    def describe(self, state: dict) -> dict[str, str]:
        """The menu offered to Jev — and what is offered when the harness fails to build it.

        The missing action, until now: *the buttons themselves*. Describing the situation is
        the harness's own job, so it is the harness that can be wrong (a bad read, a route
        table that changed shape). Such a failure used to come straight back out of `step`,
        and the caller replayed the very same frozen situation two seconds later: the same
        crash for ever, the game never advancing again. A console has seven buttons whatever
        the harness understands; the fault is named, frozen once for the repairer, said to Jev
        in the state, and the run keeps going blind rather than not going at all."""
        try:
            space = self.action_space(state)
        except Exception as error:
            self.plans, self.intents, self.leads = {}, {}, {}
            self.blind = fault(error)
            self.faults[self.blind] += 1
            self.journal.write("pannes", {"panne": self.blind, "carte": state["carte"], "position": state["position"],
                                          "repetitions": self.faults[self.blind], "trace": traceback.format_exc()[-800:]})
            if self.faults[self.blind] == BLIND_CASE_AT:      # not a hiccup: a bug in the description
                self.open_case("exception", state, {"panne": self.blind, "trace": traceback.format_exc()},
                               fingerprint=self.blind)
            return dict(RAW_BUTTONS)
        self.blind = None
        return space

    def action_space(self, state: dict) -> dict[str, str]:
        """Only what makes sense right now. The app owns this table."""
        self.plans = {}
        if battle.at_move_list(state):
            return battle.move_options(state, self.damage)
        if battle.at_main_menu(state):
            return battle.options(state, self.damage)
        if battle.at_party_screen(state):
            return battle.party_options(state)
        if state["quantite"]:
            q = state["quantite"]
            what = f" de {q['objet']}" if q["objet"] else ""
            total = f", total {q['prix']} ¥" if q["prix"] else ""
            return {
                "up":    f"Augmenter la quantité{what} d'une unité (actuellement {q['nombre']}{total}).",
                "down":  f"Diminuer la quantité{what} d'une unité (actuellement {q['nombre']}{total}).",
                "a":     f"Valider cette quantité : {q['nombre']}{what}{total}.",
                "b":     "Renoncer et revenir à la liste.",
            }
        if state["menu_ouvert"]:
            return {
                "a":     f"Valider la ligne surlignée : « {state['menu']['surligne']} ».",
                "b":     "Fermer le menu / répondre non / revenir en arrière.",
                "up":    "Monter d'une ligne dans le menu.",
                "down":  "Descendre d'une ligne dans le menu.",
                "left":  "Déplacer le curseur à gauche (grilles de lettres, menus à colonnes).",
                "right": "Déplacer le curseur à droite (grilles de lettres, menus à colonnes).",
                "start": "Terminer la saisie d'un nom et garder le nom actuel.",
            }
        if state["en_combat"]:
            return {"a": "Continuer le combat.", "b": "Annuler."}
        return self.step_space(state) if STEP_BY_STEP else self.destinations(state)

    def step_space(self, state: dict) -> dict[str, str]:
        """One decision per step. The harness knows the routes; it *says* where each direction
        leads and how far, and Jev presses the button. Nothing walks on its own."""
        named = self.destinations(state)                       # fills self.plans with a full route per destination
        routes, self.plans, self.intents, self.leads = self.plans, {}, {}, {}
        facing = state.get("orientation")
        toward: dict[str, list] = {b: [] for b in world.STEPS}
        in_front = None
        for key, text in named.items():
            route = routes.get(key)
            if not route:
                continue
            first = route[0]
            if first.startswith("face:") and first.removeprefix("face:") == facing:
                first = route[1]                                # already facing it: what remains is the A press
            if first == "a":
                in_front = (key, text)
            else:
                toward[first.removeprefix("face:")].append(Lead(len(route), key, text, first))

        here = (state["position"]["x"], state["position"]["y"])
        trail = [(h["position"]["x"], h["position"]["y"]) for h in self.history[-6:] if h["carte"] == state["carte"]]
        space: dict[str, str] = {}
        for button, leads in toward.items():
            free = named.pop(button, None)                     # destinations() offers bare steps when nothing is reachable
            if not leads and not free:
                continue
            leads.sort()
            label = f"Un pas vers {ARROWS[button]}"
            if leads and leads[0].premier.startswith("face:"):
                label = f"Se tourner vers {ARROWS[button]}"
            self.leads[button] = {lead.cible: lead.pas for lead in leads}
            going = [lead.texte.split(", à ")[0] for lead in leads
                     if lead.cible in self.heading and lead.pas < self.heading[lead.cible]]
            if leads:
                # Trois destinations tiennent dans une option : les plus proches d'abord, mais jamais
                # au prix de celle qui mène à la zone visée. À Argenta, « bord est vers Route 3 » était
                # dixième sur douze et disparaissait dans « (et 9 autres) » : la seule direction qui y
                # menait se décrivait par un musée et une boutique.
                first = sorted(leads, key=lambda lead: (lead.cible not in self.aimed, lead.pas))
                shown = " ; ".join(lead.texte.rstrip(".") for lead in first[:3])
                label += f" — rapproche de : {shown}" + (f" (et {len(leads) - 3} autres)" if len(leads) > 3 else "")
                self.intents[button] = leads[0].cible if leads[0].pas <= 3 else None  # about to arrive: this step *is* that action
                self.plans[button] = [leads[0].premier]
            else:
                label += " — aucune destination connue par là"
                self.plans[button] = [button]
            target = (here[0] + world.STEPS[button][0], here[1] + world.STEPS[button][1])
            if going:                                          # a fact about his own last move, not an order
                label += f" ↳ poursuit ton mouvement précédent vers : {' ; '.join(going[:2])}"
            if target in trail:
                label += " (case quittée il y a peu : demi-tour)"
            space[button] = label + "."
        if in_front:
            space["a"] = f"Appuyer sur A, c'est juste devant : {in_front[1]}"
            self.intents["a"] = in_front[0]
        else:
            space["a"] = named.get("a", "Interagir avec ce qui est juste devant (panneau, objet, meuble).")
        self.plans["a"] = ["a"]
        return space

    def destinations(self, state: dict) -> dict[str, str]:
        read, here = self.game.shifted(), (state["position"]["x"], state["position"]["y"])
        grid = world.walkable_grid(read, self.game.rom)
        people = world.npcs(read)
        occupied = {(p["x"], p["y"]) for p in people}
        space: dict[str, str] = {}
        exits, borders = world.warps(read), world.connections(read)
        for name in [map_name(w["vers"]) for w in exits] + [map_name(m) for m in borders.values()]:
            self.atlas.see(state["carte"], name)       # a destination read on the map is a fact, door taken or not
        hop, aim = self.atlas.heading(state["carte"], self.next_area, set(self.visited))
        self.aimed = set()          # the destinations that are the known way to the zone aimed at

        def tag(name: str, key: str = "") -> str:
            """Un pas vers la zone visée reste un pas vers la zone visée quand elle est *voisine* :
            la marque sautait justement dans ce cas (`aim != name`), et la sortie vers la zone
            du jalon se retrouvait décrite comme n'importe quelle porte du décor."""
            if name == hop and key:
                self.aimed.add(key)
            return ((" (déjà visitée)" if name in self.visited else " (jamais visitée)")
                    + (f" ← sur le chemin connu vers « {aim} »" if name == hop and aim != name
                       else " ← c'est la zone visée" if name == hop else ""))

        def known(action: str, dest: int) -> str:
            """What the harness has already lived through on this way: a route tried and never
            arrived at, a map where the team was laid out. Facts, exactly like a door read on
            the map — the description said « à 38 pas » while nothing ever got there."""
            said, seen, lost = "", self.barred.get((state["carte_id"], action)), self.defeats.get(dest)
            if seen:
                said += (f" Essayée {seen['essais']} fois sans jamais y arriver"
                         + (f" : « {seen['texte']} »" if seen["texte"] else "") + ".")
            if dest in self.defeats:
                said += " J'y ai déjà perdu un combat" + (f" contre {lost}" if lost else "") + " (équipe K.O.)."
            return said

        for i, warp in enumerate(exits):
            cell, name = (warp["x"], warp["y"]), map_name(warp["vers"])
            steps = world.path(grid, here, [cell], occupied)
            if steps is None:
                continue
            if cell == here and not world.off_map_direction(grid, *cell):
                continue                                   # a staircase we are standing on: step off and back, nothing to offer
            leave = world.off_map_direction(grid, *cell)
            self.plans[f"sortie_{i}"] = steps + ([leave] * 2 if leave else [])
            heal = (" On y soigne l'équipe." if warp["vers"] in ram.POKEMON_CENTERS else
                    " On y achète des objets (Poké Ball, Potion…)." if warp["vers"] in ram.MARTS else "")
            space[f"sortie_{i}"] = (f"Entrer dans « {name} »{tag(name, f'sortie_{i}')}, à {len(steps)} pas.{heal}"
                                    + known(f"sortie_{i}", warp["vers"]))

        for p in people:
            spots = world.talk_cells(grid, p["x"], p["y"])
            steps = world.path(grid, here, list(spots), occupied)
            if steps is None:
                continue
            end = here
            for s in steps:
                end = (end[0] + world.STEPS[s][0], end[1] + world.STEPS[s][1])
            self.plans[f"parler_{p['n']}"] = steps + [f"face:{spots[end]}", "a"]   # face, do not move
            said = self.heard.get(f"{state['carte_id']}:{p['n']}")
            counter = p["n"] == 1 and state["carte_id"]
            who = ("à l'infirmière (soigne toute l'équipe)" if counter in ram.POKEMON_CENTERS else
                   "au vendeur derrière le comptoir (ouvre ACHETER / VENDRE)" if counter in ram.MARTS else
                   f"au personnage n°{p['n']}{KINDS.get(p.get('role'), '')}")
            space[f"parler_{p['n']}"] = (f"Aller parler {who}, à {len(steps)} pas."
                                         + (f" Déjà fait, réponse : « {said} »" if said else " Jamais fait."))

        for side, target in borders.items():
            steps = world.path(grid, here, world.edge_cells(grid, side), occupied)
            if steps is not None:
                name = map_name(target)
                self.plans[f"bord_{side}"] = steps + [world.EDGES[side][1]] * 2
                space[f"bord_{side}"] = (f"Quitter cette carte par le bord {side} vers « {name} »"
                                         f"{tag(name, f'bord_{side}')}, à {len(steps)} pas."
                                         + known(f"bord_{side}", target))

        grass = set(world.grass_cells(read, self.game.rom))
        if grass and state["equipe"] and not weak(state):
            steps = world.path(grid, here, list(grass), occupied)
            if steps is not None:
                end = here
                for s in steps:
                    end = (end[0] + world.STEPS[s][0], end[1] + world.STEPS[s][1])
                pace = next(([b, o] for b, o in (("left", "right"), ("right", "left"), ("up", "down"), ("down", "up"))
                             if (end[0] + world.STEPS[b][0], end[1] + world.STEPS[b][1]) in grass), [])
                self.plans["entrainement"] = steps + pace * 8
                space["entrainement"] = ("Marcher dans les hautes herbes pour combattre des Pokémon sauvages "
                                         "(gagner des niveaux ou capturer).")

        if len(space) < 2:                                 # single steps only when nothing is reachable
            trail = [(h["carte"], h["position"]["x"], h["position"]["y"]) for h in self.history[-8:]]
            for button, (dx, dy) in world.STEPS.items():
                x, y = here[0] + dx, here[1] + dy
                if (state["carte"], x, y) in trail:
                    continue                               # no pacing back and forth
                if 0 <= y < len(grid) and 0 <= x < len(grid[0]) and grid[y][x] and (x, y) not in occupied:
                    space[button] = f"Faire un seul pas vers {ARROWS[button]} (le chemin est libre)."
        space["a"] = "Interagir avec ce qui est juste devant (panneau, objet, meuble)."
        return space

    # ---- execution ------------------------------------------------------
    def walk(self, plan: list[str]) -> int:
        """Follow a plan; stop as soon as the game takes control (text, battle, new map)
        or the route goes stale.

        A plan is a route read from *one* square: if a step does not land where it should —
        the press was swallowed while a script held the controls, a character stepped into
        the way — every button after it is played from the wrong square and the walk ends
        anywhere. Stop there and read the map again."""
        start = self.game.state()
        start_map, done = start["carte_id"], 0
        here = (start["position"]["x"], start["position"]["y"])
        for button in plan:
            turn, button = button.startswith("face:"), button.removeprefix("face:")  # turn on the spot
            before = self.game.state()["position"]
            self.game.press(button)
            state = self.game.state()
            if button in world.STEPS and not turn and state["position"] == before and state["carte_id"] == start_map \
                    and not state["texte_affiche"] and not state["en_combat"]:
                self.game.press(button)                # the first press only turned the player
                state = self.game.state()
            done += 1
            if self.on_progress:
                self.on_progress(done, len(plan))
            if state["carte_id"] != start_map or state["texte_affiche"] or state["en_combat"]:
                break
            if button in world.STEPS and not turn:
                here = (here[0] + world.STEPS[button][0], here[1] + world.STEPS[button][1])
                if (state["position"]["x"], state["position"]["y"]) != here:
                    break                              # stale route: the rest of the plan means nothing
        return done

    def settle(self) -> dict:
        """Cutscenes and battle animations ask nothing: wait until the game stands still.

        While the game holds the controls (`scene_scriptee`: a character walks its script,
        the hero is being walked back), the buttons are swallowed — asking Jev then buys a
        decision that cannot be played. Same for the few frames after a door, where the map
        id is the new one and the position still the old one: the whole action space would
        be read from the map we just left, and the walk back to a Pokémon Center after a
        knock-out, which asks nothing either."""
        if self.blackout is not None:
            for _ in range(120):
                if self.game.state()["carte_id"] != self.blackout:
                    break
                self.game.tick(20)
            self.blackout = None
        # position, map and *what is drawn*: a box still being filled in is not a question either
        face = lambda s: (s["carte_id"], s["position"], s["dialogue"], str(s["menu"]), str(s["quantite"]))
        state = self.game.state()
        for _ in range(120):
            self.game.tick(20)
            after = self.game.state()
            idle_battle = after["en_combat"] and not after["menu_ouvert"] and not after["texte_affiche"]
            here = world.loaded(self.game.shifted(), after["position"]["x"], after["position"]["y"])
            if face(after) == face(state) and not idle_battle and not after["scene_scriptee"] and here:
                return after
            state = after
        return state

    def stuck_action(self, sig) -> str | None:
        """The action we keep choosing in this exact situation, if any."""
        repeats = Counter(b for s, b in self.recent[-12:] if s == sig)
        if repeats and repeats.most_common(1)[0][1] >= self.stuck_after:
            return repeats.most_common(1)[0][0]
        return None

    # ---- supervision ----------------------------------------------------
    def _count(self, tokens: int) -> None:
        self.jev["decisions"] += 1; self.jev["tokens"] += tokens
        self.jev["cout_usd"] = self.jev["tokens"] * JEV_USD_PER_MTOK / 1e6

    def orient(self, client, state: dict) -> None:
        """Jev judges which notebook milestone we are in — only when a fact changes."""
        self.seen_items |= {i["nom"] for i in state["sac"]}
        facts_mark = (len(state["badges"]), tuple(sorted(i["nom"] for i in state["sac"])),
                      len(state["equipe"]), state["pokedex"])
        if facts_mark == self.facts_mark and self.milestone:
            return
        self.facts_mark, self.claude_tries = facts_mark, 0
        choice, confidence, tokens = self.carnet.locate(client, {
            "badges_obtenus": state["badges"], "equipe": brief(state)["equipe"],
            "pokedex_obtenu": state["pokedex"],
            "sac_actuel": [i["nom"] for i in state["sac"]], "objets_deja_eus": sorted(self.seen_items),
            "cartes_visitees": self.visited[-25:], "carte_actuelle": state["carte"]})
        self._count(tokens)
        self.journal.write("reperages", {"decision": self.jev["decisions"], "faits": {
            "badges": state["badges"], "sac": [i["nom"] for i in state["sac"]], "equipe": brief(state)["equipe"],
            "pokedex": state["pokedex"], "carte": state["carte"]}, "avant": self.milestone, "choix": choice,
            "confiance": confidence, "tokens": tokens})
        if choice != self.milestone:
            self.journal.write("jalons", {"de": self.milestone, "vers": choice, "decisions": self.jev["decisions"],
                                          "tokens": self.jev["tokens"], "cout_jev_usd": self.jev["cout_usd"],
                                          "joue_s": round(self.played_s), "blocages": self.stuck_events,
                                          "claude": dict(self.supervisor.stats) if self.supervisor else None,
                                          "equipe": brief(state)["equipe"], "carte": state["carte"]})
            self.journal.progressed(self.jev["decisions"], f"jalon {self.milestone} → {choice}")
            self.journal.bench(choice, self.game, {**self.dump(), "milestone": choice})
            self.milestone, self.source = choice, "carnet"
            self.since_progress, self.stall_limit = 0, STALL_AFTER
            self.recent.clear(); self.noops.clear()
            self.barred.clear()                        # a new fact (badge, Pokémon) can open a barred way

    def supervise(self, state: dict, space: dict[str, str]) -> None:
        """The notebook words the sub-goal for free. Claude only when we stall, and it leaves a lesson."""
        step = self.carnet.get(self.milestone)
        mark = (self.milestone, len(self.visited), len(state["equipe"]),
                sum(m["niveau"] for m in state["equipe"]), len(self.heard))
        if mark != self.progress_mark:
            if self.progress_mark is not None:
                self.journal.progressed(self.jev["decisions"], f"{state['carte']} · {len(self.visited)} cartes · niveaux {mark[3] if len(mark) > 3 else '?'}")
            self.progress_mark, self.since_progress, self.stall_limit = mark, 0, STALL_AFTER
        if step and self.source == "carnet":
            self.goal, self.next_area = step["texte"], step["zone"]

        if not self.supervisor:
            return
        answer = self.supervisor.take()
        if answer:
            self.journal.claude_answered(self.jev["decisions"], self.milestone, answer)
            self.goal, self.source = answer["sous_objectif"], "Claude"
            self.next_area = answer.get("prochaine_zone") or self.next_area
            self.carnet.learn(self.milestone, answer.get("lecon", ""))
            self.subgoals.append({"sous_objectif": self.goal, "zone": self.next_area,
                                  "raison": answer.get("raison", ""), "coup": len(self.history)})
            self.recent.clear(); self.noops.clear()
            self.since_progress = 0
        elif self.since_progress >= self.stall_limit and self.claude_tries >= CLAUDE_TRIES:
            if self.claude_tries == CLAUDE_TRIES:          # say it once, then stay quiet until a fact changes
                self.claude_tries += 1
                self.source = "carnet"
                self.open_case("trou", state, {
                    "essais_claude": [s["sous_objectif"] for s in self.subgoals[-CLAUDE_TRIES:]],
                    "options_proposees": space})
                with STUCK_LOG.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"trou_de_harnais": True, "jalon": self.milestone, "etat": brief(state),
                                             "essais_claude": [s["sous_objectif"] for s in self.subgoals[-CLAUDE_TRIES:]],
                                             "candidats": list(space)}, ensure_ascii=False) + "\n")
            self.since_progress = 0
        elif self.since_progress >= self.stall_limit:
            self.claude_tries += 1
            reason = f"stagnation : {self.since_progress} décisions sans progrès"
            self.stall_limit = min(self.stall_limit * 2, 120)
            self.since_progress = 0
            asked = {
                "objectif_final": self.final_goal, "motif": reason,
                "jalon_du_carnet": step["texte"] if step else None, "lecons_deja_notees": self.carnet.lessons(self.milestone),
                "alerte": weak(state), **brief(state), "cartes_visitees": self.visited[-14:],
                "cartes_connues_jamais_explorees": self.atlas.unexplored(state["carte"], set(self.visited))[:8],
                "derniers_dialogues": self.dialogues[-6:],
                "sous_objectifs_tentes": [self.goal] + [s["sous_objectif"] for s in self.subgoals[-4:]],
                "journal_recent": [f"{h['carte']}: {h['bouton']}" for h in self.history[-12:] if not h["auto"]],
                "actions_possibles_maintenant": list(space.values())[:14]}
            self.journal.claude_asked(self.jev["decisions"], asked)
            self.supervisor.ask_async(asked)

    def force_goal(self, text: str) -> None:
        self.goal, self.source = text, "manuel"
        self.recent.clear(); self.noops.clear()

    # ---- one decision ---------------------------------------------------
    def step(self, client, on_decision=None) -> dict:
        started_step = time.perf_counter()
        try:
            return self._step(client, on_decision)
        finally:
            self.played_s += time.perf_counter() - started_step

    def _step(self, client, on_decision) -> dict:
        state = self.settle()
        if state["carte"] not in self.visited:
            self.visited.append(state["carte"])

        if state["texte_affiche"] and not state["menu_ouvert"]:
            # Plain text leaves nothing to decide: the harness turns the pages.
            pages, presses = [], 0
            while state["texte_affiche"] and not state["menu_ouvert"] and presses < 60:
                text = state["dialogue"]
                if text and text not in pages:
                    # text prints letter by letter: a longer version replaces its own prefix
                    pages = [p for p in pages if not text.startswith(p)] + [text]
                self.game.press("a"); presses += 1
                state = self.game.state()
                if state["equipe"] and all(m["pv"] == 0 for m in state["equipe"]):
                    # Whole team down: the game blacks out to a Pokémon Center and heals it,
                    # so a decision later nothing says the fight was lost, nor where.
                    self.defeats[state["carte_id"]] = (state["combat"]["adversaire"]["espece"]
                                                       if state["combat"] else None)
                    self.blackout = state["carte_id"]
            spoken = " / ".join(pages)
            if spoken:
                self.dialogues = (self.dialogues + [spoken[-220:]])[-12:]
            if self.talking_to and pages:
                self.heard[self.talking_to] = spoken[-160:]
            if self.barring and spoken:
                self.barred[self.barring]["texte"] = spoken[-140:]   # what stopped the last attempt
            self.talking_to = self.barring = None
            self.journal.write("textes", {"carte": state["carte"], "position": state["position"], "jalon": self.milestone,
                                          "combat": bool(state["en_combat"]), "pages": pages, "appuis": presses})
            record = {"carte": state["carte"], "position": state["position"], "bouton": "a",
                      "auto": True, "dialogue": spoken, "confiance": 1.0, "ms": 0,
                      "bloque": False, "cotes": {}, "options": {}, "pas": presses}
            self.history.append(record)
            return record

        if self.pending_hit and (state["menu_ouvert"] or not state["en_combat"]):
            species, move, hp_before = self.pending_hit
            foe = state["combat"]["adversaire"] if state["combat"] else None
            if foe and foe["espece"] == species:
                self.damage[f"{species}:{move}"] = max(0, hp_before - foe["pv"])
            elif any(m["pv"] > 0 for m in state["equipe"]):
                self.damage[f"{species}:{move}"] = hp_before            # it is gone and we are standing: that hit finished it
            self.pending_hit = None
        sig = ram.signature(state)
        jev = self.link(client)
        if not state["en_combat"] and not state["menu_ouvert"]:
            self.orient(jev, state)
        space = self.describe(state)
        self.supervise(state, space)
        offered = dict(space)
        kept = {k: v for k, v in space.items() if (sig, k) not in self.noops}
        space = kept or space
        blocked = self.stuck_action(sig)
        if blocked and blocked in space and len(space) > 1:
            # A loop means the state is missing something. Record it, then stop
            # offering the action that changes nothing so the run can continue.
            self.stuck_events += 1
            with STUCK_LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"objectif": self.goal, "bouton_bloque": blocked,
                                         "etat": brief(state), "candidats": list(space)},
                                        ensure_ascii=False) + "\n")
            space = {k: v for k, v in space.items() if k != blocked}

        started = time.perf_counter()
        lessons = self.carnet.lessons(self.milestone)
        context = {"objectif": self.goal, **({"lecons": lessons} if lessons and FEED_LESSONS else {}), "objectif_final": self.final_goal, "jeu": "Pokémon Bleu",
                   "etat": brief(state),
                   "derniers_textes": self.dialogues[-3:] if state["en_combat"] else self.dialogues[-1:],
                   "derniers_coups": [b for _, b in self.recent[-6:]]}
        alert = None if state["en_combat"] else weak(state)
        if alert:
            context["alerte"] = alert
        if self.blind:                       # a fact about the harness, not about the game
            context["harnais"] = (f"Panne du harnais ({self.blind}) : il n'a pas pu décrire la situation. "
                                  "Aucune destination n'est calculée, seules les touches brutes sont proposées ; "
                                  "fie-toi à l'écran et au texte.")
        response = jev.system_one(state=context, questions={"action": Choice(
            instructions="Quelle action effectuer maintenant pour avancer vers l'objectif ?", criteria=space)})
        answer = response.choices["action"]
        decision_ms = (time.perf_counter() - started - jev.waited) * 1000   # an outage is not latency
        tokens = getattr(getattr(response, "usage", None), "input_tokens", 0) or 0
        self._count(tokens)
        self.jev["ms_total"] += round(decision_ms)
        if not state["en_combat"]:                       # a long fight is not a stall, and Claude cannot act in one
            self.since_progress += 1

        action = answer.choice
        record = {"carte": state["carte"], "position": state["position"],
                  "bouton": action, "auto": False, "dialogue": state["dialogue"],
                  "confiance": float(answer.confidence), "ms": round(decision_ms), "tokens": tokens,
                  "bloque": bool(blocked), "pas": len(self.plans.get(action, [action])), "options": space,
                  "cotes": {k: float(v) for k, v in answer.probabilities.items()}}
        if on_decision:
            on_decision(record)                        # show the choice before it plays out

        if action.split("_")[0] in ("attaque", "changer", "capturer", "potion", "fuite", "choisir"):
            ran = self.driver.run(action, state)
            if ran and state["combat"] and action.startswith("attaque_"):
                move = next((m["nom"] for m in state["combat"]["actif"]["attaques"] if f"attaque_{m['case']}" == action), None)
                foe = state["combat"]["adversaire"]
                self.pending_hit = (foe["espece"], move, foe["pv"])    # measured at the next decision, once the turn has played out
            if not ran:
                record["echec"] = f"Impossible d'exécuter {action} ; nouvelle question à Jev."
                self.noops.add((sig, action))
        else:
            meant = (self.intents.get(action) or action) if STEP_BY_STEP else action   # the named action this button completes
            if STEP_BY_STEP and action in world.STEPS:
                self.heading = self.leads.get(action, {})
            self.talking_to = f"{state['carte_id']}:{meant.split('_')[1]}" if meant.startswith("parler_") else None
            record["pas"] = self.walk(self.plans.get(action, [action]))
            after = self.game.state()
            self.atlas.record(state["carte"], meant, after["carte"])
            self.barring = None
            if meant.split("_")[0] in ("sortie", "bord") and not after["en_combat"] \
                    and after["carte_id"] == state["carte_id"]:
                # The announced destination was not reached: something bars this way. Saying
                # which way never arrives is a fact; leaving it described as "à 38 pas" is a lie.
                seen = self.barred.setdefault((state["carte_id"], meant), {"essais": 0, "texte": None})
                seen["essais"] += 1
                record["barre"] = seen["essais"]
                if after["texte_affiche"]:
                    self.barring = (state["carte_id"], meant)   # the reason is still being printed
        landed = self.game.state()
        withheld = {k: ("sans effet ici" if (sig, k) in self.noops else "boucle") for k in offered if k not in space}
        self.journal.write("decisions", {
            "n": self.jev["decisions"], "jalon": self.milestone, "source_objectif": self.source, "contexte": context,
            "options": space, "retirees_par_le_harnais": withheld, "cotes": record["cotes"], "choix": action,
            "confiance": record["confiance"], "ms": record["ms"], "tokens": tokens, "boutons": record["pas"],
            "echec": record.get("echec"), "barre": record.get("barre"), "alerte_du_harnais": alert,
            "carte": state["carte"], "position": state["position"], "effet": self.journal.effect(state, landed),
            "apres": {"carte": landed["carte"], "position": landed["position"]}})
        self.recent.append((sig, action))
        if ram.signature(self.game.state()) == sig:
            self.noops.add((sig, action))              # nothing happened: don't offer it here again
        self.history = (self.history + [record])[-400:]
        self.pacing(state, offered)
        return record

    def pacing(self, state: dict, offered: dict[str, str]) -> None:
        """Treading the same few cells is already a hole in the state or in the menu: freeze it now, instead of
        after three Claude calls (hundreds of decisions). Fights, texts and menus legitimately stay in place."""
        trail = self.__dict__.setdefault("trail", [])
        if state["en_combat"] or state["menu_ouvert"] or state["texte_affiche"]:
            return
        trail.append((state["carte"], state["position"]["x"], state["position"]["y"]))
        del trail[:-LOOP_AFTER]
        cells = sorted(set(trail))
        if len(trail) == LOOP_AFTER and len(cells) <= LOOP_CELLS and self.since_progress >= LOOP_AFTER:
            self.open_case("boucle", state, {"cases_pietinees": cells, "decisions_sur_place": LOOP_AFTER, "options_proposees": offered},
                           fingerprint=f"boucle:{self.milestone}:{cells[0]}")
            trail.clear()

    def run(self, steps: int):
        with TypeSafeClient() as client:
            for _ in range(steps):
                yield self.step(client)
