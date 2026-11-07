# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi
"""Dynamic Cursor Visual Playground and Interactive Feeder.

Provides a graphical control surface for testing pointer shapes, color tints,
corner badges, progress indicators, companion overlay toasts, and multi-feeder
priority arbitration.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

# Ensure local src/ package directory is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dynamic_cursor.client import BadgeId, BaseShape, DynamicCursorClient


class DynamicCursorPlayground(tk.Tk):
    """Interactive visual playground and feeder simulator for Dynamic Cursor."""

    SHAPES = [
        ("Default Arrow", "default_arrow"),
        ("Precision Cross", "precision_cross"),
        ("Reading Minimal", "reading_minimal"),
        ("Text I-Beam", "text_ibeam"),
        ("Hand Pointer", "hand_point"),
    ]

    BADGES = [
        ("None", "none"),
        ("Microphone Off", "mic_off"),
        ("Microphone On", "mic_on"),
        ("TV / Video Playback", "tv_icon"),
        ("Vim Normal Mode", "vim_normal"),
        ("Vim Insert Mode", "vim_insert"),
        ("Recording", "recording"),
    ]

    TINT_PRESETS = [
        ("Clear / Default", ""),
        ("Alert Red", "#FF5555"),
        ("Active Green", "#50FA7B"),
        ("Warning Yellow", "#F1FA8C"),
        ("Focus Purple", "#BD93F9"),
        ("Media Cyan", "#8BE9FD"),
        ("Standby Orange", "#FFB86C"),
    ]

    PROGRESS_STYLES = [
        ("Minimal (In-Cursor)", "minimal"),
        ("Companion Overlay", "overlay"),
        ("Both", "both"),
    ]

    def __init__(self, pipe_name: str = r"\\.\pipe\dynamic_cursor") -> None:
        super().__init__()
        self.title("Dynamic Cursor Playground")
        self.geometry("480x620")
        self.minsize(440, 560)

        self.pipe_name = pipe_name
        self.client: Optional[DynamicCursorClient] = None
        self._simulated_feeder_proc: Optional[subprocess.Popen] = None

        self._setup_style()
        self._create_widgets()
        self._connect_client()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

    def _create_widgets(self) -> None:
        main_frame = ttk.Frame(self, padding="12 12 12 12")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Connection Status Bar
        status_frame = ttk.LabelFrame(main_frame, text="Daemon Connection", padding="8 6 8 6")
        status_frame.pack(fill=tk.X, pady=(0, 8))

        self.lbl_status = ttk.Label(status_frame, text="Checking connection...", font=("Segoe UI", 9, "bold"))
        self.lbl_status.pack(side=tk.LEFT, padx=(4, 8))

        btn_reconnect = ttk.Button(status_frame, text="Reconnect", command=self._connect_client)
        btn_reconnect.pack(side=tk.RIGHT)

        # 2. Cursor Shape & Badge Controls
        visual_frame = ttk.LabelFrame(main_frame, text="Pointer Visuals", padding="8 8 8 8")
        visual_frame.pack(fill=tk.X, pady=(0, 8))

        # Base Shape
        row_shape = ttk.Frame(visual_frame)
        row_shape.pack(fill=tk.X, pady=2)
        ttk.Label(row_shape, text="Base Shape:", width=14).pack(side=tk.LEFT)
        self.var_shape = tk.StringVar(value="default_arrow")
        shape_combo = ttk.Combobox(
            row_shape,
            textvariable=self.var_shape,
            values=[val for _, val in self.SHAPES],
            state="readonly",
        )
        shape_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        shape_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_live_layer())

        # Badge
        row_badge = ttk.Frame(visual_frame)
        row_badge.pack(fill=tk.X, pady=2)
        ttk.Label(row_badge, text="Corner Badge:", width=14).pack(side=tk.LEFT)
        self.var_badge = tk.StringVar(value="none")
        badge_combo = ttk.Combobox(
            row_badge,
            textvariable=self.var_badge,
            values=[val for _, val in self.BADGES],
            state="readonly",
        )
        badge_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        badge_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_live_layer())

        # Color Tint Preset
        row_tint = ttk.Frame(visual_frame)
        row_tint.pack(fill=tk.X, pady=2)
        ttk.Label(row_tint, text="Color Tint:", width=14).pack(side=tk.LEFT)
        self.var_tint = tk.StringVar(value="")
        tint_combo = ttk.Combobox(
            row_tint,
            textvariable=self.var_tint,
            values=[color for _, color in self.TINT_PRESETS],
        )
        tint_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tint_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_live_layer())

        # Tint preset buttons
        btn_tint_row = ttk.Frame(visual_frame)
        btn_tint_row.pack(fill=tk.X, pady=(4, 0))
        for label, color in self.TINT_PRESETS:
            btn = ttk.Button(
                btn_tint_row,
                text=label.split()[0],
                command=lambda c=color: self._set_tint_preset(c),
            )
            btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=1)

        # 3. Mini Progress Bar
        prog_frame = ttk.LabelFrame(main_frame, text="Progress Bar", padding="8 8 8 8")
        prog_frame.pack(fill=tk.X, pady=(0, 8))

        row_prog = ttk.Frame(prog_frame)
        row_prog.pack(fill=tk.X)
        self.var_prog_enable = tk.BooleanVar(value=False)
        chk_prog = ttk.Checkbutton(
            row_prog,
            text="Enable Progress",
            variable=self.var_prog_enable,
            command=self.apply_live_layer,
        )
        chk_prog.pack(side=tk.LEFT)

        self.var_prog_val = tk.DoubleVar(value=0.5)
        self.scale_prog = ttk.Scale(
            row_prog,
            from_=0.0,
            to=1.0,
            variable=self.var_prog_val,
            command=lambda val: self._on_prog_slider(),
        )
        self.scale_prog.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        self.lbl_prog = ttk.Label(row_prog, text="50%", width=5)
        self.lbl_prog.pack(side=tk.RIGHT)

        row_prog_style = ttk.Frame(prog_frame)
        row_prog_style.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row_prog_style, text="Progress Style:", width=14).pack(side=tk.LEFT)
        self.var_prog_style = tk.StringVar(value="minimal")
        style_combo = ttk.Combobox(
            row_prog_style,
            textvariable=self.var_prog_style,
            values=[val for _, val in self.PROGRESS_STYLES],
            state="readonly",
        )
        style_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        style_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_live_layer())

        # 4. Companion Overlay Toast Notification
        toast_frame = ttk.LabelFrame(main_frame, text="Companion Overlay Toast", padding="8 8 8 8")
        toast_frame.pack(fill=tk.X, pady=(0, 8))

        row_toast = ttk.Frame(toast_frame)
        row_toast.pack(fill=tk.X, pady=2)
        ttk.Label(row_toast, text="Toast Text:", width=14).pack(side=tk.LEFT)
        self.entry_toast = ttk.Entry(row_toast)
        self.entry_toast.insert(0, "Build Succeeded")
        self.entry_toast.pack(side=tk.LEFT, fill=tk.X, expand=True)

        row_dur = ttk.Frame(toast_frame)
        row_dur.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row_dur, text="Duration (ms):", width=14).pack(side=tk.LEFT)
        self.var_duration = tk.IntVar(value=1500)
        dur_spin = ttk.Spinbox(row_dur, from_=500, to=10000, increment=250, textvariable=self.var_duration, width=8)
        dur_spin.pack(side=tk.LEFT)

        self.var_toast_follow = tk.BooleanVar(value=True)
        chk_follow = ttk.Checkbutton(row_dur, text="Follow Cursor", variable=self.var_toast_follow)
        chk_follow.pack(side=tk.LEFT, padx=(8, 0))

        btn_toast = ttk.Button(row_dur, text="Trigger Toast Notification", command=self.trigger_toast)
        btn_toast.pack(side=tk.RIGHT)

        # 5. Layer Arbitration & Multi-Feeder Simulator
        feeder_frame = ttk.LabelFrame(main_frame, text="Priority Arbitration & Lease Simulation", padding="8 8 8 8")
        feeder_frame.pack(fill=tk.X, pady=(0, 8))

        row_prio = ttk.Frame(feeder_frame)
        row_prio.pack(fill=tk.X, pady=2)
        ttk.Label(row_prio, text="Playground Priority:", width=18).pack(side=tk.LEFT)
        self.var_priority = tk.IntVar(value=50)
        prio_spin = ttk.Spinbox(row_prio, from_=1, to=100, textvariable=self.var_priority, width=6, command=self.apply_live_layer)
        prio_spin.pack(side=tk.LEFT)

        row_sim = ttk.Frame(feeder_frame)
        row_sim.pack(fill=tk.X, pady=(6, 0))
        self.btn_spawn_feeder = ttk.Button(row_sim, text="Spawn External Feeder (P80 Red)", command=self._toggle_simulated_feeder)
        self.btn_spawn_feeder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        # 6. Action Controls
        action_frame = ttk.Frame(main_frame)
        action_frame.pack(fill=tk.X, pady=(4, 0))

        btn_apply = ttk.Button(action_frame, text="Apply State", command=self.apply_live_layer)
        btn_apply.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        btn_clear = ttk.Button(action_frame, text="Clear Layer", command=self.clear_layer)
        btn_clear.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        btn_reset = ttk.Button(action_frame, text="Reset System Cursor", command=self.reset_system_cursor)
        btn_reset.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

    def _connect_client(self) -> None:
        try:
            self.client = DynamicCursorClient(pipe_name=self.pipe_name)
            self.lbl_status.config(text=f"Connected to {self.pipe_name}", foreground="darkgreen")
        except Exception:
            self.client = None
            self.lbl_status.config(text="Daemon Not Running", foreground="firebrick")

    def _set_tint_preset(self, color_hex: str) -> None:
        self.var_tint.set(color_hex)
        self.apply_live_layer()

    def _on_prog_slider(self) -> None:
        val = self.var_prog_val.get()
        self.lbl_prog.config(text=f"{int(val * 100)}%")
        if self.var_prog_enable.get():
            self.apply_live_layer()

    def apply_live_layer(self) -> None:
        if self.client is None or not self.client.is_connected:
            self._connect_client()
            if self.client is None:
                return

        shape = self.var_shape.get()
        badge = self.var_badge.get()
        tint = self.var_tint.get().strip() or None
        progress = self.var_prog_val.get() if self.var_prog_enable.get() else -1.0
        progress_style = self.var_prog_style.get()
        priority = self.var_priority.get()

        try:
            self.client.set_layer(
                layer_id="playground_layer",
                priority=priority,
                base_shape=shape,
                badge_id=badge if badge != "none" else None,
                tint_rgba=tint,
                progress=progress,
                progress_style=progress_style,
            )
        except Exception as exc:
            self.client = None
            self.lbl_status.config(text=f"Disconnected: {exc}", foreground="firebrick")

    def trigger_toast(self) -> None:
        if self.client is None or not self.client.is_connected:
            self._connect_client()
            if self.client is None:
                return

        text = self.entry_toast.get().strip()
        duration = self.var_duration.get()
        follow_cursor = self.var_toast_follow.get()
        if not text:
            return

        try:
            self.client.show_toast(
                text=text,
                duration_ms=duration,
                follow_cursor=follow_cursor,
            )
        except Exception as exc:
            self.client = None
            self.lbl_status.config(text=f"Disconnected: {exc}", foreground="firebrick")

    def clear_layer(self) -> None:
        if self.client and self.client.is_connected:
            try:
                self.client.clear_layer("playground_layer")
            except Exception:
                pass

    def reset_system_cursor(self) -> None:
        if self.client and self.client.is_connected:
            try:
                self.client.clear_all()
            except Exception:
                pass
        self.var_prog_enable.set(False)
        self.var_prog_style.set("minimal")
        self.var_badge.set("none")
        self.var_tint.set("")
        self.var_shape.set("default_arrow")

    def _toggle_simulated_feeder(self) -> None:
        if self._simulated_feeder_proc is None or self._simulated_feeder_proc.poll() is not None:
            # Spawn independent process asserting higher priority layer
            feeder_script = f"""
from dynamic_cursor.client import DynamicCursorClient
import time
client = DynamicCursorClient(pipe_name=r'{self.pipe_name}')
client.set_layer(layer_id='feeder_alpha', priority=80, tint_rgba='#FF5555', badge_id='tv_icon')
time.sleep(300)
"""
            sub_env = sys.modules["os"].environ.copy()
            sub_env["PYTHONPATH"] = str(SRC_DIR)
            self._simulated_feeder_proc = subprocess.Popen(
                [sys.executable, "-c", feeder_script],
                env=sub_env,
            )
            self.btn_spawn_feeder.config(text="Kill Feeder (Observe Lease Rollback)")
        else:
            # Forcibly terminate feeder process to trigger kernel lease rollback
            self._simulated_feeder_proc.terminate()
            self._simulated_feeder_proc.wait(timeout=2.0)
            self._simulated_feeder_proc = None
            self.btn_spawn_feeder.config(text="Spawn External Feeder (P80 Red)")

    def _on_close(self) -> None:
        if self._simulated_feeder_proc and self._simulated_feeder_proc.poll() is None:
            self._simulated_feeder_proc.terminate()
        if self.client:
            try:
                self.client.clear_layer("playground_layer")
                self.client.close()
            except Exception:
                pass
        self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dynamic Cursor Visual Playground")
    parser.add_argument("--pipe", default=r"\\.\pipe\dynamic_cursor", help="Named pipe path")
    args = parser.parse_args()

    app = DynamicCursorPlayground(pipe_name=args.pipe)
    app.mainloop()


if __name__ == "__main__":
    main()

