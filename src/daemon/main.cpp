// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shellapi.h>

#include "bitmap_compositor.hpp"
#include "companion_overlay.hpp"
#include "hardware_driver.hpp"
#include "ipc_server.hpp"
#include "protocol.hpp"
#include "raii_handles.hpp"
#include "state_compositor.hpp"

#include <atomic>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <string>

namespace {

constexpr UINT WM_TRAY_ICON_NOTIFY = WM_APP + 1;
constexpr UINT WM_APP_STATE_CHANGED = WM_APP + 2;
constexpr UINT_PTR IDM_PAUSE_RESUME = 1001;
constexpr UINT_PTR IDM_RESET_CURSOR = 1002;
constexpr UINT_PTR IDM_EXIT = 1003;

constexpr const wchar_t* kWindowClassName = L"DynamicCursorTrayWindowClass";
constexpr const wchar_t* kSingleInstanceMutexName = L"Local\\DynamicCursorSingleInstanceMutex";

dynamic_cursor::HardwareCursorDriver* g_driver = nullptr;
dynamic_cursor::CursorStateCompositor* g_compositor = nullptr;
dynamic_cursor::CompanionOverlayDriver* g_overlay = nullptr;

std::atomic<bool> g_paused{false};
bool g_dry_run = false;
bool g_no_tray = false;

std::mutex g_state_mutex;
dynamic_cursor::CompositeState g_pending_state;
uint64_t g_last_applied_hash = 0;
bool g_has_applied = false;
uint64_t g_last_toast_hash = 0;

UINT g_wm_taskbar_created = 0;
NOTIFYICONDATAW g_notify_data{};

/// Restores the operating system default cursor scheme via SPI_SETCURSORS.
void perform_system_restoration() noexcept {
    SystemParametersInfoW(SPI_SETCURSORS, 0, nullptr, 0);
}

/// Computes a lightweight signature hash for toast deduplication.
uint64_t compute_toast_hash(const dynamic_cursor::ToastSpec& spec) noexcept {
    constexpr uint64_t kFnvOffsetBasis = 14695981039346656037ULL;
    constexpr uint64_t kFnvPrime = 1099511628211ULL;
    uint64_t hash = kFnvOffsetBasis;
    for (char c : spec.text) {
        hash ^= static_cast<uint8_t>(c);
        hash *= kFnvPrime;
    }
    hash ^= static_cast<uint64_t>(spec.duration_ms);
    hash *= kFnvPrime;
    hash ^= static_cast<uint64_t>(spec.follow_cursor ? 1 : 0);
    hash *= kFnvPrime;
    return hash;
}

/// Applies composite state changes with deduplication and edge-triggered toasts.
void apply_composite_update(const dynamic_cursor::CompositeState& state, bool force = false) {
    if (g_driver == nullptr) {
        return;
    }

    const uint64_t new_hash = state.compute_hash();
    if (!force && g_has_applied && new_hash == g_last_applied_hash) {
        return;
    }

    dynamic_cursor::CompositeState driver_state = state;
    if (state.has_progress() && state.progress_style == dynamic_cursor::ProgressStyle::Overlay) {
        // Overlay-only style suppresses in-cursor minimal progress bar
        driver_state.progress = -1.0f;
    }

    g_driver->apply_state(driver_state, g_dry_run);
    g_last_applied_hash = new_hash;
    g_has_applied = true;

    if (state.has_toast) {
        const uint64_t toast_sig = compute_toast_hash(state.toast);
        if (toast_sig != g_last_toast_hash) {
            if (g_overlay != nullptr) {
                g_overlay->show_toast(state.toast);
            }
            g_last_toast_hash = toast_sig;
        }
    } else {
        g_last_toast_hash = 0;
        if (state.has_progress()) {
            if (state.progress_style == dynamic_cursor::ProgressStyle::Overlay ||
                state.progress_style == dynamic_cursor::ProgressStyle::Both) {
                if (g_overlay != nullptr) {
                    g_overlay->show_hud("", state.progress);
                }
            } else if (g_overlay != nullptr && g_overlay->is_visible()) {
                g_overlay->hide();
            }
        } else if (g_overlay != nullptr && g_overlay->is_visible()) {
            g_overlay->hide();
        }
    }
}

/// Window procedure for hidden system tray message routing.
LRESULT CALLBACK tray_window_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    switch (message) {
        case WM_TRAY_ICON_NOTIFY: {
            const auto event_type = LOWORD(lparam);
            if (event_type == WM_RBUTTONUP || event_type == WM_CONTEXTMENU) {
                HMENU menu = CreatePopupMenu();
                if (menu != nullptr) {
                    const wchar_t* pause_label = g_paused.load() ? L"Resume Compositing" : L"Pause Compositing";
                    AppendMenuW(menu, MF_STRING, IDM_PAUSE_RESUME, pause_label);
                    AppendMenuW(menu, MF_STRING, IDM_RESET_CURSOR, L"Reset System Cursor");
                    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
                    AppendMenuW(menu, MF_STRING, IDM_EXIT, L"Exit");

                    POINT cursor_pos{};
                    GetCursorPos(&cursor_pos);
                    SetForegroundWindow(hwnd);
                    TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN, cursor_pos.x, cursor_pos.y, 0, hwnd, nullptr);
                    PostMessageW(hwnd, WM_NULL, 0, 0);
                    DestroyMenu(menu);
                }
            }
            return 0;
        }

        case WM_APP_STATE_CHANGED: {
            dynamic_cursor::CompositeState current;
            {
                std::lock_guard<std::mutex> lock(g_state_mutex);
                current = g_pending_state;
            }
            if (!g_paused.load()) {
                apply_composite_update(current);
            }
            return 0;
        }

        case WM_COMMAND: {
            const auto command_id = LOWORD(wparam);
            if (command_id == IDM_PAUSE_RESUME) {
                bool expected = false;
                if (g_paused.compare_exchange_strong(expected, true)) {
                    if (g_driver != nullptr) {
                        g_driver->restore();
                    }
                    g_has_applied = false;
                } else {
                    g_paused.store(false);
                    if (g_compositor != nullptr) {
                        apply_composite_update(g_compositor->evaluate_composite(), true /* force */);
                    }
                }
                return 0;
            }

            if (command_id == IDM_RESET_CURSOR) {
                perform_system_restoration();
                g_has_applied = false;
                return 0;
            }

            if (command_id == IDM_EXIT) {
                DestroyWindow(hwnd);
                return 0;
            }
            break;
        }

        case WM_QUERYENDSESSION:
            return TRUE;

        case WM_ENDSESSION: {
            if (wparam == TRUE) {
                perform_system_restoration();
            }
            return 0;
        }

        case WM_DESTROY: {
            PostQuitMessage(0);
            return 0;
        }

        default: {
            if (g_wm_taskbar_created != 0 && message == g_wm_taskbar_created) {
                if (!g_no_tray) {
                    Shell_NotifyIconW(NIM_ADD, &g_notify_data);
                }
                return 0;
            }
            break;
        }
    }

    return DefWindowProcW(hwnd, message, wparam, lparam);
}

} // namespace

int WINAPI wWinMain(HINSTANCE hinstance, HINSTANCE, PWSTR, int) {
    // Fail-safe initialization: reset any stale or crashed cursor scheme states.
    perform_system_restoration();

    SetUnhandledExceptionFilter([](EXCEPTION_POINTERS*) -> LONG {
        perform_system_restoration();
        return EXCEPTION_CONTINUE_SEARCH;
    });

    g_wm_taskbar_created = RegisterWindowMessageW(L"TaskbarCreated");

    int argc = 0;
    PWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);

    std::wstring pipe_name = dynamic_cursor::kDefaultPipeName;
    bool dry_run = false;
    bool no_tray = false;

    if (argv != nullptr) {
        for (int i = 1; i < argc; ++i) {
            if (wcscmp(argv[i], L"--pipe") == 0 && i + 1 < argc) {
                pipe_name = argv[++i];
            } else if (wcscmp(argv[i], L"--dry-run-cursor") == 0) {
                dry_run = true;
            } else if (wcscmp(argv[i], L"--no-tray") == 0) {
                no_tray = true;
            }
        }
        LocalFree(argv);
    }

    g_dry_run = dry_run;
    g_no_tray = no_tray;

    // Enforce single-instance mutex scoped to pipe name to avoid test harness collisions.
    std::wstring mutex_name = kSingleInstanceMutexName;
    if (pipe_name != dynamic_cursor::kDefaultPipeName) {
        mutex_name = L"Local\\DynamicCursorMutex_";
        for (wchar_t ch : pipe_name) {
            if ((ch >= L'a' && ch <= L'z') || (ch >= L'A' && ch <= L'Z') || (ch >= L'0' && ch <= L'9')) {
                mutex_name += ch;
            } else {
                mutex_name += L'_';
            }
        }
    }

    HANDLE hmutex = CreateMutexW(nullptr, TRUE, mutex_name.c_str());
    if (hmutex == nullptr || GetLastError() == ERROR_ALREADY_EXISTS) {
        if (hmutex != nullptr) {
            CloseHandle(hmutex);
        }
        return 0;
    }
    dynamic_cursor::UniqueHandle mutex_guard(hmutex);

    // Register fail-safe system restoration hooks on exit or abnormal termination.
    std::atexit(perform_system_restoration);
    SetConsoleCtrlHandler([](DWORD) -> BOOL {
        perform_system_restoration();
        return FALSE;
    }, TRUE);

    // Register hidden window class for system tray messages.
    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(WNDCLASSEXW);
    window_class.lpfnWndProc = &tray_window_proc;
    window_class.hInstance = hinstance;
    window_class.lpszClassName = kWindowClassName;

    if (RegisterClassExW(&window_class) == 0) {
        return 1;
    }

    HWND hidden_window = CreateWindowExW(
        0,
        kWindowClassName,
        L"Dynamic Cursor Host",
        0,
        0,
        0,
        0,
        0,
        nullptr,
        nullptr,
        hinstance,
        nullptr
    );

    if (hidden_window == nullptr) {
        UnregisterClassW(kWindowClassName, hinstance);
        return 1;
    }

    // Initialize core visual compositor, hardware driver, and companion overlay.
    dynamic_cursor::CursorStateCompositor compositor;
    dynamic_cursor::HardwareCursorDriver driver;
    dynamic_cursor::CompanionOverlayDriver overlay;

    g_driver = &driver;
    g_compositor = &compositor;
    g_overlay = &overlay;

    auto on_state_changed = [hidden_window](const dynamic_cursor::CompositeState& state) {
        if (g_paused.load()) {
            return;
        }
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            g_pending_state = state;
        }
        PostMessageW(hidden_window, WM_APP_STATE_CHANGED, 0, 0);
    };

    // Instantiate and start asynchronous named pipe IPC server.
    dynamic_cursor::IpcServer ipc_server(pipe_name, on_state_changed, &compositor);
    if (!ipc_server.start()) {
        DestroyWindow(hidden_window);
        UnregisterClassW(kWindowClassName, hinstance);
        return 1;
    }

    // Configure system tray icon.
    g_notify_data.cbSize = sizeof(NOTIFYICONDATAW);
    g_notify_data.hWnd = hidden_window;
    g_notify_data.uID = 1;
    g_notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    g_notify_data.uCallbackMessage = WM_TRAY_ICON_NOTIFY;
    g_notify_data.hIcon = LoadIconW(nullptr, MAKEINTRESOURCEW(32512));
    wcscpy_s(g_notify_data.szTip, L"Dynamic Cursor Compositor");

    if (!no_tray) {
        Shell_NotifyIconW(NIM_ADD, &g_notify_data);
    }

    // Standard Win32 message loop dispatching tray events and system messages.
    MSG msg{};
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    // Clean shutdown sequence.
    if (!no_tray) {
        Shell_NotifyIconW(NIM_DELETE, &g_notify_data);
    }

    ipc_server.stop();
    overlay.stop();
    driver.restore();
    perform_system_restoration();

    UnregisterClassW(kWindowClassName, hinstance);
    return 0;
}
