"""Battle as named intentions. Jev picks `attaque_1` or `capturer`; the harness drives the
menus and checks the cursor after every press (wCurrentMenuItem & co. are reliable)."""
from __future__ import annotations

import data

MAIN_Y, LEFT_X, RIGHT_X = 14, 7, 13          # 2x2 main menu: ATTAQ PKMN / OBJET FUITE
PARTY_X, PARTY_Y = 0, 1


def at_main_menu(state: dict) -> bool:
    c = state["curseur"]
    return bool(state["en_combat"] and state["menu"] and c["y"] == MAIN_Y
                and c["x"] in (LEFT_X, RIGHT_X) and "ATTAQ" in " ".join(state["menu"]["options"]))


MOVES_X, MOVES_Y = 5, 12                     # the move list, once ATTAQ is opened
SPECIAL_TYPES = {0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A}   # Gen 1: the move's type decides physical or special


def at_move_list(state: dict) -> bool:
    """The move list is open. Its box is overlapped by the TYPE/ box, so the screen reader
    cannot see it as a menu; the cursor registers can."""
    c = state["curseur"]
    # the cursor registers keep their value from the previous battle: the screen must agree
    return bool(state["en_combat"] and state.get("liste_attaques") and c["x"] == MOVES_X and c["y"] == MOVES_Y)


def at_party_screen(state: dict) -> bool:
    c = state["curseur"]
    return bool(state["menu"] and c["x"] == PARTY_X and c["y"] == PARTY_Y and state["equipe"])


def _effect(factor: float) -> str:
    return {0.0: "SANS EFFET", 0.25: "très peu efficace", 0.5: "peu efficace", 1.0: "efficacité normale",
            2.0: "SUPER EFFICACE", 4.0: "SUPER EFFICACE ×4"}.get(factor, f"×{factor}")


def _hit(move: dict, mine: dict, foe: dict) -> str:
    """Ce que le coup retire vraiment, en PV : la formule de dégâts de la 1ʳᵉ génération
    appliquée aux statistiques que le jeu utilise à cet instant.

    Le champ qui manquait. « peu efficace » disait exactement la même chose de GRIFFE et de
    FLAMMECHE contre le RACAILLOU de l'arène d'Argenta : l'une retire 2 PV sur 33, l'autre 9
    (Défense 30 contre Spécial 14, et le bonus de type). Sans le chiffre, une attaque sur
    deux est jouée à l'aveugle et un combat d'arène ne finit jamais."""
    special = move["type_id"] in SPECIAL_TYPES
    attack = mine["special"] if special else mine["attaque"]
    defense = max(1, foe["special"] if special else foe["defense"])
    factor = data.effectiveness(move["type_id"], foe["type_ids"])
    stab = 1.5 if move["type_id"] in mine["type_ids"] else 1.0
    base = int((2 * mine["niveau"] / 5 + 2) * move["puissance"] * attack // defense // 50) + 2
    high = int(int(base * stab) * factor)          # le jeu tire ensuite entre 85 % et 100 %
    low = high * 217 // 255
    if high <= 0:
        return "aucun dégât"
    hits = -(-foe["pv"] // max(1, low))
    return (f"retire environ {low} à {high} PV par coup sur les {foe['pv']} qui restent à "
            f"{foe['espece']}, soit {hits} coup{'s' if hits > 1 else ''}")


def options(state: dict, seen: dict | None = None) -> dict[str, str]:
    """Semantic choices available from the main battle menu."""
    fight, space = state["combat"], {}
    foe, mine = fight["adversaire"], fight["actif"]
    for move in mine["attaques"]:
        if move["pp"] == 0:
            continue
        if move["puissance"]:
            special = move["type_id"] in SPECIAL_TYPES
            detail = (f"puissance {move['puissance']}, attaque {'spéciale' if special else 'physique'}, "
                      f"{_effect(data.effectiveness(move['type_id'], foe['type_ids']))} contre {foe['espece']}, "
                      f"{_hit(move, mine, foe)}")
        else:
            detail = "attaque de statut, n'inflige aucun dégât"
        last = (seen or {}).get(f"{foe['espece']}:{move['nom']}")
        if last is not None:                     # what it actually did, the last time it was tried on this foe
            share = round(100 * last / max(1, foe["pv_max"]))
            detail += f", au dernier essai : {last} PV retirés à {foe['espece']}, soit {share} % de ses PV"
        space[f"attaque_{move['case']}"] = (f"Utiliser {move['nom']} ({move['type']}, {detail}, "
                                            f"précision {move['precision']} %, PP {move['pp']}/{move['pp_max']}).")
    for mon in state["equipe"]:
        if mon["pv"] > 0 and mon["espece"] != mine["espece"]:
            space[f"changer_{mon['n']}"] = (f"Envoyer {mon['espece']} ({'/'.join(mon['types'])}, niveau {mon['niveau']}, "
                                            f"{mon['pv']}/{mon['pv_max']} PV) à la place.")
    bag = {item["id"]: item for item in state["sac"]}
    potion = next((bag[i] for i in data.POTIONS if i in bag), None)
    if potion and mine["pv"] < mine["pv_max"] * 0.5:
        space["potion"] = f"Utiliser {potion['nom']} sur {mine['espece']} ({mine['pv']}/{mine['pv_max']} PV)."
    if state["type_combat"] == "sauvage":
        ball = next((bag[i] for i in data.BALLS if i in bag), None)
        if ball and len(state["equipe"]) < 6:
            space["capturer"] = (f"Lancer {ball['nom']} (×{ball['quantite']}) sur {foe['espece']} niveau {foe['niveau']} "
                                 f"({foe['pv']}/{foe['pv_max']} PV ; plus ses PV sont bas, plus ça marche).")
        space["fuite"] = "Fuir ce combat sauvage (aucun gain d'expérience)."
    return space


def move_options(state: dict, seen: dict | None = None) -> dict[str, str]:
    """Same attacks as the main menu, when the move list is already open."""
    space = {k: v for k, v in options(state, seen).items() if k.startswith("attaque_")}
    space["b"] = "Refermer la liste des attaques et revenir au menu de combat (changer, objet, fuite)."
    return space


def party_options(state: dict) -> dict[str, str]:
    space = {f"choisir_{m['n']}": f"Choisir {m['espece']} (niveau {m['niveau']}, {m['pv']}/{m['pv_max']} PV)."
             for m in state["equipe"] if m["pv"] > 0}
    space["b"] = "Annuler et revenir en arrière."
    return space


class Driver:
    def __init__(self, game):
        self.game = game

    def _cursor(self) -> dict:
        return self.game.state()["curseur"]

    def _row_to(self, target: int, *, limit: int = 12, scrolled: bool = False) -> bool:
        for _ in range(limit):
            c = self._cursor()
            now = c["ligne"] + (c["defilement"] if scrolled else 0)
            if now == target:
                return True
            self.game.press("down" if now < target else "up", settle=10)
        return False

    def _main(self, row: int, right: bool) -> bool:
        for _ in range(4):
            c = self._cursor()
            if c["y"] == MAIN_Y and c["ligne"] == row and (c["x"] == RIGHT_X) == right:
                self.game.press("a"); self.game.tick(30)
                return True
            if c["ligne"] != row:
                self.game.press("down" if row else "up", settle=10)
            else:
                self.game.press("right" if right else "left", settle=10)
        return False

    def _pick_party(self, index: int) -> bool:
        if not self._row_to(index - 1):
            return False
        self.game.press("a"); self.game.tick(30)
        if self.game.state()["menu"]:                    # ORDRE / STATS / RETOUR: first line acts
            self.game.press("a")
        return True

    def run(self, action: str, state: dict) -> bool:
        """Carry out a semantic action. False means the menus did not behave; ask again."""
        kind, _, arg = action.partition("_")
        if kind == "choisir":
            return self._pick_party(int(arg))
        if kind == "attaque" and at_move_list(state):
            return self._row_to(int(arg), limit=6) and self._press_a()
        if kind == "attaque":
            return self._main(0, False) and self._row_to(int(arg), limit=6) and self._press_a()
        if kind == "fuite":
            return self._main(1, True)
        if kind == "changer":
            return self._main(0, True) and self._tick(40) and self._pick_party(int(arg))
        if kind in ("capturer", "potion"):
            wanted = data.BALLS if kind == "capturer" else data.POTIONS
            ids = [item["id"] for item in state["sac"]]
            slot = next((ids.index(i) for i in wanted if i in ids), None)
            if slot is None or not self._main(1, False):
                return False
            if not (self._row_to(slot, limit=24, scrolled=True) and self._press_a()):
                return False
            if kind == "potion":
                self.game.tick(40)
                active = next((m["n"] for m in state["equipe"]
                               if m["espece"] == state["combat"]["actif"]["espece"]), 1)
                return self._row_to(active - 1) and self._press_a()
            return True
        return False

    def _press_a(self) -> bool:
        self.game.press("a"); return True

    def _tick(self, frames: int) -> bool:
        self.game.tick(frames); return True
