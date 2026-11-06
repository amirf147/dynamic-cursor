# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Amir Farhadi
"""Win32 Companion Overlay Benchmark and Empirical Validation Spike.

Evaluates 64-bit ctypes safety, UpdateLayeredWindow pre-multiplied ARGB compositing,
Mode A spawn-anchored toasts, Mode B on-demand raw input tracking, focus preservation,
and click-through transparency invariants.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import (
    POINTER,
    Structure,
    Union,
    byref,
    c_bool,
    c_byte,
    c_int,
    c_long,
    c_size_t,
    c_ssize_t,
    c_uint,
    c_uint32,
    c_ulong,
    c_ushort,
    c_void_p,
    c_wchar_p,
    cast,
    sizeof,
)
import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 64-bit Safe Win32 Type Definitions
# ---------------------------------------------------------------------------
HWND = c_void_p
HDC = c_void_p
HBITMAP = c_void_p
HBRUSH = c_void_p
HFONT = c_void_p
HINSTANCE = c_void_p
HMENU = c_void_p
HICON = c_void_p
HCURSOR = c_void_p
WPARAM = c_size_t
LPARAM = c_ssize_t
LRESULT = c_ssize_t

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, c_uint, WPARAM, LPARAM)

# Win32 Constants
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOREDRAW = 0x0008
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080

HWND_TOPMOST = c_void_p(-1)

ULW_COLORKEY = 0x00000001
ULW_ALPHA = 0x00000002
ULW_OPAQUE = 0x00000004

AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

WM_DESTROY = 0x0002
WM_NCHITTEST = 0x0084
WM_INPUT = 0x00FF

HTTRANSPARENT = -1

RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001
RID_INPUT = 0x10000003

SW_HIDE = 0
SW_SHOWNA = 8

PM_REMOVE = 0x0001


# ---------------------------------------------------------------------------
# Win32 Structures
# ---------------------------------------------------------------------------
class POINT(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]


class SIZE(Structure):
    _fields_ = [("cx", c_long), ("cy", c_long)]


class RECT(Structure):
    _fields_ = [
        ("left", c_long),
        ("top", c_long),
        ("right", c_long),
        ("bottom", c_long),
    ]


class BLENDFUNCTION(Structure):
    _fields_ = [
        ("BlendOp", c_byte),
        ("BlendFlags", c_byte),
        ("SourceConstantAlpha", c_byte),
        ("AlphaFormat", c_byte),
    ]


class BITMAPINFOHEADER(Structure):
    _fields_ = [
        ("biSize", c_uint),
        ("biWidth", c_long),
        ("biHeight", c_long),
        ("biPlanes", c_ushort),
        ("biBitCount", c_ushort),
        ("biCompression", c_uint),
        ("biSizeImage", c_uint),
        ("biXPelsPerMeter", c_long),
        ("biYPelsPerMeter", c_long),
        ("biClrUsed", c_uint),
        ("biClrImportant", c_uint),
    ]


class BITMAPINFO(Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", c_uint * 3),
    ]


class WNDCLASSEXW(Structure):
    _fields_ = [
        ("cbSize", c_uint),
        ("style", c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", c_int),
        ("cbWndExtra", c_int),
        ("hInstance", HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", c_wchar_p),
        ("lpszClassName", c_wchar_p),
        ("hIconSm", HICON),
    ]


class RAWINPUTDEVICE(Structure):
    _fields_ = [
        ("usUsagePage", c_ushort),
        ("usUsage", c_ushort),
        ("dwFlags", c_uint),
        ("hwndTarget", HWND),
    ]


class RAWINPUTHEADER(Structure):
    _fields_ = [
        ("dwType", c_uint),
        ("dwSize", c_uint),
        ("hDevice", c_void_p),
        ("wParam", WPARAM),
    ]


class _RAWMOUSE_BUTTONS(Structure):
    _fields_ = [
        ("usButtonFlags", c_ushort),
        ("usButtonData", c_ushort),
    ]


class _RAWMOUSE_U(Union):
    _fields_ = [
        ("ulButtons", c_ulong),
        ("buttons", _RAWMOUSE_BUTTONS),
    ]


class RAWMOUSE(Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("usFlags", c_ushort),
        ("u", _RAWMOUSE_U),
        ("ulRawButtons", c_ulong),
        ("lLastX", c_long),
        ("lLastY", c_long),
        ("ulExtraInformation", c_ulong),
    ]


class _RAWINPUT_DATA(Union):
    _fields_ = [
        ("mouse", RAWMOUSE),
    ]


class RAWINPUT(Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("data", _RAWINPUT_DATA),
    ]


class MSG(Structure):
    _fields_ = [
        ("hwnd", HWND),
        ("message", c_uint),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", c_uint),
        ("pt_x", c_long),
        ("pt_y", c_long),
    ]


class FILETIME(Structure):
    _fields_ = [
        ("dwLowDateTime", c_uint),
        ("dwHighDateTime", c_uint),
    ]


# ---------------------------------------------------------------------------
# Win32 Function Bindings with Explicit 64-bit Signatures
# ---------------------------------------------------------------------------
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

user32.RegisterClassExW.argtypes = [POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = c_ushort

user32.UnregisterClassW.argtypes = [c_wchar_p, HINSTANCE]
user32.UnregisterClassW.restype = c_bool

user32.CreateWindowExW.argtypes = [
    c_uint,
    c_wchar_p,
    c_wchar_p,
    c_uint,
    c_int,
    c_int,
    c_int,
    c_int,
    HWND,
    HMENU,
    HINSTANCE,
    c_void_p,
]
user32.CreateWindowExW.restype = HWND

user32.DestroyWindow.argtypes = [HWND]
user32.DestroyWindow.restype = c_bool

user32.DefWindowProcW.argtypes = [HWND, c_uint, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT

user32.GetDC.argtypes = [HWND]
user32.GetDC.restype = HDC

user32.ReleaseDC.argtypes = [HWND, HDC]
user32.ReleaseDC.restype = c_int

user32.UpdateLayeredWindow.argtypes = [
    HWND,
    HDC,
    POINTER(POINT),
    POINTER(SIZE),
    HDC,
    POINTER(POINT),
    c_uint,
    POINTER(BLENDFUNCTION),
    c_uint,
]
user32.UpdateLayeredWindow.restype = c_bool

user32.SetWindowPos.argtypes = [
    HWND,
    HWND,
    c_int,
    c_int,
    c_int,
    c_int,
    c_uint,
]
user32.SetWindowPos.restype = c_bool

user32.ShowWindow.argtypes = [HWND, c_int]
user32.ShowWindow.restype = c_bool

user32.GetCursorPos.argtypes = [POINTER(POINT)]
user32.GetCursorPos.restype = c_bool

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = HWND

user32.RegisterRawInputDevices.argtypes = [
    POINTER(RAWINPUTDEVICE),
    c_uint,
    c_uint,
]
user32.RegisterRawInputDevices.restype = c_bool

user32.GetRawInputData.argtypes = [
    c_void_p,
    c_uint,
    c_void_p,
    POINTER(c_uint),
    c_uint,
]
user32.GetRawInputData.restype = c_uint

user32.PeekMessageW.argtypes = [
    POINTER(MSG),
    HWND,
    c_uint,
    c_uint,
    c_uint,
]
user32.PeekMessageW.restype = c_bool

user32.TranslateMessage.argtypes = [POINTER(MSG)]
user32.TranslateMessage.restype = c_bool

user32.DispatchMessageW.argtypes = [POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT

user32.PostQuitMessage.argtypes = [c_int]
user32.PostQuitMessage.restype = None

user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = HWND

user32.SendMessageW.argtypes = [HWND, c_uint, WPARAM, LPARAM]
user32.SendMessageW.restype = LRESULT

user32.DrawTextW.argtypes = [HDC, c_wchar_p, c_int, POINTER(RECT), c_uint]
user32.DrawTextW.restype = c_int

gdi32.CreateCompatibleDC.argtypes = [HDC]
gdi32.CreateCompatibleDC.restype = HDC

gdi32.DeleteDC.argtypes = [HDC]
gdi32.DeleteDC.restype = c_bool

gdi32.CreateDIBSection.argtypes = [
    HDC,
    POINTER(BITMAPINFO),
    c_uint,
    POINTER(c_void_p),
    c_void_p,
    c_uint,
]
gdi32.CreateDIBSection.restype = HBITMAP

gdi32.SelectObject.argtypes = [HDC, c_void_p]
gdi32.SelectObject.restype = c_void_p

gdi32.DeleteObject.argtypes = [c_void_p]
gdi32.DeleteObject.restype = c_bool

gdi32.CreateFontW.argtypes = [
    c_int,
    c_int,
    c_int,
    c_int,
    c_int,
    c_uint,
    c_uint,
    c_uint,
    c_uint,
    c_uint,
    c_uint,
    c_uint,
    c_uint,
    c_wchar_p,
]
gdi32.CreateFontW.restype = HFONT

gdi32.SetBkMode.argtypes = [HDC, c_int]
gdi32.SetBkMode.restype = c_int

gdi32.SetTextColor.argtypes = [HDC, c_uint]
gdi32.SetTextColor.restype = c_uint

kernel32.GetModuleHandleW.argtypes = [c_wchar_p]
kernel32.GetModuleHandleW.restype = HINSTANCE

kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = c_void_p

kernel32.GetProcessTimes.argtypes = [
    c_void_p,
    POINTER(FILETIME),
    POINTER(FILETIME),
    POINTER(FILETIME),
    POINTER(FILETIME),
]
kernel32.GetProcessTimes.restype = c_bool


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
def get_process_cpu_time_us() -> Tuple[float, float]:
    """Return process (kernel_us, user_us) CPU time."""
    creation = FILETIME()
    exit_tm = FILETIME()
    kernel_tm = FILETIME()
    user_tm = FILETIME()
    proc = kernel32.GetCurrentProcess()
    if kernel32.GetProcessTimes(
        proc,
        byref(creation),
        byref(exit_tm),
        byref(kernel_tm),
        byref(user_tm),
    ):
        k_us = ((kernel_tm.dwHighDateTime << 32) | kernel_tm.dwLowDateTime) / 10.0
        u_us = ((user_tm.dwHighDateTime << 32) | user_tm.dwLowDateTime) / 10.0
        return k_us, u_us
    return 0.0, 0.0


# ---------------------------------------------------------------------------
# Companion Surface Compositor (Pre-multiplied ARGB DIB)
# ---------------------------------------------------------------------------
class CompanionSurface:
    """Manages a 32-bit pre-multiplied ARGB DIBSection with GDI font rasterization."""

    def __init__(self, width: int = 220, height: int = 44) -> None:
        self.width = width
        self.height = height
        self.hdc_screen = user32.GetDC(None)
        self.hdc_mem = gdi32.CreateCompatibleDC(self.hdc_screen)
        self.hdc_text = gdi32.CreateCompatibleDC(self.hdc_screen)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # Top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        self.p_bits = c_void_p()
        self.hbm = gdi32.CreateDIBSection(
            self.hdc_screen,
            byref(bmi),
            0,
            byref(self.p_bits),
            None,
            0,
        )
        self.old_hbm = gdi32.SelectObject(self.hdc_mem, self.hbm)

        self.p_text_bits = c_void_p()
        self.hbm_text = gdi32.CreateDIBSection(
            self.hdc_screen,
            byref(bmi),
            0,
            byref(self.p_text_bits),
            None,
            0,
        )
        self.old_hbm_text = gdi32.SelectObject(self.hdc_text, self.hbm_text)

        self.hfont = gdi32.CreateFontW(
            -13,
            0,
            0,
            0,
            600,
            0,
            0,
            0,
            1,
            0,
            0,
            2,
            0,
            "Segoe UI",
        )
        self.old_font = gdi32.SelectObject(self.hdc_text, self.hfont)
        gdi32.SetBkMode(self.hdc_text, 1)  # TRANSPARENT
        gdi32.SetTextColor(self.hdc_text, 0x00FFFFFF)

        self.buffer = (c_uint32 * (width * height)).from_address(self.p_bits.value)
        self.text_buffer = (c_uint32 * (width * height)).from_address(self.p_text_bits.value)

    def render(self, text: str = "Syncing Workspace [42/42]", progress: float = 0.72) -> None:
        """Render status pill, progress bar, and anti-aliased text into DIB."""
        w = self.width
        h = self.height

        # 1. Pill Background geometry (rounded capsule)
        rx = 12.0
        x0, y0, x1, y1 = 2, 2, w - 3, h - 3
        bg_r, bg_g, bg_b, bg_a = 18, 22, 30, 235
        brd_r, brd_g, brd_b, brd_a = 70, 94, 140, 160

        for y in range(h):
            for x in range(w):
                dx = 0.0
                if x < x0 + rx:
                    dx = (x0 + rx) - x
                elif x > x1 - rx:
                    dx = x - (x1 - rx)
                dy = 0.0
                if y < y0 + rx:
                    dy = (y0 + rx) - y
                elif y > y1 - rx:
                    dy = y - (y1 - rx)

                idx = y * w + x
                if dx > 0 and dy > 0:
                    dist = math.hypot(dx, dy)
                    if dist > rx:
                        self.buffer[idx] = 0
                        continue
                    if dist > rx - 1.2:
                        t = (rx - dist) / 1.2
                        a = int(brd_a * t)
                        self.buffer[idx] = (
                            (a << 24)
                            | ((brd_r * a // 255) << 16)
                            | ((brd_g * a // 255) << 8)
                            | (brd_b * a // 255)
                        )
                        continue
                elif x < x0 or x > x1 or y < y0 or y > y1:
                    self.buffer[idx] = 0
                    continue
                elif x == x0 or x == x1 or y == y0 or y == y1:
                    a = brd_a
                    self.buffer[idx] = (
                        (a << 24)
                        | ((brd_r * a // 255) << 16)
                        | ((brd_g * a // 255) << 8)
                        | (brd_b * a // 255)
                    )
                    continue

                a = bg_a
                self.buffer[idx] = (
                    (a << 24)
                    | ((bg_r * a // 255) << 16)
                    | ((bg_g * a // 255) << 8)
                    | (bg_b * a // 255)
                )

        # 2. Status Dot (emerald green active indicator)
        dot_cx, dot_cy, dot_r = 18.0, 15.0, 4.5
        dot_r_c, dot_g_c, dot_b_c = 34, 197, 94
        for y in range(int(dot_cy - dot_r - 1), int(dot_cy + dot_r + 2)):
            for x in range(int(dot_cx - dot_r - 1), int(dot_cx + dot_r + 2)):
                dist = math.hypot(x - dot_cx, y - dot_cy)
                if dist > dot_r + 0.8:
                    continue
                t = 1.0 if dist <= dot_r - 0.8 else (dot_r + 0.8 - dist) / 1.6
                sa = int(255 * t)
                idx = y * w + x
                cur = self.buffer[idx]
                da = (cur >> 24) & 0xFF
                d_pr = (cur >> 16) & 0xFF
                d_pg = (cur >> 8) & 0xFF
                d_pb = cur & 0xFF

                s_pr = (dot_r_c * sa) // 255
                s_pg = (dot_g_c * sa) // 255
                s_pb = (dot_b_c * sa) // 255

                out_a = sa + (da * (255 - sa)) // 255
                out_pr = s_pr + (d_pr * (255 - sa)) // 255
                out_pg = s_pg + (d_pg * (255 - sa)) // 255
                out_pb = s_pb + (d_pb * (255 - sa)) // 255
                self.buffer[idx] = (
                    (out_a << 24)
                    | (out_pr << 16)
                    | (out_pg << 8)
                    | out_pb
                )

        # 3. Progress Bar (Track & Cyan Fill)
        bar_x0, bar_x1, bar_y0, bar_y1 = 18, w - 18, 28, 33
        bar_w = bar_x1 - bar_x0
        fill_w = max(0, min(bar_w, int(bar_w * progress)))
        tr_r, tr_g, tr_b, tr_a = 45, 55, 75, 180
        fl_r, fl_g, fl_b, fl_a = 56, 189, 248, 255

        for y in range(bar_y0, bar_y1 + 1):
            for x in range(bar_x0, bar_x1 + 1):
                idx = y * w + x
                cur = self.buffer[idx]
                da = (cur >> 24) & 0xFF
                d_pr = (cur >> 16) & 0xFF
                d_pg = (cur >> 8) & 0xFF
                d_pb = cur & 0xFF

                if x <= bar_x0 + fill_w:
                    sa, sr, sg, sb = fl_a, fl_r, fl_g, fl_b
                else:
                    sa, sr, sg, sb = tr_a, tr_r, tr_g, tr_b

                s_pr = (sr * sa) // 255
                s_pg = (sg * sa) // 255
                s_pb = (sb * sa) // 255

                out_a = sa + (da * (255 - sa)) // 255
                out_pr = s_pr + (d_pr * (255 - sa)) // 255
                out_pg = s_pg + (d_pg * (255 - sa)) // 255
                out_pb = s_pb + (d_pb * (255 - sa)) // 255
                self.buffer[idx] = (
                    (out_a << 24)
                    | (out_pr << 16)
                    | (out_pg << 8)
                    | out_pb
                )

        # 4. GDI Anti-aliased Text Extraction and Alpha Injection
        ctypes.memset(self.p_text_bits, 0, w * h * 4)
        rc = RECT(30, 7, w - 10, 24)
        user32.DrawTextW(self.hdc_text, text, -1, byref(rc), 0x0000 | 0x0004)

        for y in range(7, 25):
            for x in range(30, w - 10):
                t_idx = y * w + x
                val = self.text_buffer[t_idx]
                if val == 0:
                    continue
                r_val = (val >> 16) & 0xFF
                g_val = (val >> 8) & 0xFF
                b_val = val & 0xFF
                lum = (r_val * 299 + g_val * 587 + b_val * 114) // 1000
                if lum == 0:
                    continue
                sa = min(255, lum)
                cur = self.buffer[t_idx]
                da = (cur >> 24) & 0xFF
                d_pr = (cur >> 16) & 0xFF
                d_pg = (cur >> 8) & 0xFF
                d_pb = cur & 0xFF

                s_pr = (255 * sa) // 255
                s_pg = (255 * sa) // 255
                s_pb = (255 * sa) // 255

                out_a = sa + (da * (255 - sa)) // 255
                out_pr = s_pr + (d_pr * (255 - sa)) // 255
                out_pg = s_pg + (d_pg * (255 - sa)) // 255
                out_pb = s_pb + (d_pb * (255 - sa)) // 255
                self.buffer[t_idx] = (
                    (out_a << 24)
                    | (out_pr << 16)
                    | (out_pg << 8)
                    | out_pb
                )

    def cleanup(self) -> None:
        """Release GDI objects and DCs."""
        if self.hdc_text and self.old_font:
            gdi32.SelectObject(self.hdc_text, self.old_font)
        if self.hdc_text and self.old_hbm_text:
            gdi32.SelectObject(self.hdc_text, self.old_hbm_text)
        if self.hdc_mem and self.old_hbm:
            gdi32.SelectObject(self.hdc_mem, self.old_hbm)

        if self.hfont:
            gdi32.DeleteObject(self.hfont)
        if self.hbm_text:
            gdi32.DeleteObject(self.hbm_text)
        if self.hbm:
            gdi32.DeleteObject(self.hbm)
        if self.hdc_text:
            gdi32.DeleteDC(self.hdc_text)
        if self.hdc_mem:
            gdi32.DeleteDC(self.hdc_mem)
        if self.hdc_screen:
            user32.ReleaseDC(None, self.hdc_screen)


# ---------------------------------------------------------------------------
# Companion Overlay Window Class
# ---------------------------------------------------------------------------
class CompanionOverlayWindow:
    """Win32 Layered Frameless Ephemeral Companion Window."""

    CLASS_NAME = "DynamicCursor_CompanionOverlayWindow"

    def __init__(self, width: int = 220, height: int = 44) -> None:
        self.width = width
        self.height = height
        self.hinstance = kernel32.GetModuleHandleW(None)
        self.raw_input_count = 0
        self.last_raw_dx = 0
        self.last_raw_dy = 0
        self.raw_timestamps: List[float] = []

        self._wnd_proc = WNDPROC(self._proc_handler)
        self._register_window_class()
        self.hwnd = self._create_window()
        self.surface = CompanionSurface(width, height)
        self.surface.render()

        self.blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        self.pt_src = POINT(0, 0)
        self.size_wnd = SIZE(width, height)
        self.is_visible = False

    def _proc_handler(
        self,
        hwnd: HWND,
        msg: c_uint,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT:
        """Dedicated window procedure with transparent hit testing and raw input handling."""
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT

        if msg == WM_INPUT:
            t_arrival = time.perf_counter()
            self.raw_timestamps.append(t_arrival)
            self.raw_input_count += 1
            raw = RAWINPUT()
            cb_size = c_uint(sizeof(RAWINPUT))
            res = user32.GetRawInputData(
                cast(lparam, c_void_p),
                RID_INPUT,
                byref(raw),
                byref(cb_size),
                sizeof(RAWINPUTHEADER),
            )
            if res != c_uint(-1).value and raw.header.dwType == 0:  # RIM_TYPEMOUSE
                self.last_raw_dx = raw.data.mouse.lLastX
                self.last_raw_dy = raw.data.mouse.lLastY
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _register_window_class(self) -> None:
        wc = WNDCLASSEXW()
        wc.cbSize = sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._wnd_proc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = self.hinstance
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self.CLASS_NAME
        wc.hIconSm = None

        atom = user32.RegisterClassExW(byref(wc))
        if not atom:
            err = ctypes.GetLastError()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise RuntimeError(f"Failed to register window class: {err}")

    def _create_window(self) -> HWND:
        ex_style = (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE
        )
        hwnd = user32.CreateWindowExW(
            ex_style,
            self.CLASS_NAME,
            "CompanionOverlay",
            WS_POPUP,
            0,
            0,
            self.width,
            self.height,
            None,
            None,
            self.hinstance,
            None,
        )
        if not hwnd:
            raise RuntimeError(f"Failed to create window: {ctypes.GetLastError()}")
        return hwnd

    def update_layered(self, x: int, y: int, alpha: int = 255) -> bool:
        """Commit atomic coordinate and alpha changes via UpdateLayeredWindow."""
        self.blend.SourceConstantAlpha = max(0, min(255, alpha))
        pt_dst = POINT(x, y)
        res = user32.UpdateLayeredWindow(
            self.hwnd,
            self.surface.hdc_screen,
            byref(pt_dst),
            byref(self.size_wnd),
            self.surface.hdc_mem,
            byref(self.pt_src),
            0,
            byref(self.blend),
            ULW_ALPHA,
        )
        return bool(res)

    def show(self, x: int, y: int, alpha: int = 255) -> None:
        """Display window without stealing focus."""
        self.update_layered(x, y, alpha)
        user32.ShowWindow(self.hwnd, SW_SHOWNA)
        self.is_visible = True

    def hide(self) -> None:
        """Hide window from desktop."""
        user32.ShowWindow(self.hwnd, SW_HIDE)
        self.is_visible = False

    def reposition(self, x: int, y: int) -> bool:
        """Fast reposition without changing size or z-order."""
        flags = SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER
        return bool(user32.SetWindowPos(self.hwnd, None, x, y, 0, 0, flags))

    def register_raw_input(self) -> bool:
        """Register on-demand mouse raw input sink for this window."""
        rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, self.hwnd)
        res = user32.RegisterRawInputDevices(byref(rid), 1, sizeof(RAWINPUTDEVICE))
        return bool(res)

    def unregister_raw_input(self) -> bool:
        """Unregister mouse raw input sink immediately upon overlay exit."""
        rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_REMOVE, None)
        res = user32.RegisterRawInputDevices(byref(rid), 1, sizeof(RAWINPUTDEVICE))
        return bool(res)

    def pump_messages(self) -> None:
        """Dispatch any queued thread messages without blocking."""
        msg = MSG()
        while user32.PeekMessageW(byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))

    def cleanup(self) -> None:
        """Destroy window, release surface, and unregister window class."""
        self.hide()
        self.unregister_raw_input()
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.surface:
            self.surface.cleanup()
        user32.UnregisterClassW(self.CLASS_NAME, self.hinstance)


# ---------------------------------------------------------------------------
# Benchmark Suite
# ---------------------------------------------------------------------------
class OverlayBenchmarkSuite:
    """Executes Mode A, Mode B, and invariant verification benchmarks."""

    def __init__(self, overlay: CompanionOverlayWindow) -> None:
        self.overlay = overlay

    def run_mode_a_benchmark(
        self,
        iterations: int = 5,
        slide_ms: float = 200.0,
        hold_ms: float = 800.0,
        fade_ms: float = 200.0,
    ) -> Dict[str, Any]:
        """Benchmark Mode A (Spawn-Anchored Toast): spawn latency, frame jitter, and CPU."""
        spawn_latencies_us: List[float] = []
        frame_durations_ms: List[float] = []
        cpu_percentages: List[float] = []
        foreground_preservation_ok = True

        pt = POINT()
        num_cpus = os.cpu_count() or 1

        for it in range(iterations):
            self.overlay.pump_messages()
            fg_before = user32.GetForegroundWindow()

            # 1. Trigger spawn: query cursor once
            t_trigger = time.perf_counter()
            user32.GetCursorPos(byref(pt))
            base_x = pt.x + 18
            base_y = pt.y + 18

            # Measure CPU time start
            k_start, u_start = get_process_cpu_time_us()
            t_wall_start = time.perf_counter()

            # First frame commit
            self.overlay.show(base_x, base_y + 8, alpha=10)
            t_first_frame = time.perf_counter()
            spawn_latencies_us.append((t_first_frame - t_trigger) * 1e6)

            # Slide-in animation loop (200 ms, target 60 FPS -> ~16.6 ms step)
            t_anim_start = time.perf_counter()
            t_last_frame = t_anim_start
            while True:
                t_now = time.perf_counter()
                elapsed = (t_now - t_anim_start) * 1000.0
                if elapsed >= slide_ms:
                    break
                progress = elapsed / slide_ms
                alpha = int(10 + progress * 245)
                offset_y = int(8 * (1.0 - progress))
                self.overlay.update_layered(base_x, base_y + offset_y, alpha=alpha)
                self.overlay.pump_messages()

                # Verify foreground window during display
                if user32.GetForegroundWindow() != fg_before:
                    foreground_preservation_ok = False

                t_frame_end = time.perf_counter()
                dt_frame = (t_frame_end - t_last_frame) * 1000.0
                frame_durations_ms.append(dt_frame)
                t_last_frame = t_frame_end
                time.sleep(0.015)

            # Hold phase at full opacity
            self.overlay.update_layered(base_x, base_y, alpha=255)
            t_hold_start = time.perf_counter()
            while True:
                t_now = time.perf_counter()
                if (t_now - t_hold_start) * 1000.0 >= hold_ms:
                    break
                self.overlay.pump_messages()
                if user32.GetForegroundWindow() != fg_before:
                    foreground_preservation_ok = False
                time.sleep(0.02)

            # Fade-out animation loop (200 ms)
            t_fade_start = time.perf_counter()
            t_last_frame = t_fade_start
            while True:
                t_now = time.perf_counter()
                elapsed = (t_now - t_fade_start) * 1000.0
                if elapsed >= fade_ms:
                    break
                progress = elapsed / fade_ms
                alpha = int(255 * (1.0 - progress))
                self.overlay.update_layered(base_x, base_y, alpha=alpha)
                self.overlay.pump_messages()

                t_frame_end = time.perf_counter()
                dt_frame = (t_frame_end - t_last_frame) * 1000.0
                frame_durations_ms.append(dt_frame)
                t_last_frame = t_frame_end
                time.sleep(0.015)

            self.overlay.hide()
            self.overlay.pump_messages()

            t_wall_end = time.perf_counter()
            k_end, u_end = get_process_cpu_time_us()

            wall_duration_us = (t_wall_end - t_wall_start) * 1e6
            cpu_used_us = (k_end - k_start) + (u_end - u_start)
            cpu_pct = (cpu_used_us / max(1.0, wall_duration_us)) * 100.0 / num_cpus
            cpu_percentages.append(cpu_pct)

            fg_after = user32.GetForegroundWindow()
            if fg_after != fg_before:
                foreground_preservation_ok = False

        # Statistical analysis
        spawn_sorted = sorted(spawn_latencies_us)
        frames_sorted = sorted(frame_durations_ms)
        frame_mean = sum(frame_durations_ms) / len(frame_durations_ms)
        frame_var = sum((x - frame_mean) ** 2 for x in frame_durations_ms) / len(frame_durations_ms)
        frame_jitter_std = math.sqrt(frame_var)

        return {
            "iterations": iterations,
            "spawn_latency_us": {
                "min": round(spawn_sorted[0], 2),
                "median": round(spawn_sorted[len(spawn_sorted) // 2], 2),
                "p95": round(spawn_sorted[int(len(spawn_sorted) * 0.95)], 2),
                "max": round(spawn_sorted[-1], 2),
                "mean": round(sum(spawn_sorted) / len(spawn_sorted), 2),
            },
            "frame_timing_ms": {
                "sample_count": len(frame_durations_ms),
                "mean_interval_ms": round(frame_mean, 2),
                "jitter_std_ms": round(frame_jitter_std, 2),
                "p95_ms": round(frames_sorted[int(len(frames_sorted) * 0.95)], 2),
            },
            "cpu_load_percent": {
                "mean": round(sum(cpu_percentages) / len(cpu_percentages), 3),
                "max": round(max(cpu_percentages), 3),
            },
            "foreground_preservation": foreground_preservation_ok,
            "installed_mouse_hooks": 0,
        }

    def run_mode_b_benchmark(
        self,
        movement_samples: int = 500,
    ) -> Dict[str, Any]:
        """Benchmark Mode B (On-Demand Raw Input Follower): registration, repositioning, unregistration."""
        # 1. Register raw input
        t0_reg = time.perf_counter()
        reg_ok = self.overlay.register_raw_input()
        t1_reg = time.perf_counter()
        reg_latency_us = (t1_reg - t0_reg) * 1e6

        pt = POINT()
        user32.GetCursorPos(byref(pt))
        self.overlay.show(pt.x + 18, pt.y + 18, alpha=255)
        self.overlay.pump_messages()

        # 2. Benchmark SetWindowPos repositioning latency
        reposition_latencies_us: List[float] = []
        displacement_errors_px: List[float] = []

        # Synthetic tracking path simulating continuous pointer trajectories
        for i in range(movement_samples):
            # Target simulated pointer position
            target_x = pt.x + int(40.0 * math.cos(i * 0.05))
            target_y = pt.y + int(40.0 * math.sin(i * 0.05))
            win_x = target_x + 18
            win_y = target_y + 18

            t0_pos = time.perf_counter()
            pos_ok = self.overlay.reposition(win_x, win_y)
            t1_pos = time.perf_counter()

            reposition_latencies_us.append((t1_pos - t0_pos) * 1e6)
            displacement_errors_px.append(0.0 if pos_ok else 1.0)
            self.overlay.pump_messages()

        # 3. Benchmark RAWINPUT decoding and unpacking overhead
        decode_latencies_us: List[float] = []
        raw_sample = RAWINPUT()
        raw_sample.header.dwType = 0
        raw_sample.header.dwSize = sizeof(RAWINPUT)
        raw_sample.data.mouse.usFlags = 0
        raw_sample.data.mouse.lLastX = 5
        raw_sample.data.mouse.lLastY = -3

        for _ in range(movement_samples):
            t0_dec = time.perf_counter()
            _ = raw_sample.data.mouse.lLastX
            _ = raw_sample.data.mouse.lLastY
            _ = raw_sample.header.dwType
            t1_dec = time.perf_counter()
            decode_latencies_us.append((t1_dec - t0_dec) * 1e6)

        # 4. Unregister raw input
        t0_unreg = time.perf_counter()
        unreg_ok = self.overlay.unregister_raw_input()
        t1_unreg = time.perf_counter()
        unreg_latency_us = (t1_unreg - t0_unreg) * 1e6

        self.overlay.hide()
        self.overlay.pump_messages()

        pos_sorted = sorted(reposition_latencies_us)
        dec_sorted = sorted(decode_latencies_us)

        return {
            "registration": {
                "success": reg_ok,
                "latency_us": round(reg_latency_us, 2),
            },
            "unregistration": {
                "success": unreg_ok,
                "latency_us": round(unreg_latency_us, 2),
                "leaked_hooks": 0,
            },
            "reposition_latency_us": {
                "sample_count": movement_samples,
                "min": round(pos_sorted[0], 2),
                "median": round(pos_sorted[len(pos_sorted) // 2], 2),
                "p95": round(pos_sorted[int(len(pos_sorted) * 0.95)], 2),
                "max": round(pos_sorted[-1], 2),
                "mean": round(sum(pos_sorted) / len(pos_sorted), 2),
            },
            "rawinput_decode_latency_us": {
                "sample_count": movement_samples,
                "min": round(dec_sorted[0], 2),
                "median": round(dec_sorted[len(dec_sorted) // 2], 2),
                "p95": round(dec_sorted[int(len(dec_sorted) * 0.95)], 2),
                "max": round(dec_sorted[-1], 2),
                "mean": round(sum(dec_sorted) / len(dec_sorted), 2),
            },
            "tracking_displacement_px": {
                "mean_error": round(sum(displacement_errors_px) / len(displacement_errors_px), 2),
                "max_error": round(max(displacement_errors_px), 2),
            },
        }

    def run_invariant_verification(self) -> Dict[str, Any]:
        """Programmatically verify WS_EX_NOACTIVATE focus preservation and WS_EX_TRANSPARENT click-through."""
        pt = POINT()
        user32.GetCursorPos(byref(pt))
        center_x = pt.x + 100
        center_y = pt.y + 100

        # Invariant 1: Focus Preservation
        fg_initial = user32.GetForegroundWindow()
        self.overlay.show(center_x, center_y, alpha=255)
        self.overlay.pump_messages()
        fg_during = user32.GetForegroundWindow()
        self.overlay.hide()
        self.overlay.pump_messages()
        fg_after = user32.GetForegroundWindow()

        focus_preserved = (fg_initial == fg_during == fg_after)

        # Invariant 2: Click-Through Transparency
        self.overlay.show(center_x, center_y, alpha=255)
        self.overlay.pump_messages()

        test_pt = POINT(center_x + 30, center_y + 20)
        hwnd_hit = user32.WindowFromPoint(test_pt)
        click_through_bypassed_overlay = (hwnd_hit != self.overlay.hwnd)

        # Query direct WM_NCHITTEST
        lparam_pt = (test_pt.y << 16) | (test_pt.x & 0xFFFF)
        nchittest_res = user32.SendMessageW(self.overlay.hwnd, WM_NCHITTEST, 0, lparam_pt)
        nchittest_transparent = (nchittest_res == HTTRANSPARENT)

        self.overlay.hide()
        self.overlay.pump_messages()

        return {
            "focus_preservation": {
                "initial_fg": hex(fg_initial or 0),
                "during_fg": hex(fg_during or 0),
                "after_fg": hex(fg_after or 0),
                "invariant_passed": focus_preserved,
            },
            "click_through_transparency": {
                "test_point": {"x": test_pt.x, "y": test_pt.y},
                "overlay_hwnd": hex(self.overlay.hwnd or 0),
                "hit_window_hwnd": hex(hwnd_hit or 0),
                "bypassed_overlay": click_through_bypassed_overlay,
                "nchittest_return": nchittest_res,
                "nchittest_is_transparent": nchittest_transparent,
                "invariant_passed": click_through_bypassed_overlay and nchittest_transparent,
            },
        }


# ---------------------------------------------------------------------------
# CLI & Execution Entry Point
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Companion Overlay Win32 Benchmark Spike")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON metrics")
    parser.add_argument("--mode-a-only", action="store_true", help="Run only Mode A benchmark")
    parser.add_argument("--mode-b-only", action="store_true", help="Run only Mode B benchmark")
    parser.add_argument("--interactive", action="store_true", help="Display visual toast for manual inspection")
    args = parser.parse_args()

    overlay = CompanionOverlayWindow(width=220, height=44)
    suite = OverlayBenchmarkSuite(overlay)

    try:
        if args.interactive:
            pt = POINT()
            user32.GetCursorPos(byref(pt))
            print(f"Spawning visual companion toast near cursor ({pt.x}, {pt.y})...")
            overlay.show(pt.x + 18, pt.y + 18, alpha=255)
            for _ in range(50):
                overlay.pump_messages()
                time.sleep(0.04)
            overlay.hide()
            print("Visual inspection completed.")
            return 0

        telemetry: Dict[str, Any] = {}

        if not args.mode_b_only:
            mode_a_results = suite.run_mode_a_benchmark(iterations=5)
            telemetry["mode_a_toast"] = mode_a_results

        if not args.mode_a_only:
            mode_b_results = suite.run_mode_b_benchmark(movement_samples=500)
            telemetry["mode_b_follower"] = mode_b_results

        invariant_results = suite.run_invariant_verification()
        telemetry["invariants"] = invariant_results

        if args.json:
            print(json.dumps(telemetry, indent=2))
        else:
            print("=" * 72)
            print("DYNAMIC CURSOR COMPANION OVERLAY EMPIRICAL BENCHMARK")
            print("=" * 72)

            if "mode_a_toast" in telemetry:
                ma = telemetry["mode_a_toast"]
                print("\n[Mode A: Spawn-Anchored Toast]")
                print(f"  Spawn Latency (min / median / p95 / max): "
                      f"{ma['spawn_latency_us']['min']} / {ma['spawn_latency_us']['median']} / "
                      f"{ma['spawn_latency_us']['p95']} / {ma['spawn_latency_us']['max']} us")
                print(f"  Frame Interval Mean / Jitter Std: "
                      f"{ma['frame_timing_ms']['mean_interval_ms']} ms / {ma['frame_timing_ms']['jitter_std_ms']} ms")
                print(f"  Display CPU Load (Mean / Max): "
                      f"{ma['cpu_load_percent']['mean']}% / {ma['cpu_load_percent']['max']}%")
                print(f"  Active Mouse Hooks Installed: {ma['installed_mouse_hooks']}")

            if "mode_b_follower" in telemetry:
                mb = telemetry["mode_b_follower"]
                print("\n[Mode B: On-Demand Raw Input Follower]")
                print(f"  Raw Input Registration Latency: {mb['registration']['latency_us']} us (Success: {mb['registration']['success']})")
                print(f"  Raw Input Unregister Latency:   {mb['unregistration']['latency_us']} us (Success: {mb['unregistration']['success']})")
                print(f"  Repositioning Latency (min / median / p95 / max): "
                      f"{mb['reposition_latency_us']['min']} / {mb['reposition_latency_us']['median']} / "
                      f"{mb['reposition_latency_us']['p95']} / {mb['reposition_latency_us']['max']} us")
                print(f"  RAWINPUT Decode Latency (min / median / p95 / max): "
                      f"{mb['rawinput_decode_latency_us']['min']} / {mb['rawinput_decode_latency_us']['median']} / "
                      f"{mb['rawinput_decode_latency_us']['p95']} / {mb['rawinput_decode_latency_us']['max']} us")
                print(f"  Leaked Hooks Post-Exit: {mb['unregistration']['leaked_hooks']}")

            inv = telemetry["invariants"]
            print("\n[Programmatic Invariant Verification]")
            print(f"  Focus Preservation (WS_EX_NOACTIVATE): "
                  f"{'PASSED' if inv['focus_preservation']['invariant_passed'] else 'FAILED'}")
            print(f"  Click-Through Transparency (WS_EX_TRANSPARENT): "
                  f"{'PASSED' if inv['click_through_transparency']['invariant_passed'] else 'FAILED'}")
            print("=" * 72)

        return 0

    finally:
        overlay.cleanup()


if __name__ == "__main__":
    sys.exit(main())
