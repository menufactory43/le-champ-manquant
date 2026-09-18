"""Locate WRAM addresses empirically: move, diff, keep what moved by exactly one."""
from __future__ import annotations
from game import Game

WRAM = range(0xC000, 0xE000)


def snapshot(g):
    return {a: g.read(a) for a in WRAM}


def diff(before, after, delta=None):
    out = {}
    for a, v in before.items():
        w = after[a]
        if v == w:
            continue
        if delta is None or (w - v) % 256 == delta % 256:
            out[a] = (v, w)
    return out


with Game("pokemon_blue_fr.gb", window=False, speed=0) as g:
    g.pyboy.tick(400)
    for _ in range(240):                     # traverser l'intro
        g.press("a", hold=6, settle=10)
    for _ in range(12):                      # fermer tout dialogue restant
        g.press("b", hold=6, settle=10)

    print("recherche de Y (deux pas vers le bas)")
    b0 = snapshot(g); g.press("down"); a1 = snapshot(g)
    d1 = diff(b0, a1, +1)
    g.press("down"); a2 = snapshot(g)
    d2 = diff(a1, a2, +1)
    y_cands = sorted(set(d1) & set(d2))
    print("  candidats Y :", [hex(a) for a in y_cands][:8])

    print("recherche de X (deux pas vers la droite)")
    b0 = snapshot(g); g.press("right"); a1 = snapshot(g)
    d1 = diff(b0, a1, +1)
    g.press("right"); a2 = snapshot(g)
    d2 = diff(a1, a2, +1)
    x_cands = sorted(set(d1) & set(d2))
    print("  candidats X :", [hex(a) for a in x_cands][:8])
