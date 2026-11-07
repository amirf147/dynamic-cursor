// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#pragma once

#ifndef DYNAMIC_CURSOR_COMPANION_OVERLAY_HPP
#define DYNAMIC_CURSOR_COMPANION_OVERLAY_HPP

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include "protocol.hpp"
#include "raii_handles.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>

#ifdef _MSC_VER
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#endif

namespace dynamic_cursor {

namespace detail {

/// Calculates 32-bit pre-multiplied ARGB channel values.
/// GDI layered windows require color channels pre-multiplied by alpha.
[[nodiscard]] inline constexpr uint32_t premultiply_argb(uint8_t a, uint8_t r, uint8_t g, uint8_t b) noexcept {
    const uint32_t alpha = static_cast<uint32_t>(a);
    const uint32_t pr = (static_cast<uint32_t>(r) * alpha) / 255U;
    const uint32_t pg = (static_cast<uint32_t>(g) * alpha) / 255U;
    const uint32_t pb = (static_cast<uint32_t>(b) * alpha) / 255U;
    return (alpha << 24) | (pr << 16) | (pg << 8) | pb;
}

/// Overload calculating pre-multiplied ARGB from an existing ARGB integer.
[[nodiscard]] inline constexpr uint32_t premultiply_argb(uint32_t argb) noexcept {
    const uint8_t a = static_cast<uint8_t>((argb >> 24) & 0xFF);
    const uint8_t r = static_cast<uint8_t>((argb >> 16) & 0xFF);
    const uint8_t g = static_cast<uint8_t>((argb >> 8) & 0xFF);
    const uint8_t b = static_cast<uint8_t>(argb & 0xFF);
    return premultiply_argb(a, r, g, b);
}

/// Converts a UTF-8 string view into a Win32 wide string.
[[nodiscard]] inline std::wstring utf8_to_wide(std::string_view utf8) {
    if (utf8.empty()) {
        return {};
    }
    const int required = MultiByteToWideChar(
        CP_UTF8,
        0,
        utf8.data(),
        static_cast<int>(utf8.size()),
        nullptr,
        0
    );
    if (required <= 0) {
        return {};
    }
    std::wstring wide(static_cast<size_t>(required), L'\0');
    MultiByteToWideChar(
        CP_UTF8,
        0,
        utf8.data(),
        static_cast<int>(utf8.size()),
        wide.data(),
        required
    );
    return wide;
}

} // namespace detail

/// Frameless layered companion overlay supporting spawn-anchored toasts and raw input follower mode.
class CompanionOverlay {
public:
    static constexpr const wchar_t* kWindowClassName = L"DynamicCursorCompanionOverlay";
    static constexpr UINT_PTR TIMER_TOAST_ID = 1001;
    static constexpr UINT_PTR kToastTimerId = TIMER_TOAST_ID;

    static constexpr UINT WM_OVERLAY_SHOW_TOAST = WM_APP + 101;
    static constexpr UINT WM_OVERLAY_START_FOLLOW = WM_APP + 102;
    static constexpr UINT WM_OVERLAY_STOP_FOLLOW = WM_APP + 103;
    static constexpr UINT WM_OVERLAY_HIDE = WM_APP + 104;
    static constexpr UINT WM_OVERLAY_SHOW_HUD = WM_APP + 105;
    static constexpr UINT WM_OVERLAY_REPOSITION = WM_APP + 106;

    static constexpr int kOverlayOffsetX = 24;
    static constexpr int kOverlayOffsetY = 24;
    static constexpr int kMinCardWidth = 88;
    static constexpr int kMaxCardWidth = 360;

    /// Computes monitor-clamped desktop coordinates ensuring the card remains fully on screen.
    [[nodiscard]] static POINT compute_clamped_position(POINT cursor_pt, int card_w, int card_h) noexcept {
        HMONITOR h_mon = MonitorFromPoint(cursor_pt, MONITOR_DEFAULTTONEAREST);
        MONITORINFO mi{};
        mi.cbSize = sizeof(MONITORINFO);
        int target_x = cursor_pt.x + kOverlayOffsetX;
        int target_y = cursor_pt.y + kOverlayOffsetY;
        if (GetMonitorInfoW(h_mon, &mi)) {
            if (target_x + card_w > mi.rcWork.right) {
                target_x = cursor_pt.x - card_w - 8;
            }
            if (target_y + card_h > mi.rcWork.bottom) {
                target_y = cursor_pt.y - card_h - 8;
            }
            target_x = std::clamp(target_x, static_cast<int>(mi.rcWork.left), static_cast<int>(mi.rcWork.right - card_w));
            target_y = std::clamp(target_y, static_cast<int>(mi.rcWork.top), static_cast<int>(mi.rcWork.bottom - card_h));
        }
        return POINT{target_x, target_y};
    }

    /// Spawns a dedicated background UI thread and waits for window creation to complete.
    CompanionOverlay() {
        ready_event_.reset(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!ready_event_.is_valid()) {
            return;
        }
        ui_thread_ = std::thread(&CompanionOverlay::ui_thread_worker, this);
        WaitForSingleObject(ready_event_.get(), INFINITE);
    }

    /// Shuts down the background message loop and joins the worker thread.
    ~CompanionOverlay() noexcept {
        stop();
    }

    CompanionOverlay(const CompanionOverlay&) = delete;
    CompanionOverlay& operator=(const CompanionOverlay&) = delete;
    CompanionOverlay(CompanionOverlay&&) = delete;
    CompanionOverlay& operator=(CompanionOverlay&&) = delete;

    /// Shuts down the background UI window and joins the worker thread cleanly.
    void stop() noexcept {
        if (hwnd_ != nullptr) {
            PostMessageW(hwnd_, WM_CLOSE, 0, 0);
        } else if (ui_thread_id_ != 0) {
            PostThreadMessageW(ui_thread_id_, WM_QUIT, 0, 0);
        }
        if (ui_thread_.joinable()) {
            ui_thread_.join();
        }
        hwnd_ = nullptr;
        ui_thread_id_ = 0;
    }

    /// Mode A: Spawns an anchored toast card near cursor position with automatic dismissal.
    /// In follower mode, registers the raw input sink to follow the cursor while active.
    void show_toast(const std::string& text, uint32_t duration_ms = 1500, bool follow_cursor = true) {
        {
            std::lock_guard<std::mutex> lock(request_mutex_);
            pending_toast_ = ToastRequest{text, duration_ms, follow_cursor};
        }
        if (hwnd_ != nullptr) {
            PostMessageW(hwnd_, WM_OVERLAY_SHOW_TOAST, 0, 0);
        }
    }

    /// Overload accepting structured ToastSpec configuration.
    void show_toast(const ToastSpec& spec) {
        show_toast(spec.text, spec.duration_ms, spec.follow_cursor);
    }

    /// Displays companion HUD content with an optional progress bar and cursor following.
    void show_hud(const std::string& text, float progress = -1.0f, bool follow_cursor = true) {
        {
            std::lock_guard<std::mutex> lock(request_mutex_);
            pending_hud_ = HudRequest{text, progress, follow_cursor};
        }
        if (hwnd_ != nullptr) {
            PostMessageW(hwnd_, WM_OVERLAY_SHOW_HUD, 0, 0);
        }
    }

    /// Mode B: Registers mouse raw input sink for on-demand cursor tracking.
    void start_following() noexcept {
        following_.store(true, std::memory_order_release);
        if (hwnd_ != nullptr) {
            PostMessageW(hwnd_, WM_OVERLAY_START_FOLLOW, 0, 0);
        }
    }

    /// Mode B: Unregisters mouse raw input sink immediately.
    void stop_following() noexcept {
        following_.store(false, std::memory_order_release);
        if (hwnd_ != nullptr) {
            PostMessageW(hwnd_, WM_OVERLAY_STOP_FOLLOW, 0, 0);
        }
    }

    /// Hides the companion window, cancels pending timers, and removes active raw input sinks.
    void hide() noexcept {
        if (hwnd_ != nullptr) {
            PostMessageW(hwnd_, WM_OVERLAY_HIDE, 0, 0);
        }
    }

    /// Repositions the overlay to absolute screen coordinates without changing dimensions.
    void reposition(int x, int y) noexcept {
        {
            std::lock_guard<std::mutex> lock(request_mutex_);
            pending_position_ = POINT{x, y};
        }
        if (hwnd_ != nullptr) {
            PostMessageW(hwnd_, WM_OVERLAY_REPOSITION, 0, 0);
        }
    }

    [[nodiscard]] HWND hwnd() const noexcept {
        return hwnd_;
    }

    [[nodiscard]] bool is_following() const noexcept {
        return following_.load(std::memory_order_acquire);
    }

    [[nodiscard]] bool is_visible() const noexcept {
        return is_visible_.load(std::memory_order_acquire);
    }

    [[nodiscard]] bool is_valid() const noexcept {
        return hwnd_ != nullptr;
    }

private:
    struct ToastRequest {
        std::string text;
        uint32_t duration_ms{1500};
        bool follow_cursor{true};
    };

    struct HudRequest {
        std::string text;
        float progress{-1.0f};
        bool follow_cursor{true};
    };

    UniqueHandle ready_event_;
    std::thread ui_thread_;
    DWORD ui_thread_id_{0};
    HWND hwnd_{nullptr};
    HFONT font_{nullptr};

    std::atomic<bool> following_{false};
    std::atomic<bool> is_visible_{false};
    std::atomic<bool> raw_input_registered_{false};
    std::atomic<int> card_w_{kMinCardWidth};
    std::atomic<int> card_h_{38};

    mutable std::mutex request_mutex_;
    ToastRequest pending_toast_;
    HudRequest pending_hud_;
    POINT pending_position_{0, 0};

    /// Dedicated background UI thread worker procedure.
    void ui_thread_worker() noexcept {
        ui_thread_id_ = GetCurrentThreadId();
        const HINSTANCE hinstance = GetModuleHandleW(nullptr);

        WNDCLASSEXW wc{};
        wc.cbSize = sizeof(WNDCLASSEXW);
        wc.style = CS_HREDRAW | CS_VREDRAW;
        wc.lpfnWndProc = &CompanionOverlay::wnd_proc_thunk;
        wc.hInstance = hinstance;
        wc.hCursor = nullptr;
        wc.hbrBackground = nullptr;
        wc.lpszClassName = kWindowClassName;

        RegisterClassExW(&wc);

        font_ = CreateFontW(
            -13,
            0,
            0,
            0,
            FW_SEMIBOLD,
            FALSE,
            FALSE,
            FALSE,
            DEFAULT_CHARSET,
            OUT_OUTLINE_PRECIS,
            CLIP_DEFAULT_PRECIS,
            CLEARTYPE_QUALITY,
            DEFAULT_PITCH | FF_DONTCARE,
            L"Segoe UI"
        );

        constexpr DWORD ex_style = WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE;
        constexpr DWORD style = WS_POPUP;

        hwnd_ = CreateWindowExW(
            ex_style,
            kWindowClassName,
            L"DynamicCursorCompanionOverlay",
            style,
            0,
            0,
            kMinCardWidth,
            40,
            nullptr,
            nullptr,
            hinstance,
            this
        );

        SetEvent(ready_event_.get());

        if (hwnd_ == nullptr) {
            if (font_ != nullptr) {
                DeleteObject(font_);
                font_ = nullptr;
            }
            UnregisterClassW(kWindowClassName, hinstance);
            return;
        }

        MSG msg{};
        while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        if (raw_input_registered_.load(std::memory_order_relaxed)) {
            unregister_raw_input_internal();
        }

        if (font_ != nullptr) {
            DeleteObject(font_);
            font_ = nullptr;
        }

        UnregisterClassW(kWindowClassName, hinstance);
    }

    /// Static window procedure thunk dispatching to instance method.
    static LRESULT CALLBACK wnd_proc_thunk(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
        CompanionOverlay* self = nullptr;
        if (msg == WM_NCCREATE) {
            auto* cs = reinterpret_cast<CREATESTRUCTW*>(lParam);
            self = static_cast<CompanionOverlay*>(cs->lpCreateParams);
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        } else {
            self = reinterpret_cast<CompanionOverlay*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
        }

        if (self != nullptr) {
            return self->handle_message(hwnd, msg, wParam, lParam);
        }
        return DefWindowProcW(hwnd, msg, wParam, lParam);
    }

    /// Internal message dispatcher running on the background UI thread.
    LRESULT handle_message(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
        switch (msg) {
        case WM_NCHITTEST:
            return HTTRANSPARENT;

        case WM_MOUSEACTIVATE:
            return MA_NOACTIVATE;

        case WM_TIMER:
            if (wParam == TIMER_TOAST_ID) {
                KillTimer(hwnd, TIMER_TOAST_ID);
                ShowWindow(hwnd, SW_HIDE);
                is_visible_.store(false, std::memory_order_release);
                following_.store(false, std::memory_order_release);
                unregister_raw_input_internal();
                return 0;
            }
            break;

        case WM_INPUT:
            if (following_.load(std::memory_order_relaxed)) {
                RAWINPUT raw{};
                UINT size = sizeof(RAWINPUT);
                const UINT result = GetRawInputData(
                    reinterpret_cast<HRAWINPUT>(lParam),
                    RID_INPUT,
                    &raw,
                    &size,
                    sizeof(RAWINPUTHEADER)
                );
                if (result != static_cast<UINT>(-1) && raw.header.dwType == RIM_TYPEMOUSE) {
                    POINT pt{};
                    if (GetCursorPos(&pt)) {
                        const POINT clamped = compute_clamped_position(
                            pt,
                            card_w_.load(std::memory_order_relaxed),
                            card_h_.load(std::memory_order_relaxed)
                        );
                        SetWindowPos(
                            hwnd,
                            nullptr,
                            clamped.x,
                            clamped.y,
                            0,
                            0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                        );
                    }
                }
            }
            return DefWindowProcW(hwnd, msg, wParam, lParam);

        case WM_OVERLAY_SHOW_TOAST: {
            ToastRequest req;
            {
                std::lock_guard<std::mutex> lock(request_mutex_);
                req = pending_toast_;
            }
            render_and_show_toast(req.text, req.duration_ms, req.follow_cursor);
            return 0;
        }

        case WM_OVERLAY_SHOW_HUD: {
            HudRequest req;
            {
                std::lock_guard<std::mutex> lock(request_mutex_);
                req = pending_hud_;
            }
            render_and_show_hud(req.text, req.progress, req.follow_cursor);
            return 0;
        }

        case WM_OVERLAY_START_FOLLOW:
            register_raw_input_internal();
            return 0;

        case WM_OVERLAY_STOP_FOLLOW:
            unregister_raw_input_internal();
            return 0;

        case WM_OVERLAY_HIDE:
            hide_internal();
            return 0;

        case WM_OVERLAY_REPOSITION: {
            POINT pt{};
            {
                std::lock_guard<std::mutex> lock(request_mutex_);
                pt = pending_position_;
            }
            SetWindowPos(
                hwnd,
                nullptr,
                pt.x,
                pt.y,
                0,
                0,
                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            );
            return 0;
        }

        case WM_CLOSE:
            DestroyWindow(hwnd);
            return 0;

        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;

        default:
            break;
        }

        return DefWindowProcW(hwnd, msg, wParam, lParam);
    }

    /// Registers mouse raw input sink for background cursor motion events.
    void register_raw_input_internal() noexcept {
        if (raw_input_registered_.load(std::memory_order_relaxed)) {
            return;
        }
        RAWINPUTDEVICE rid{};
        rid.usUsagePage = 0x01;
        rid.usUsage = 0x02;
        rid.dwFlags = RIDEV_INPUTSINK;
        rid.hwndTarget = hwnd_;
        if (RegisterRawInputDevices(&rid, 1, sizeof(RAWINPUTDEVICE))) {
            raw_input_registered_.store(true, std::memory_order_release);
        }
    }

    /// Removes mouse raw input sink immediately.
    void unregister_raw_input_internal() noexcept {
        if (!raw_input_registered_.load(std::memory_order_relaxed)) {
            return;
        }
        RAWINPUTDEVICE rid{};
        rid.usUsagePage = 0x01;
        rid.usUsage = 0x02;
        rid.dwFlags = RIDEV_REMOVE;
        rid.hwndTarget = nullptr;
        RegisterRawInputDevices(&rid, 1, sizeof(RAWINPUTDEVICE));
        raw_input_registered_.store(false, std::memory_order_release);
    }

    /// Hides window, removes raw input registration, and cancels timer.
    void hide_internal() noexcept {
        if (hwnd_ != nullptr) {
            KillTimer(hwnd_, TIMER_TOAST_ID);
            ShowWindow(hwnd_, SW_HIDE);
        }
        is_visible_.store(false, std::memory_order_release);
        following_.store(false, std::memory_order_release);
        unregister_raw_input_internal();
    }

    /// Renders and displays a toast at current cursor position with optional cursor following.
    void render_and_show_toast(const std::string& text, uint32_t duration_ms, bool follow_cursor = true) {
        if (hwnd_ == nullptr) {
            return;
        }
        POINT pt{};
        if (!GetCursorPos(&pt)) {
            pt.x = 100;
            pt.y = 100;
        }
        render_and_present_card(text, -1.0f, pt.x, pt.y, duration_ms);
        if (follow_cursor) {
            following_.store(true, std::memory_order_release);
            register_raw_input_internal();
        } else {
            following_.store(false, std::memory_order_release);
            unregister_raw_input_internal();
        }
    }

    /// Renders and displays HUD card at current cursor position with optional cursor following.
    void render_and_show_hud(const std::string& text, float progress, bool follow_cursor = true) {
        if (hwnd_ == nullptr) {
            return;
        }
        POINT pt{};
        if (!GetCursorPos(&pt)) {
            pt.x = 100;
            pt.y = 100;
        }
        render_and_present_card(text, progress, pt.x, pt.y, 0);
        if (follow_cursor) {
            following_.store(true, std::memory_order_release);
            register_raw_input_internal();
        } else {
            following_.store(false, std::memory_order_release);
            unregister_raw_input_internal();
        }
    }

    /// Composites card graphics, measures text, and updates layered window.
    void render_and_present_card(
        const std::string& text,
        float progress,
        int pos_x,
        int pos_y,
        uint32_t duration_ms
    ) {
        if (hwnd_ == nullptr) {
            return;
        }

        KillTimer(hwnd_, TIMER_TOAST_ID);

        const std::wstring wtext = detail::utf8_to_wide(text);
        const HDC hdc_screen = GetDC(nullptr);
        if (hdc_screen == nullptr) {
            return;
        }

        const HDC hdc_measure = CreateCompatibleDC(hdc_screen);
        if (hdc_measure == nullptr) {
            ReleaseDC(nullptr, hdc_screen);
            return;
        }

        const bool has_progress = (progress >= 0.0f);
        HGDIOBJ old_measure_font = SelectObject(hdc_measure, font_);
        RECT rc_calc{0, 0, 0, 0};
        DrawTextW(
            hdc_measure,
            wtext.c_str(),
            static_cast<int>(wtext.size()),
            &rc_calc,
            DT_CALCRECT | DT_SINGLELINE
        );
        SelectObject(hdc_measure, old_measure_font);
        DeleteDC(hdc_measure);

        const int text_w = rc_calc.right - rc_calc.left;
        const int card_w = std::clamp(text_w + 44, kMinCardWidth, kMaxCardWidth);
        const int card_h = has_progress ? 46 : 38;

        BITMAPINFO bmi{};
        bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        bmi.bmiHeader.biWidth = card_w;
        bmi.bmiHeader.biHeight = -card_h;
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;
        bmi.bmiHeader.biCompression = BI_RGB;

        uint32_t* card_bits = nullptr;
        HBITMAP hbm_card = CreateDIBSection(
            hdc_screen,
            &bmi,
            DIB_RGB_COLORS,
            reinterpret_cast<void**>(&card_bits),
            nullptr,
            0
        );
        if (!hbm_card || !card_bits) {
            ReleaseDC(nullptr, hdc_screen);
            return;
        }

        // Dark charcoal background (#1E1E24) with subtle slate border.
        constexpr uint8_t bg_r = 30, bg_g = 30, bg_b = 36, bg_a = 235;
        constexpr uint8_t brd_r = 70, brd_g = 94, brd_b = 140, brd_a = 160;
        constexpr float rx = 10.0f;
        const int x0 = 1;
        const int y0 = 1;
        const int x1 = card_w - 2;
        const int y1 = card_h - 2;

        for (int y = 0; y < card_h; ++y) {
            for (int x = 0; x < card_w; ++x) {
                float dx = 0.0f;
                if (x < x0 + static_cast<int>(rx)) {
                    dx = (x0 + rx) - static_cast<float>(x);
                } else if (x > x1 - static_cast<int>(rx)) {
                    dx = static_cast<float>(x) - (x1 - rx);
                }
                float dy = 0.0f;
                if (y < y0 + static_cast<int>(rx)) {
                    dy = (y0 + rx) - static_cast<float>(y);
                } else if (y > y1 - static_cast<int>(rx)) {
                    dy = static_cast<float>(y) - (y1 - rx);
                }

                const size_t idx = static_cast<size_t>(y * card_w + x);
                if (dx > 0.0f && dy > 0.0f) {
                    const float dist = std::hypot(dx, dy);
                    if (dist > rx) {
                        card_bits[idx] = 0;
                        continue;
                    }
                    if (dist > rx - 1.2f) {
                        const float t = (rx - dist) / 1.2f;
                        const uint8_t a = static_cast<uint8_t>(static_cast<float>(brd_a) * t);
                        card_bits[idx] = detail::premultiply_argb(a, brd_r, brd_g, brd_b);
                        continue;
                    }
                } else if (x < x0 || x > x1 || y < y0 || y > y1) {
                    card_bits[idx] = 0;
                    continue;
                } else if (x == x0 || x == x1 || y == y0 || y == y1) {
                    card_bits[idx] = detail::premultiply_argb(brd_a, brd_r, brd_g, brd_b);
                    continue;
                }

                card_bits[idx] = detail::premultiply_argb(bg_a, bg_r, bg_g, bg_b);
            }
        }

        // Active emerald status dot.
        const float dot_cx = 18.0f;
        const float dot_cy = has_progress ? 16.0f : (static_cast<float>(card_h) / 2.0f);
        constexpr float dot_r = 3.5f;
        constexpr uint8_t dot_r_c = 34, dot_g_c = 197, dot_b_c = 94;

        for (int y = static_cast<int>(dot_cy - dot_r - 1.0f); y <= static_cast<int>(dot_cy + dot_r + 2.0f); ++y) {
            for (int x = static_cast<int>(dot_cx - dot_r - 1.0f); x <= static_cast<int>(dot_cx + dot_r + 2.0f); ++x) {
                if (x < 0 || x >= card_w || y < 0 || y >= card_h) {
                    continue;
                }
                const float dist = std::hypot(static_cast<float>(x) - dot_cx, static_cast<float>(y) - dot_cy);
                if (dist > dot_r + 0.8f) {
                    continue;
                }
                const float t = (dist <= dot_r - 0.8f) ? 1.0f : (dot_r + 0.8f - dist) / 1.6f;
                const uint32_t sa = static_cast<uint32_t>(255.0f * t);
                const size_t idx = static_cast<size_t>(y * card_w + x);
                const uint32_t cur = card_bits[idx];
                const uint32_t da = (cur >> 24) & 0xFF;
                const uint32_t d_pr = (cur >> 16) & 0xFF;
                const uint32_t d_pg = (cur >> 8) & 0xFF;
                const uint32_t d_pb = cur & 0xFF;

                const uint32_t s_pr = (static_cast<uint32_t>(dot_r_c) * sa) / 255U;
                const uint32_t s_pg = (static_cast<uint32_t>(dot_g_c) * sa) / 255U;
                const uint32_t s_pb = (static_cast<uint32_t>(dot_b_c) * sa) / 255U;

                const uint32_t inv_sa = 255U - sa;
                const uint32_t out_a = sa + (da * inv_sa) / 255U;
                const uint32_t out_pr = s_pr + (d_pr * inv_sa) / 255U;
                const uint32_t out_pg = s_pg + (d_pg * inv_sa) / 255U;
                const uint32_t out_pb = s_pb + (d_pb * inv_sa) / 255U;
                card_bits[idx] = (out_a << 24) | (out_pr << 16) | (out_pg << 8) | out_pb;
            }
        }

        // Progress bar track and cyan indicator fill.
        if (has_progress) {
            const int bar_x0 = 18;
            const int bar_x1 = card_w - 18;
            const int bar_y0 = card_h - 10;
            const int bar_y1 = card_h - 6;
            const int bar_w = bar_x1 - bar_x0;
            const int fill_w = std::clamp(static_cast<int>(static_cast<float>(bar_w) * progress), 0, bar_w);

            constexpr uint8_t tr_r = 45, tr_g = 55, tr_b = 75, tr_a = 180;
            constexpr uint8_t fl_r = 56, fl_g = 189, fl_b = 248, fl_a = 255;

            for (int y = bar_y0; y <= bar_y1; ++y) {
                for (int x = bar_x0; x <= bar_x1; ++x) {
                    const size_t idx = static_cast<size_t>(y * card_w + x);
                    const uint32_t cur = card_bits[idx];
                    const uint32_t da = (cur >> 24) & 0xFF;
                    const uint32_t d_pr = (cur >> 16) & 0xFF;
                    const uint32_t d_pg = (cur >> 8) & 0xFF;
                    const uint32_t d_pb = cur & 0xFF;

                    const uint8_t sa = (x <= bar_x0 + fill_w) ? fl_a : tr_a;
                    const uint8_t sr = (x <= bar_x0 + fill_w) ? fl_r : tr_r;
                    const uint8_t sg = (x <= bar_x0 + fill_w) ? fl_g : tr_g;
                    const uint8_t sb = (x <= bar_x0 + fill_w) ? fl_b : tr_b;

                    const uint32_t s_pr = (static_cast<uint32_t>(sr) * sa) / 255U;
                    const uint32_t s_pg = (static_cast<uint32_t>(sg) * sa) / 255U;
                    const uint32_t s_pb = (static_cast<uint32_t>(sb) * sa) / 255U;

                    const uint32_t inv_sa = 255U - sa;
                    const uint32_t out_a = sa + (da * inv_sa) / 255U;
                    const uint32_t out_pr = s_pr + (d_pr * inv_sa) / 255U;
                    const uint32_t out_pg = s_pg + (d_pg * inv_sa) / 255U;
                    const uint32_t out_pb = s_pb + (d_pb * inv_sa) / 255U;
                    card_bits[idx] = (out_a << 24) | (out_pr << 16) | (out_pg << 8) | out_pb;
                }
            }
        }

        // Crisp white text rasterization with anti-aliasing.
        uint32_t* text_bits = nullptr;
        HBITMAP hbm_text = CreateDIBSection(
            hdc_screen,
            &bmi,
            DIB_RGB_COLORS,
            reinterpret_cast<void**>(&text_bits),
            nullptr,
            0
        );

        if (hbm_text && text_bits) {
            HDC hdc_text = CreateCompatibleDC(hdc_screen);
            HGDIOBJ old_text_bm = SelectObject(hdc_text, hbm_text);
            HGDIOBJ old_text_font = SelectObject(hdc_text, font_);
            SetBkMode(hdc_text, TRANSPARENT);
            SetTextColor(hdc_text, RGB(255, 255, 255));

            RECT rc_text{30, has_progress ? 6 : 0, card_w - 14, has_progress ? 28 : card_h};
            DrawTextW(
                hdc_text,
                wtext.c_str(),
                static_cast<int>(wtext.size()),
                &rc_text,
                DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_END_ELLIPSIS
            );

            for (int y = 0; y < card_h; ++y) {
                for (int x = 30; x < card_w - 14; ++x) {
                    const size_t t_idx = static_cast<size_t>(y * card_w + x);
                    const uint32_t val = text_bits[t_idx];
                    if (val == 0) {
                        continue;
                    }
                    const uint32_t r_val = (val >> 16) & 0xFF;
                    const uint32_t g_val = (val >> 8) & 0xFF;
                    const uint32_t b_val = val & 0xFF;
                    const uint32_t lum = (r_val * 299 + g_val * 587 + b_val * 114) / 1000;
                    if (lum == 0) {
                        continue;
                    }
                    const uint32_t sa = std::min(255U, lum);
                    const size_t idx = t_idx;
                    const uint32_t cur = card_bits[idx];
                    const uint32_t da = (cur >> 24) & 0xFF;
                    const uint32_t d_pr = (cur >> 16) & 0xFF;
                    const uint32_t d_pg = (cur >> 8) & 0xFF;
                    const uint32_t d_pb = cur & 0xFF;

                    const uint32_t s_pr = sa;
                    const uint32_t s_pg = sa;
                    const uint32_t s_pb = sa;

                    const uint32_t inv_sa = 255U - sa;
                    const uint32_t out_a = sa + (da * inv_sa) / 255U;
                    const uint32_t out_pr = s_pr + (d_pr * inv_sa) / 255U;
                    const uint32_t out_pg = s_pg + (d_pg * inv_sa) / 255U;
                    const uint32_t out_pb = s_pb + (d_pb * inv_sa) / 255U;
                    card_bits[idx] = (out_a << 24) | (out_pr << 16) | (out_pg << 8) | out_pb;
                }
            }

            SelectObject(hdc_text, old_text_font);
            SelectObject(hdc_text, old_text_bm);
            DeleteDC(hdc_text);
            DeleteObject(hbm_text);
        }

        card_w_.store(card_w, std::memory_order_relaxed);
        card_h_.store(card_h, std::memory_order_relaxed);

        HDC hdc_card = CreateCompatibleDC(hdc_screen);
        HGDIOBJ old_card_bm = SelectObject(hdc_card, hbm_card);

        POINT pt_src{0, 0};
        SIZE wnd_size{card_w, card_h};
        POINT pt_dst = compute_clamped_position(POINT{pos_x, pos_y}, card_w, card_h);

        BLENDFUNCTION blend{};
        blend.BlendOp = AC_SRC_OVER;
        blend.BlendFlags = 0;
        blend.SourceConstantAlpha = 255;
        blend.AlphaFormat = AC_SRC_ALPHA;

        UpdateLayeredWindow(
            hwnd_,
            hdc_screen,
            &pt_dst,
            &wnd_size,
            hdc_card,
            &pt_src,
            0,
            &blend,
            ULW_ALPHA
        );

        SelectObject(hdc_card, old_card_bm);
        DeleteDC(hdc_card);
        DeleteObject(hbm_card);
        ReleaseDC(nullptr, hdc_screen);

        SetWindowPos(
            hwnd_,
            HWND_TOPMOST,
            pt_dst.x,
            pt_dst.y,
            card_w,
            card_h,
            SWP_SHOWWINDOW | SWP_NOACTIVATE
        );

        if (duration_ms > 0) {
            SetTimer(hwnd_, TIMER_TOAST_ID, duration_ms, nullptr);
        }

        is_visible_.store(true, std::memory_order_release);
    }
};

/// Convenience alias matching architecture specification.
using CompanionOverlayDriver = CompanionOverlay;

} // namespace dynamic_cursor

#endif // DYNAMIC_CURSOR_COMPANION_OVERLAY_HPP
