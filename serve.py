"""Live view: the game, Jev's odds for every option, the log. Stdlib only.

    uv run python serve.py "Devenir Maître Pokémon" --rom pokemon_blue_fr.gb --load start.state
"""
from __future__ import annotations
import argparse, io, json, queue, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from typesafe_sdk import TypeSafeClient

import world
from agent import TRANSIENT, Agent, fault, map_name
from game import Game
from supervisor import Supervisor
import relay

PAGE = Path(__file__).with_name("ui.html")


class Hub:
    """Latest frame + event fan-out to every connected browser."""
    def __init__(self):
        self.frame, self.frame_ready = b"", threading.Condition()
        self.raw_frame = None                       # last PIL image, for the public relay
        self.clients: list[queue.Queue] = []
        self.backlog: list[str] = []
        self.paused = False
        self.new_goal: str | None = None
        self.speed = 1                              # 1 real time, 3 fast, 0 unlimited

    def push_frame(self, image) -> None:
        self.raw_frame = image.copy()
        buffer = io.BytesIO()
        image.convert("RGB").resize((480, 432), Image.NEAREST).save(buffer, "JPEG", quality=92)
        with self.frame_ready:
            self.frame = buffer.getvalue()
            self.frame_ready.notify_all()

    def publish(self, event: dict) -> None:
        event.setdefault("t", round(time.time()))            # the journal shows when it happened, not when it was read
        line = json.dumps(event, ensure_ascii=False)
        self.backlog = (self.backlog + [line])[-150:]
        for client in list(self.clients):
            client.put(line)


def changelog() -> dict:
    path = Path(__file__).with_name("changelog.jsonl")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return {"n": len(lines), "dernier": json.loads(lines[-1])["resume"] if lines else None}


def snapshot(agent: Agent, game: Game, record: dict, step: int) -> dict:
    state = agent.settle()                                   # let a map transition finish
    jev, asked = agent.jev, max(1, agent.jev["decisions"])
    return {
        "n": step, "coup": record, "etat": state, "objectif": agent.goal, "objectif_final": agent.final_goal,
        "zone": agent.next_area, "source": agent.source, "jalon": agent.milestone,
        "lecons": agent.carnet.lessons(agent.milestone),
        "raison": agent.subgoals[-1]["raison"] if agent.subgoals and agent.source == "Claude" else "",
        "jev": {**jev, "latence": round(jev["ms_total"] / asked), "blocages": agent.stuck_events,
                "taux_blocage": round(100 * agent.stuck_events / asked, 1)},
        "claude": ({**agent.supervisor.stats, "occupe": agent.supervisor.busy,
                    "sous_objectifs": len(agent.subgoals)} if agent.supervisor else None),
        "joue_s": round(agent.played_s), "cartes": len(agent.visited), "correctifs": changelog(),
        "hors_ligne": agent.offline,
    }


def checkpoint(game: Game, agent: Agent, state: dict, folder: Path, keep: int = 40) -> None:
    """Timestamped save + memory, so a bad night can be rolled back to any point."""
    folder.mkdir(exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}_{len(state['badges'])}b_{state['carte_id']:03d}"
    game.save(str(folder / f"{name}.state"))
    (folder / f"{name}.json").write_text(json.dumps(agent.dump(), ensure_ascii=False))
    for old in sorted(folder.glob("*.state"))[:-keep]:
        old.unlink(); old.with_suffix(".json").unlink(missing_ok=True)


def play(hub: Hub, args) -> None:
    memory = Path(args.autosave).with_suffix(".json")
    resume = Path(args.autosave).exists() and not args.fresh
    with Game(args.rom, window=False, speed=hub.speed, save_state=args.autosave if resume else args.load) as game:
        game.on_frame = hub.push_frame
        supervisor = None if args.superviseur == "aucun" else Supervisor(args.superviseur)
        agent = Agent(game, args.goal, supervisor=supervisor, final_goal=args.goal)
        if resume and memory.exists():
            agent.restore(json.loads(memory.read_text()))
        speed, saved_at, milestone = hub.speed, time.time(), None
        events = Path(args.autosave).with_name("events.jsonl").open("a", encoding="utf-8")
        with TypeSafeClient() as client:
            step, failures, last_fault, offline = 0, 0, None, 0
            while True:
                if hub.speed != speed:
                    speed = hub.speed; game.pyboy.set_emulation_speed(speed)
                if hub.new_goal:
                    agent.force_goal(hub.new_goal); hub.new_goal = None
                if hub.paused:
                    game.tick(10); continue
                try:
                    record = agent.step(client, lambda r: hub.publish({"decision": r, "objectif": agent.goal}))
                except TRANSIENT as error:                 # the link to Jev dropped: wait it out, it is not a bug
                    offline += 1
                    pause = min(5 * offline, 30)           # short enough to keep the buttons answering
                    hub.publish({"erreur": f"Jev injoignable depuis {offline} essai(s) "
                                           f"({type(error).__name__}), reprise dans {pause} s", "hors_ligne": True})
                    game.tick(pause * 60)                  # ticking, not sleeping: the live view stays alive
                    continue
                except Exception as error:                 # unexpected: say so, keep playing
                    panne = fault(error)                   # the same fault keeps its name when its wording changes
                    hub.publish({"erreur": repr(error), "panne": panne}); time.sleep(2)
                    failures = failures + 1 if panne == last_fault else 1
                    last_fault = panne
                    if failures == 5:                      # not a hiccup: a bug. Freeze it for the repairer.
                        agent.open_case("exception", game.state(),
                                        {"panne": panne, "trace": traceback.format_exc()}, fingerprint=panne)
                    continue
                failures, offline = 0, 0
                step += 1
                shot = snapshot(agent, game, record, step)
                hub.publish(shot)
                events.write(json.dumps({"t": round(time.time()), "n": step, **{k: record.get(k) for k in
                             ("carte", "position", "bouton", "auto", "confiance", "ms", "tokens", "bloque", "echec")},
                             "objectif": agent.goal}, ensure_ascii=False) + "\n"); events.flush()
                state = shot["etat"]
                if step % 10 == 0 and not state["en_combat"]:
                    game.save(args.autosave)
                    memory.write_text(json.dumps(agent.dump(), ensure_ascii=False))
                now_milestone = (len(state["badges"]), len(state["equipe"]))
                if not state["en_combat"] and (time.time() - saved_at > args.checkpoint * 60 or now_milestone != milestone):
                    checkpoint(game, agent, state, Path(args.autosave).with_name("saves"))
                    saved_at, milestone = time.time(), now_milestone


def handler(hub: Hub):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"                 # keep-alive for the frame loop
        def log_message(self, *_): pass

        def do_GET(self):
            if self.path == "/":
                body = PAGE.read_bytes()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/frame"):
                with hub.frame_ready:
                    hub.frame_ready.wait(0.5)               # long-poll: answer with the next frame
                    frame = hub.frame
                self.send_response(200); self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(frame))); self.end_headers()
                self.wfile.write(frame)
            elif self.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close"); self.end_headers()
                sent = None
                try:
                    while True:
                        with hub.frame_ready:
                            hub.frame_ready.wait(1)
                            frame = hub.frame
                        if frame is sent:
                            continue                     # only ever send the newest frame
                        sent = frame
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                except OSError:
                    pass
            elif self.path == "/events":
                self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close"); self.end_headers()
                mine: queue.Queue = queue.Queue()
                for line in hub.backlog:
                    mine.put(line)
                hub.clients.append(mine)
                try:
                    while True:
                        try:
                            self.wfile.write(f"data: {mine.get(timeout=15)}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except OSError:
                    hub.clients.remove(mine)
            else:
                self.send_error(404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            if self.path == "/goal" and body.strip():
                hub.new_goal = body.strip()[:300]
            elif self.path == "/pause":
                hub.paused = not hub.paused
            elif self.path == "/speed":
                hub.speed = {1: 3, 3: 0, 0: 1}[hub.speed]
            self.send_response(204); self.send_header("Content-Length", "0"); self.end_headers()
    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(prog="jev-pokemon-live")
    ap.add_argument("goal", nargs="?", default="Battre la Ligue Pokémon le plus vite possible.")
    ap.add_argument("--checkpoint", type=int, default=10, help="minutes entre deux sauvegardes horodatées dans saves/")
    ap.add_argument("--superviseur", default="sonnet", help="modèle Claude via le CLI (haiku, sonnet…) ou « aucun »")
    ap.add_argument("--fresh", action="store_true", help="ignorer live.state et repartir de --load")
    ap.add_argument("--rom", default="pokemon_blue_fr.gb")
    ap.add_argument("--load", default="start.state")
    ap.add_argument("--autosave", default="live.state")
    ap.add_argument("--speed", type=int, default=1, help="1 = temps réel, 0 = sans limite")
    ap.add_argument("--port", type=int, default=8151)
    ap.add_argument("--host", default="127.0.0.1", help="adresse d'écoute (0.0.0.0 ou l'adresse Tailscale sur la machine de jeu)")
    args = ap.parse_args()

    hub = Hub()
    threading.Thread(target=play, args=(hub, args), daemon=True).start()
    print("relais public :", "actif" if relay.start(hub) else "désactivé (RELAY_URL / RELAY_SECRET absents)")
    print(f"http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), handler(hub)).serve_forever()


if __name__ == "__main__":
    main()
