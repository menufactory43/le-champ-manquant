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


def at_party_screen(state: dict) -> bool:
    c = state["curseur"]
    return bool(state["menu"] and c["x"] == PARTY_X and c["y"] == PARTY_Y and state["equipe"])


def _effect(factor: float) -> str:
    return {0.0: "SANS EFFET", 0.25: "très peu efficace", 0.5: "peu efficace", 1.0: "efficacité normale",
            2.0: "SUPER EFFICACE", 4.0: "SUPER EFFICACE ×4"}.get(factor, f"×{factor}")


def options(state: dict) -> dict[str, str]:
    """Semantic choices available from the main battle menu."""
    fight, space = state["combat"], {}
    foe, mine = fight["adversaire"], fight["actif"]
    for move in mine["attaques"]:
        if move["pp"] == 0:
            continue
        if move["puissance"]:
            detail = (f"puissance {move['puissance']}, "
                      f"{_effect(data.effectiveness(move['type_id'], foe['type_ids']))} contre {foe['espece']}")
        else:
            detail = "attaque de statut, n'inflige aucun dégât"
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
