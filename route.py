"""Orientation, not a script.

`carnet.json` is a plain-text travel notebook. Nothing here tests the game: *Jev* judges
which milestone is current (one `choice`, asked only when a fact changes), the map graph
is *learned* from transitions actually lived, and Claude appends lessons when the agent stalls.
"""
from __future__ import annotations
import json
from collections import deque
from pathlib import Path

from typesafe_sdk import Choice

CARNET = Path(__file__).with_name("carnet.json")      # editorial, versioned, never written at runtime
LESSONS = Path(__file__).with_name("lecons.json")     # what Claude learned while playing, runtime


class Carnet:
    def __init__(self, path: Path = CARNET):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8"))
        self.learned = json.loads(LESSONS.read_text(encoding="utf-8")) if LESSONS.exists() else []

    @property
    def milestones(self) -> list[dict]:
        return self.data["jalons"]

    def get(self, milestone_id: str | None) -> dict | None:
        return next((m for m in self.milestones if m["id"] == milestone_id), None)

    def lessons(self, milestone_id: str | None) -> list[str]:
        return [l["texte"] for l in self.learned if l["jalon"] == milestone_id][-4:]

    def learn(self, milestone_id: str | None, text: str) -> None:
        if text and text not in [l["texte"] for l in self.learned]:
            self.learned.append({"jalon": milestone_id, "texte": text[:240]})
            LESSONS.write_text(json.dumps(self.learned, ensure_ascii=False, indent=2), encoding="utf-8")

    def locate(self, client, facts: dict) -> tuple[str, float, int]:
        """Ask Jev where we stand. Returns (milestone id, confidence, input tokens)."""
        response = client.system_one(
            state={"jeu": "Pokémon Bleu", "faits": facts},
            questions={"jalon": Choice(
                instructions=("Voici la progression d'une partie. Les jalons sont dans l'ordre du jeu. "
                              "Quel est le PREMIER jalon qui n'est pas encore accompli ?"),
                criteria={m["id"]: m["texte"] for m in self.milestones})})
        answer = response.choices["jalon"]
        tokens = getattr(getattr(response, "usage", None), "input_tokens", 0) or 0
        return answer.choice, float(answer.confidence), tokens


class Atlas:
    """Map graph learned by living it: (from map, action label) -> map reached.

    An exit *seen* on a map is a fact too: its destination map id sits in the warp table
    even when the door was never taken. Without it the graph stops at what has already been
    walked, and the harness cannot say where the unexplored edge of the world is."""
    def __init__(self):
        self.edges: dict[str, dict[str, str]] = {}      # lived: origin -> {reached: action label}
        self.seen: dict[str, list[str]] = {}            # read on the map: origin -> destinations

    def record(self, origin: str, label: str, reached: str) -> None:
        if origin != reached:
            self.edges.setdefault(origin, {})[reached] = label
            self.see(origin, reached)

    def see(self, origin: str, destination: str) -> None:
        """One of `origin`'s exits leads to `destination`, taken or not."""
        if origin != destination and destination not in self.seen.setdefault(origin, []):
            self.seen[origin].append(destination)

    def neighbours(self, origin: str) -> list[str]:
        lived = list(self.edges.get(origin, {}))
        return lived + [n for n in self.seen.get(origin, []) if n not in lived]

    def _hops(self, origin: str) -> dict[str, str]:
        """Every reachable map -> the neighbouring map to enter first, nearest first."""
        hops, seen, queue = {}, {origin}, deque([(origin, None)])
        while queue:
            here, first = queue.popleft()
            for neighbour in self.neighbours(here):
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                hops[neighbour] = first or neighbour
                queue.append((neighbour, first or neighbour))
        return hops

    def next_hop(self, origin: str, target: str | None) -> str | None:
        """The neighbouring map to enter next on the shortest *known* way to target."""
        if not target or origin == target:
            return None
        return self._hops(origin).get(target)

    def unexplored(self, origin: str, visited) -> list[str]:
        """Maps an exit is known to lead to but that were never entered, nearest first."""
        return [name for name in self._hops(origin) if name not in visited]

    def heading(self, origin: str, target: str | None, visited) -> tuple[str | None, str | None]:
        """(next map to enter, map aimed at): the known way to `target`, or else the
        nearest map we know exists and have never set foot in."""
        hop = self.next_hop(origin, target)
        if hop:
            return hop, target
        hops = self._hops(origin)
        for name in hops:
            if name not in visited:
                return hops[name], name
        return None, None
