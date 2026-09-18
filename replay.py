"""Replay a frozen case: does Jev get out of it with the current harness?

    uv run python replay.py stuck/0001-trou [--decisions 40]

Exit code 0 = out, 1 = still stuck (or still crashing). Prints one JSON line.
"""
from __future__ import annotations
import argparse, json, sys, traceback
from pathlib import Path

from typesafe_sdk import TypeSafeClient

import agent as agent_module
from agent import Agent
from game import Game


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("--decisions", type=int, default=40)
    ap.add_argument("--rom", default="pokemon_blue_fr.gb")
    args = ap.parse_args()
    folder = Path(args.case)
    case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    agent_module.STUCK_LOG = folder / "replay_stuck.jsonl"          # never pollute the live logs
    agent_module.CASES = folder / "replay_cases"
    agent_module.Journal = lambda: __import__("journal").Journal(enabled=False)      # a replay is not the run

    with Game(args.rom, window=False, speed=0, save_state=str(folder / "case.state")) as game, TypeSafeClient() as client:
        agent = Agent(game, case["memoire"].get("final_goal", case["objectif"]))
        agent.restore(case["memoire"])
        known, before = set(agent.visited), None
        try:
            while agent.jev["decisions"] - case["memoire"]["jev"]["decisions"] < args.decisions:
                agent.step(client)
                state = game.state()
                facts = (agent.milestone, len(state["badges"]), len(state["equipe"]),
                         tuple(sorted(i["nom"] for i in state["sac"])))
                before = before or (case["jalon"], *facts[1:])
                new_maps = [m for m in agent.visited if m not in known]
                if case["genre"] == "trou" and (facts != before or new_maps):
                    return report(True, agent, f"sorti : jalon {before[0]} → {facts[0]}, nouvelles cartes {new_maps}, faits {facts[1:]}")
        except Exception:
            return report(False, agent, "exception : " + traceback.format_exc()[-600:])
        if case["genre"] == "exception":
            return report(True, agent, f"{args.decisions} décisions sans exception")
        return report(False, agent, f"toujours bloqué après {args.decisions} décisions (jalon {agent.milestone}, carte {game.state()['carte']})")


def report(ok: bool, agent: Agent, why: str) -> int:
    print(json.dumps({"sorti": ok, "raison": why, "derniers_coups":
                      [f"{h['carte']}: {h['bouton']}" for h in agent.history[-10:] if not h["auto"]]}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
