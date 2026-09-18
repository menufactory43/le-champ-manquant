"""Traverse the intro once and save the state, so runs start in the bedroom."""
from game import Game
import ram, json

with Game("pokemon_blue_fr.gb", window=False, speed=0) as g:
    g.pyboy.tick(400)
    for _ in range(240): g.press("a", hold=6, settle=10)
    for _ in range(12):  g.press("b", hold=6, settle=10)
    g.shift = ram.detect_shift(g.read)
    print("décalage détecté :", g.shift)
    print("état :", json.dumps(g.state(), ensure_ascii=False))
    g.save("start.state")
    g.pyboy.screen.image.save("start.png")
    print("sauvegarde écrite : start.state")
