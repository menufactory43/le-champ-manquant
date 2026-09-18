"""Geometry the app owns: walkable grid, warps, NPCs, pathfinding.

Jev never sees coordinates to aim at; it picks a named destination and the
harness walks there. Addresses follow pret/pokered (the reader applies the shift).
"""
from __future__ import annotations
from collections import deque

OVERWORLD_MAP = 0xC6E8          # block ids, 3-block border on every side
MAP_HEIGHT, MAP_WIDTH = 0xD368, 0xD369
CONNECTIONS = 0xD370            # bit3 north, bit2 south, bit1 west, bit0 east
TILESET_BANK, TILESET_BLOCKS, TILESET_COLLISION = 0xD52B, 0xD52C, 0xD530
WARP_COUNT, WARP_ENTRIES = 0xD3AE, 0xD3AF      # y, x, warp id, destination map
LAST_MAP = 0xD365
SPRITES_1, SPRITES_2 = 0xC100, 0xC200

STEPS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
EDGES = {"nord": (8, "up"), "sud": (4, "down"), "ouest": (2, "left"), "est": (1, "right")}


def _u16le(read, address):
    return read(address) | (read(address + 1) << 8)


GRASS_TILE = 0xD535


def cell_tiles(read, rom) -> list[list[int]]:
    """The tile the game tests for each map cell: bottom-left of its 2x2 tiles."""
    width, height = read(MAP_WIDTH), read(MAP_HEIGHT)
    bank, blocks = read(TILESET_BANK), _u16le(read, TILESET_BLOCKS)
    tiles = [[0] * (width * 2) for _ in range(height * 2)]
    for by in range(height):
        for bx in range(width):
            block = read(OVERWORLD_MAP + (by + 3) * (width + 6) + 3 + bx)
            for cy in range(2):
                for cx in range(2):
                    tiles[by * 2 + cy][bx * 2 + cx] = rom(bank, blocks + block * 16 + (cy * 2 + 1) * 4 + cx * 2)
    return tiles


def walkable_grid(read, rom) -> list[list[bool]]:
    """One bool per map cell. `rom(bank, address)` reads the cartridge."""
    passable, address = set(), _u16le(read, TILESET_COLLISION)
    while rom(0, address) != 0xFF and len(passable) < 64:
        passable.add(rom(0, address)); address += 1
    return [[tile in passable for tile in row] for row in cell_tiles(read, rom)]


def grass_cells(read, rom) -> list[tuple[int, int]]:
    grass = read(GRASS_TILE)
    if grass == 0xFF:
        return []
    return [(x, y) for y, row in enumerate(cell_tiles(read, rom)) for x, tile in enumerate(row) if tile == grass]


def warps(read) -> list[dict]:
    out = []
    for i in range(min(read(WARP_COUNT), 32)):
        y, x, _, dest = (read(WARP_ENTRIES + 4 * i + k) for k in range(4))
        out.append({"x": x, "y": y, "vers": read(LAST_MAP) if dest == 0xFF else dest})
    return out


def loaded(read, x: int, y: int) -> bool:
    """Do the position and the loaded map agree?

    Crossing a door, the map id changes a few frames before the map itself: read in
    between, the harness offers the exits of the new map from the square of the old one.
    Vérifié sur stuck/0005 (Argenta → Arène) : id 54 avec (16, 17) alors que la carte
    chargée ne fait que 10×14 cases."""
    return 0 <= x < read(MAP_WIDTH) * 2 and 0 <= y < read(MAP_HEIGHT) * 2


def talk_cells(grid, x: int, y: int) -> dict[tuple[int, int], str]:
    """Cells from which (x, y) can be spoken to, with the button that faces it.
    Nurses and clerks stand behind a counter: two cells away across a blocked one."""
    h, w, out = len(grid), len(grid[0]), {}
    opposite = {"up": "down", "down": "up", "left": "right", "right": "left"}
    for button, (dx, dy) in STEPS.items():
        near, far = (x + dx, y + dy), (x + 2 * dx, y + 2 * dy)
        inside = lambda c: 0 <= c[0] < w and 0 <= c[1] < h
        if inside(near) and grid[near[1]][near[0]]:
            out[near] = opposite[button]
        elif inside(far) and grid[far[1]][far[0]]:
            out[far] = opposite[button]
    return out


MISSABLE_FLAGS, MISSABLE_LIST = 0xD5A6, 0xD5CE     # (sprite index, flag bit) pairs, 0xFF-terminated


def hidden_sprites(read) -> set[int]:
    """Sprites the game has removed from the map (taken item balls, departed characters)."""
    hidden = set()
    for k in range(17):
        sprite, bit = read(MISSABLE_LIST + 2 * k), read(MISSABLE_LIST + 2 * k + 1)
        if sprite == 0xFF:
            break
        if read(MISSABLE_FLAGS + bit // 8) & (1 << (bit % 8)):
            hidden.add(sprite)
    return hidden


def npcs(read) -> list[dict]:
    """Every character on the map, on screen or not. (Image index 0xFF only means off screen.)"""
    gone, out = hidden_sprites(read), []
    for i in range(1, 16):
        if read(SPRITES_1 + 16 * i) == 0 or i in gone:
            continue
        out.append({"n": i, "x": read(SPRITES_2 + 16 * i + 5) - 4,
                    "y": read(SPRITES_2 + 16 * i + 4) - 4})
    return out


CONNECTED_MAP = {"nord": 0xD371, "sud": 0xD37C, "ouest": 0xD387, "est": 0xD392}


def connections(read) -> dict[str, int]:
    """Which map lies beyond each open edge."""
    flags = read(CONNECTIONS)
    return {side: read(CONNECTED_MAP[side]) for side, (bit, _) in EDGES.items() if flags & bit}


def path(grid, start, goals, blocked=()) -> list[str] | None:
    """Shortest button sequence from start to any goal cell."""
    goals, blocked = set(goals), set(blocked)
    if start in goals:
        return []
    seen, queue = {start}, deque([(start, [])])
    while queue:
        (x, y), steps = queue.popleft()
        for button, (dx, dy) in STEPS.items():
            nxt = (x + dx, y + dy)
            if nxt in seen or nxt in blocked:
                continue
            if not (0 <= nxt[1] < len(grid) and 0 <= nxt[0] < len(grid[0])) or not grid[nxt[1]][nxt[0]]:
                continue
            if nxt in goals:
                return steps + [button]
            seen.add(nxt); queue.append((nxt, steps + [button]))
    return None


def edge_cells(grid, side) -> list[tuple[int, int]]:
    h, w = len(grid), len(grid[0])
    cells = {"nord": [(x, 0) for x in range(w)], "sud": [(x, h - 1) for x in range(w)],
             "ouest": [(0, y) for y in range(h)], "est": [(w - 1, y) for y in range(h)]}[side]
    return [(x, y) for x, y in cells if grid[y][x]]


def off_map_direction(grid, x, y) -> str | None:
    """Door mats sit on the map edge: you leave by walking off it."""
    h, w = len(grid), len(grid[0])
    return ("down" if y == h - 1 else "up" if y == 0 else
            "left" if x == 0 else "right" if x == w - 1 else None)
