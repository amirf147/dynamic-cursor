# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi
"""Dynamic Cursor build progress bar animation and toast notification utility.

This utility demonstrates driving cursor progress bar animations during
long-running tasks followed by an ephemeral completion toast.
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


def run_progress_notification(
    pipe_name: str = r"\\.\pipe\dynamic_cursor",
    steps: int = 20,
    delay_sec: float = 0.08,
    toast_message: str = "Build completed: 42 targets compiled",
    duration_ms: int = 2500,
    layer_id: str = "cli_build",
    priority: int = 90,
    progress_style: str = "minimal",
) -> None:
    """Executes a simulated progress animation and sends a completion toast."""
    sys.stdout.write(f"Connecting to Dynamic Cursor pipe: {pipe_name}\n")
    total_steps = max(1, steps)

    with DynamicCursorClient(pipe_name=pipe_name) as client:
        sys.stdout.write(f"Starting task progress animation (style: {progress_style})...\n")

        for step in range(total_steps + 1):
            ratio = round(step / float(total_steps), 3)

            # Assert loading progress with blue tint
            client.set_layer(
                layer_id=layer_id,
                priority=priority,
                tint_rgba="#44AAFF",
                progress=ratio,
                progress_style=progress_style,
            )

            sys.stdout.write(f"\rProgress: [{ratio * 100:5.1f}%] (step {step}/{total_steps})")
            sys.stdout.flush()

            if step < total_steps:
                time.sleep(delay_sec)

        sys.stdout.write("\nTask finished, removing progress bar layer.\n")
        client.clear_layer(layer_id)

        sys.stdout.write(f"Triggering completion toast: '{toast_message}'\n")
        client.show_toast(toast_message, duration_ms=duration_ms)


def main() -> int:
    """Parses command line arguments and runs the build notify utility."""
    parser = argparse.ArgumentParser(
        description="Dynamic Cursor progress bar animation and completion toast reference.",
    )
    parser.add_argument(
        "--pipe",
        type=str,
        default=r"\\.\pipe\dynamic_cursor",
        help="Named pipe path (default: \\\\.\\pipe\\dynamic_cursor)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=25,
        help="Number of animation progress steps from 0 to 100 percent.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.06,
        help="Step duration delay in seconds.",
    )
    parser.add_argument(
        "--message",
        type=str,
        default="Build completed: 42 targets compiled",
        help="Toast notification text on task completion.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=2500,
        help="Toast display duration in milliseconds.",
    )
    parser.add_argument(
        "--progress-style",
        type=str,
        default="minimal",
        choices=["minimal", "overlay", "both"],
        help="Progress bar presentation style: minimal (in-cursor), overlay (companion HUD), or both.",
    )
    args = parser.parse_args()

    try:
        run_progress_notification(
            pipe_name=args.pipe,
            steps=args.steps,
            delay_sec=args.delay,
            toast_message=args.message,
            duration_ms=args.duration,
            progress_style=args.progress_style,
        )
        return 0
    except Exception as exc:
        sys.stderr.write(f"Progress notification error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
