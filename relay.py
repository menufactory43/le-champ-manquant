"""Push the live game to the public relay — outbound only, so the game host is never exposed.

Enabled when RELAY_URL and RELAY_SECRET are set (in .env). Sends nothing but a heartbeat
while nobody watches, so the relay can hibernate.
"""
from __future__ import annotations
import asyncio, io, json, os, queue, threading, time

from PIL import Image

FPS = 10


def png(image) -> bytes:
    """Native 160x144, 4 greys: about 2 KB a frame instead of 35 KB of JPEG."""
    buffer = io.BytesIO()
    image.convert("L").quantize(4).save(buffer, "PNG", optimize=False)
    return buffer.getvalue()


def start(hub) -> bool:
    url, secret = os.environ.get("RELAY_URL"), os.environ.get("RELAY_SECRET")
    if not (url and secret):
        return False
    threading.Thread(target=lambda: asyncio.run(_forever(hub, url, secret)), daemon=True).start()
    return True


async def _forever(hub, url: str, secret: str) -> None:
    import websockets
    wait = 2
    while True:
        try:
            async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {secret}"},
                                          ping_interval=30, max_size=2 ** 20) as ws:
                wait = 2
                await _session(hub, ws)
        except Exception as error:                        # relay down or network cut: the game must not care
            print(f"relais : {error!r}, nouvel essai dans {wait} s", flush=True)
        await asyncio.sleep(wait)
        wait = min(wait * 2, 120)


async def _session(hub, ws) -> None:
    state = {"viewers": 0}
    mine: queue.Queue = queue.Queue()

    async def listen():
        async for raw in ws:
            note = json.loads(raw)
            if "spectateurs" in note:
                was, state["viewers"] = state["viewers"], note["spectateurs"]
                if note.get("nouveau") or (was == 0 and state["viewers"] > 0):
                    for line in hub.backlog[-60:]:        # a newcomer gets the recent story at once
                        await ws.send(line)

    async def events():
        hub.clients.append(mine)
        try:
            while True:
                try:
                    line = await asyncio.to_thread(mine.get, True, 5)
                except queue.Empty:
                    continue
                if state["viewers"]:
                    await ws.send(line)
        finally:
            hub.clients.remove(mine)

    async def frames():
        last = None
        while True:
            await asyncio.sleep(1 / FPS)
            image = hub.raw_frame
            if state["viewers"] and image is not None and image is not last:
                last = image
                await ws.send(png(image))

    await asyncio.gather(listen(), events(), frames())
