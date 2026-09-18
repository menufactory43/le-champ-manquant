"""PyBoy wrapper: press a button, let the frames run, read the state."""
from __future__ import annotations
from pathlib import Path

from pyboy import PyBoy

import data, ram

BUTTONS = ("a", "b", "up", "down", "left", "right", "start", "select")


class Game:
    def __init__(self, rom: str, *, window: bool = True, speed: int = 0,
                 save_state: str | None = None, shift: int | None = None):
        data.load(rom)
        self.pyboy = PyBoy(rom, window="SDL2" if window else "null")
        self.pyboy.set_emulation_speed(speed)      # 0 = as fast as the host allows
        if save_state and Path(save_state).exists():
            with open(save_state, "rb") as handle:
                self.pyboy.load_state(handle)
        self.shift = shift
        self.on_frame = None                       # called with the PyBoy screen image

    def read(self, address: int) -> int:
        return self.pyboy.memory[address]

    def rom(self, bank: int, address: int) -> int:
        return self.pyboy.memory[bank, address]

    def shifted(self):
        if self.shift is None:
            self.shift = ram.detect_shift(self.read)
        return ram.shifted_reader(self.read, self.shift)

    def tick(self, frames: int) -> None:
        if self.on_frame is None:
            self.pyboy.tick(frames)
            return
        for i in range(frames):
            self.pyboy.tick(1)
            if i % 2 == 0:
                self.on_frame(self.pyboy.screen.image)

    def fast_options(self) -> None:
        """What a player in a hurry sets in the OPTIONS menu: fastest text, battle animations off."""
        if self.shift is None:
            self.shift = ram.detect_shift(self.read)
        address = 0xD355 + self.shift
        self.pyboy.memory[address] = (self.pyboy.memory[address] & 0x70) | 0x80 | 0x01

    def state(self) -> dict:
        if self.shift is None:
            self.shift = ram.detect_shift(self.read)
        return ram.read_state(ram.shifted_reader(self.read, self.shift))

    def press(self, button: str, *, hold: int = 8, settle: int = 24) -> None:
        """Press, release, then let the game digest it (animations, text scroll)."""
        if button == "wait":
            self.tick(settle * 2)
            return
        self.pyboy.button_press(button)
        self.tick(hold)
        self.pyboy.button_release(button)
        self.tick(settle)

    def save(self, path: str) -> None:
        with open(path, "wb") as handle:
            self.pyboy.save_state(handle)

    def close(self) -> None:
        self.pyboy.stop(save=False)

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
