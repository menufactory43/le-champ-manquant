"""Read the UI straight from the tilemap copy in WRAM (wTileMap, 20x18).

Flags like wMaxMenuItem are never cleared when a menu closes, so they can't say
"a menu is open". The box border tiles can, in any localisation.
"""
from __future__ import annotations

TILEMAP, COLS, ROWS = 0xC3A0, 20, 18
TOP_LEFT, HORIZONTAL, TOP_RIGHT, VERTICAL, BOTTOM_LEFT, BOTTOM_RIGHT = 0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E
CURSOR, IDLE_CURSOR, MORE, TIMES = 0xED, 0xEC, 0xEE, 0xF1

CHARS = {0x7F: " ", 0xE0: "'", 0xE3: "-", 0xE6: "?", 0xE7: "!", 0xE8: ".", 0xF4: ",",
         0x9A: "(", 0x9B: ")", 0x9C: ":", 0x9D: ";", 0xF3: "/", 0xF0: "¥", 0xEF: "♂", 0xF5: "♀",
         CURSOR: "▶", IDLE_CURSOR: "▷", MORE: "▼", TIMES: "×"}
CHARS.update({0x80 + i: chr(ord("A") + i) for i in range(26)})
CHARS.update({0xA0 + i: chr(ord("a") + i) for i in range(26)})
CHARS.update({0xF6 + i: str(i) for i in range(10)})
CHARS.update(zip(range(0xBA, 0xCD), "àèéùßçÄÖÜäöüëïâôûêî"))   # FR/DE charset


def grid(read) -> list[list[int]]:
    return [[read(TILEMAP + r * COLS + c) for c in range(COLS)] for r in range(ROWS)]


def boxes(tiles) -> list[tuple[int, int, int, int]]:
    """(top, left, bottom, right) of every bordered box on screen."""
    found = []
    for top in range(ROWS):
        for left in range(COLS):
            if tiles[top][left] != TOP_LEFT:
                continue
            right = next((c for c in range(left + 1, COLS) if tiles[top][c] == TOP_RIGHT), None)
            bottom = next((r for r in range(top + 1, ROWS) if tiles[r][left] == BOTTOM_LEFT), None)
            if right is not None and bottom is not None:
                found.append((top, left, bottom, right))
    return found


def _text(row) -> str:
    return "".join(CHARS.get(t, "") for t in row).strip()


def read_ui(read) -> dict:
    tiles = grid(read)
    dialogue, menus = None, []
    for top, left, bottom, right in boxes(tiles):
        rows = [tiles[r][left + 1:right] for r in range(top + 1, bottom)]
        lines = [t for t in (_text(row) for row in rows) if t]
        if any(CURSOR in row for row in rows):
            options = [line.lstrip("▶▷ ") for line in lines]
            current = next((i for i, line in enumerate(lines) if line.startswith("▶")), 0)
            menus.append({"options": options, "ligne": current, "surligne": options[current]})
        elif left == 0 and right == COLS - 1 and top >= 12:
            dialogue = " ".join(lines).replace("▼", "").strip()
        # other boxes (HUD, money) carry no decision
    if not menus:
        menus = _loose_menu(tiles)
    # the move list draws a « TYPE/ » box over its own corner, so it is never seen as a menu above
    move_list = any("TYPE/" in _text(row) for row in tiles[8:11])
    return {"dialogue": dialogue, "menu": menus[-1] if menus else None, "liste_attaques": move_list,
            "quantite": None if menus else _quantity(tiles)}


def _quantity(tiles) -> dict | None:
    """The « combien ? » widget: a small box showing « ×01 » and, when buying, the total price.

    It draws no cursor and no dialogue box, so without this the harness believed the game was
    back in the overworld and offered to walk — the buttons then changed the quantity instead.
    The line above it keeps the chosen item marked with the idle cursor ▷."""
    digits = range(0xF6, 0x100)
    for row_index, row in enumerate(tiles):
        if TIMES not in row:
            continue
        at = row.index(TIMES)
        if at == 0 or row[at - 1] != VERTICAL or not all(t in digits for t in row[at + 1:at + 3]):
            continue                                   # a map tile that happens to be 0xF1, not the widget
        text = "".join(CHARS.get(t, " ") for t in row[at + 1:])
        numbers = [int(n) for n in "".join(c if c.isdigit() else " " for c in text).split()]
        if not numbers:
            continue
        chosen = [_text(r).lstrip("▷ ") for r in tiles[:row_index] if IDLE_CURSOR in r]
        return {"nombre": numbers[0], "prix": numbers[1] if len(numbers) > 1 else None,
                "objet": chosen[-1] if chosen else None}
    return None


def _loose_menu(tiles) -> list[dict]:
    """Full-screen lists (party, some shops) draw a cursor without a box around it."""
    spots = [(r, c) for r in range(ROWS) for c in range(COLS) if tiles[r][c] == CURSOR]
    if not spots:
        return []
    row, col = spots[0]
    lines = [(r, _text(tiles[r][col + 1:])) for r in range(ROWS) if tiles[r][col] in (CURSOR, 0x7F, 0xEC)]
    lines = [(r, t) for r, t in lines if t and r < 12]
    options = [t for _, t in lines]
    current = next((i for i, (r, _) in enumerate(lines) if r == row), 0)
    return [{"options": options, "ligne": current, "surligne": options[current]}] if options else []
