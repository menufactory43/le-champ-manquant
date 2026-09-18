"""Game tables read straight from the cartridge, so names come in the ROM's own language.

Offsets were located by content search (see README): the Pound record, the first
type-chart triplets, and the encoded first names of each list.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path

import screen

MOVES, MOVE_NAMES, SPECIES_NAMES, TYPE_CHART = 0x38000, 0xB0000, 0x1C21E, 0x3E489
TYPES = {0: "Normal", 1: "Combat", 2: "Vol", 3: "Poison", 4: "Sol", 5: "Roche", 7: "Insecte",
         8: "Spectre", 0x14: "Feu", 0x15: "Eau", 0x16: "Plante", 0x17: "Électrik",
         0x18: "Psy", 0x19: "Glace", 0x1A: "Dragon"}
ITEMS = {0x01: "MASTER BALL", 0x02: "HYPER BALL", 0x03: "SUPER BALL", 0x04: "POKé BALL",
         0x0A: "PIERRE LUNE", 0x0B: "ANTIDOTE", 0x0C: "ANTI-BRÛLE", 0x0D: "ANTIGEL", 0x0E: "RÉVEIL",
         0x0F: "ANTI-PARA", 0x10: "GUÉRISON", 0x11: "POTION MAX", 0x12: "HYPER POTION",
         0x13: "SUPER POTION", 0x14: "POTION", 0x1D: "CORDE SORTIE", 0x1E: "REPOUSSE",
         0x28: "SUPER BONBON", 0x35: "RAPPEL", 0x46: "COLIS DE CHEN", 0x06: "BICYCLETTE",
         0xC4: "CS01 COUPE", 0xC5: "CS02 VOL", 0xC6: "CS03 SURF", 0xC7: "CS04 FORCE", 0xC8: "CS05 FLASH"}
BALLS, POTIONS = (0x04, 0x03, 0x02, 0x01), (0x14, 0x13, 0x12, 0x11)

_rom = b""


def load(path: str) -> None:
    global _rom
    _rom = Path(path).read_bytes()
    move_names.cache_clear()


def _decode(raw: bytes) -> str:
    return "".join(screen.CHARS.get(b, "") for b in raw).strip()


@lru_cache(maxsize=1)
def move_names() -> list[str]:
    return [_decode(chunk) for chunk in _rom[MOVE_NAMES:MOVE_NAMES + 2400].split(b"\x50")[:165]]


def move(move_id: int) -> dict | None:
    if not 1 <= move_id <= 165:
        return None
    _, _, power, kind, accuracy, pp = _rom[MOVES + 6 * (move_id - 1):MOVES + 6 * move_id]
    return {"nom": move_names()[move_id - 1], "type": TYPES.get(kind, "?"), "type_id": kind,
            "puissance": power, "precision": round(accuracy * 100 / 255), "pp_max": pp}


def species(internal_id: int) -> str:
    if not 1 <= internal_id <= 190:
        return "?"
    start = SPECIES_NAMES + 10 * (internal_id - 1)
    return _decode(_rom[start:start + 10].split(b"\x50")[0])


def effectiveness(move_type: int, defender_types) -> float:
    factor, at = 1.0, TYPE_CHART
    while _rom[at] != 0xFF:
        attacker, defender, multiplier = _rom[at:at + 3]
        if attacker == move_type and defender in set(defender_types):
            factor *= multiplier / 10
        at += 3
    return factor
