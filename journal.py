"""The construction log: everything a better harness will be built from.

This run is allowed help (Claude, the repairer). The point of logging is to list, precisely,
every place that help was needed — so the next run can do without it. One JSON line per fact:

    journal/decisions.jsonl   every Jev decision: exact context sent, options offered (and which
                              ones the harness withheld, and why), odds, pick, and what it changed
    journal/textes.jsonl      every text the game showed (hints live here: « Bats PIERRE... »)
    journal/reperages.jsonl   every time Jev judged which notebook milestone we are in
    journal/claude.jsonl      every supervisor call: what it was told, what it said, and how many
                              decisions later something finally progressed — a dependency to remove
    journal/jalons.jsonl      milestone transitions with the bill so far (the benchmark to beat)
    journal/bancs/<jalon>.*   emulator state + memory at each milestone start: a test bench for
                              the no-LLM run and for the random baseline
"""
from __future__ import annotations
import json, time
from pathlib import Path

ROOT = Path(__file__).with_name("journal")


class Journal:
    def __init__(self, root: Path = ROOT, enabled: bool = True):
        self.root, self.enabled = root, enabled
        self.pending_claude: dict | None = None
        if enabled:
            (root / "bancs").mkdir(parents=True, exist_ok=True)

    def write(self, name: str, fact: dict) -> None:
        if self.enabled:
            with (self.root / f"{name}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"t": round(time.time(), 1), **fact}, ensure_ascii=False) + "\n")

    @staticmethod
    def effect(before: dict, after: dict) -> list[str]:
        """What the decision changed, in words a report can count."""
        out = []
        if after["carte_id"] != before["carte_id"]:
            out.append("carte")
        elif after["position"] != before["position"]:
            out.append("deplacement")
        if after["en_combat"] and not before["en_combat"]:
            out.append("combat_debut")
        if before["en_combat"] and not after["en_combat"]:
            out.append("combat_fin")
        if after["texte_affiche"] and not before["texte_affiche"]:
            out.append("texte")
        if after["menu_ouvert"] != before["menu_ouvert"]:
            out.append("menu")
        if sorted(i["nom"] for i in after["sac"]) != sorted(i["nom"] for i in before["sac"]):
            out.append("objet")
        if len(after["equipe"]) != len(before["equipe"]):
            out.append("equipe")
        if sum(m["niveau"] for m in after["equipe"]) > sum(m["niveau"] for m in before["equipe"]):
            out.append("niveau")
        if len(after["badges"]) > len(before["badges"]):
            out.append("badge")
        if before["combat"] and after["combat"] and after["combat"]["adversaire"]["pv"] < before["combat"]["adversaire"]["pv"]:
            out.append("degats")
        return out or ["rien"]

    def claude_asked(self, decision: int, context: dict) -> None:
        self.write("claude", {"moment": "question", "decision": decision, "contexte": context})

    def claude_answered(self, decision: int, milestone: str | None, answer: dict) -> None:
        self.pending_claude = {"decision": decision, "jalon": milestone, "reponse": answer}
        self.write("claude", {"moment": "reponse", "decision": decision, "jalon": milestone, "reponse": answer})

    def progressed(self, decision: int, what: str) -> None:
        """Close the open Claude intervention: how long until anything moved?"""
        if self.pending_claude:
            self.write("claude", {"moment": "effet", **self.pending_claude,
                                  "decisions_avant_progres": decision - self.pending_claude["decision"],
                                  "progres": what})
            self.pending_claude = None

    def bench(self, milestone: str, game, memory: dict) -> None:
        if self.enabled and not (self.root / "bancs" / f"{milestone}.state").exists():
            game.save(str(self.root / "bancs" / f"{milestone}.state"))
            (self.root / "bancs" / f"{milestone}.json").write_text(json.dumps(memory, ensure_ascii=False), encoding="utf-8")
