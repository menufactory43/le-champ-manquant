"""jev-pokemon — Jev plays Pokémon Red. You supply your own ROM dump."""
from __future__ import annotations
import argparse, json, statistics, sys
from collections import Counter
from pathlib import Path

from agent import Agent, STUCK_LOG
from game import Game
import ram

DEFAULT_ROM = Path.home() / "jev-pokemon" / "pokemon_red.gb"


def selftest(game) -> None:
    """Prove the RAM map is right before trusting any decision built on it."""
    print("=== contrôle de la carte mémoire ===")
    before = game.state()
    print(f"  état initial : {before['carte']} ({before['position']})")
    moved = False
    for button in ("down", "up", "left", "right"):
        game.press(button)
        after = game.state()
        if after["position"] != before["position"] or after["carte_id"] != before["carte_id"]:
            print(f"  « {button} » → {after['carte']} {after['position']}  ✓ la position bouge")
            moved = True
            break
    if not moved:
        print("  ⚠ aucun mouvement détecté : soit un dialogue est ouvert, "
              "soit les adresses x/y sont fausses pour cette révision de ROM.")
    s = game.state()
    print(f"  équipe {s['equipe']['nombre']} · badges {s['badges']} · "
          f"argent {s['argent']} · combat={s['en_combat']} · texte={s['texte_affiche']}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="jev-pokemon")
    ap.add_argument("goal", nargs="*", default=["Sortir de la maison et explorer le village."])
    ap.add_argument("--rom", default=str(DEFAULT_ROM))
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--speed", type=int, default=2, help="0 = sans limite, 1 = temps réel")
    ap.add_argument("--load", help="charger une sauvegarde d'état")
    ap.add_argument("--save", help="enregistrer l'état à la fin")
    ap.add_argument("--selftest", action="store_true", help="vérifier la carte mémoire puis sortir")
    args = ap.parse_args()

    rom = Path(args.rom)
    if not rom.exists():
        print(f"ROM introuvable : {rom}\n\n"
              "Dépose ton propre dump de Pokémon Rouge à cet emplacement "
              "(ou passe --rom). Je ne peux pas te le fournir.", file=sys.stderr)
        sys.exit(2)

    goal = " ".join(args.goal)
    with Game(str(rom), window=not args.no_window, speed=args.speed,
              save_state=args.load) as game:
        if args.selftest:
            selftest(game); return

        print(f"objectif : {goal}\n")
        agent = Agent(game, goal)
        for i, step in enumerate(agent.run(args.steps), 1):
            flag = " ⟲" if step["bloque"] else ""
            print(f"  {i:>3}. {step['carte'][:22]:<22} {str(step['position']):<18} "
                  f"{step["bouton"]:<10} conf {step['confiance']:.2f}  {step['ms']:>4} ms{flag}",
                  flush=True)
        if args.save:
            game.save(args.save); print(f"\nétat enregistré : {args.save}")

        final = game.state()
        hist = agent.history
        print(f"\n=== {len(hist)} coups ===")
        print(f"arrivée : {final['carte']} {final['position']} · "
              f"équipe {final['equipe']['nombre']} · badges {final['badges']}")
        print(f"confiance médiane : {statistics.median(h['confiance'] for h in hist):.2f} | "
              f"latence médiane : {statistics.median(h['ms'] for h in hist):.0f} ms")
        print(f"boutons : {dict(Counter(h['bouton'] for h in hist).most_common())}")
        print(f"blocages détectés : {agent.stuck_events}"
              + (f" → {STUCK_LOG}" if agent.stuck_events else ""))


if __name__ == "__main__":
    main()
