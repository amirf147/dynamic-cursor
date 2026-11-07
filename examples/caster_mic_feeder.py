# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi
"""Caster voice engine microphone state feeder for Dynamic Cursor.

This script demonstrates integration between Caster voice engine mode callbacks
and the Dynamic Cursor Win32 Named Pipe sink.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

# Ensure local src/ package directory is importable without requiring manual PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dynamic_cursor.client import DynamicCursorClient


class CasterMicFeeder:
    """Bridges Caster microphone status events into Dynamic Cursor visual layers."""

    LAYER_ID = "caster_mic"
    PRIORITY = 80

    # Color definitions for speech recognition operational states
    COLOR_LISTENING = "#44FF66"  # Active recognition green
    COLOR_SLEEPING = "#FF5555"   # Standby recognition red
    COLOR_MUTED = "#888888"      # Inactive microphone gray

    def __init__(
        self,
        pipe_name: str = r"\\.\pipe\dynamic_cursor",
        auto_connect: bool = True,
    ) -> None:
        self.client = DynamicCursorClient(pipe_name=pipe_name, auto_connect=auto_connect)

    def on_mic_state_change(self, state: str) -> None:
        """Translates engine microphone state into visual pointer attributes.

        Supported states include 'listening', 'sleeping', and 'muted'.
        """
        norm_state = state.strip().lower()

        if norm_state in {"listening", "active", "on"}:
            self.client.set_layer(
                layer_id=self.LAYER_ID,
                priority=self.PRIORITY,
                tint_rgba=self.COLOR_LISTENING,
                badge_id=None,
            )
        elif norm_state in {"sleeping", "standby", "sleep"}:
            self.client.set_layer(
                layer_id=self.LAYER_ID,
                priority=self.PRIORITY,
                tint_rgba=self.COLOR_SLEEPING,
                badge_id=None,
            )
        elif norm_state in {"muted", "off", "disabled"}:
            self.client.set_layer(
                layer_id=self.LAYER_ID,
                priority=self.PRIORITY,
                tint_rgba=self.COLOR_MUTED,
                badge_id="mic_off",
            )
        else:
            self.client.clear_layer(self.LAYER_ID)

    def clear(self) -> None:
        """Removes the microphone status layer."""
        self.client.clear_layer(self.LAYER_ID)

    def close(self) -> None:
        """Closes the client connection."""
        self.client.close()

    def __enter__(self) -> CasterMicFeeder:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


def run_simulation(pipe_name: str, cycles: int = 3, interval_sec: float = 1.5) -> None:
    """Simulates Caster voice mode transitions sequentially."""
    states = ["listening", "sleeping", "muted"]
    sys.stdout.write(f"Connecting to Dynamic Cursor pipe: {pipe_name}\n")

    with CasterMicFeeder(pipe_name=pipe_name) as feeder:
        for cycle in range(cycles):
            sys.stdout.write(f"Executing cycle {cycle + 1} of {cycles}\n")
            for state in states:
                sys.stdout.write(f"  Transitioning mic state to: {state}\n")
                feeder.on_mic_state_change(state)
                time.sleep(interval_sec)

        feeder.clear()
        sys.stdout.write("Simulation cycle finished, cleared microphone layer.\n")


def main() -> int:
    """Parses command line arguments and runs the state feeder simulation."""
    parser = argparse.ArgumentParser(
        description="Caster voice engine microphone state feeder reference.",
    )
    parser.add_argument(
        "--pipe",
        type=str,
        default=r"\\.\pipe\dynamic_cursor",
        help="Named pipe path (default: \\\\.\\pipe\\dynamic_cursor)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=2,
        help="Number of state cycle repetitions.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.2,
        help="Interval between state changes in seconds.",
    )
    args = parser.parse_args()

    try:
        run_simulation(pipe_name=args.pipe, cycles=args.cycles, interval_sec=args.interval)
        return 0
    except Exception as exc:
        sys.stderr.write(f"Feeder execution error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
