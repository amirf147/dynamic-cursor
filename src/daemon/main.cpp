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
#include <string>

namespace {

constexpr UINT WM_TRAY_ICON_NOTIFY = WM_APP + 1;
constexpr UINT_PTR IDM_PAUSE_RESUME = 1001;
constexpr UINT_PTR IDM_RESET_CURSOR = 1002;
constexpr UINT_PTR IDM_EXIT = 1003;

constexpr const wchar_t* kWindowClassName = L"DynamicCursorTrayWindowClass";
constexpr const wchar_t* kSingleInstanceMutexName = L"Local\\DynamicCursorSingleInstanceMutex";

dynamic_cursor::HardwareCursorDriver* g_driver = nullptr;
dynamic_cursor::CursorStateCompositor* g_compositor = nullptr;
std::atomic<bool> g_paused{false};
bool g_dry_run = false;

/// Restores the operating system default cursor scheme via SPI_SETCURSORS.
void perform_system_restoration() noexcept {
    SystemParametersInfoW(SPI_SETCURSORS, 0, nullptr, 0);
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

        case WM_COMMAND: {
            const auto command_id = LOWORD(wparam);
            if (command_id == IDM_PAUSE_RESUME) {
                bool expected = false;
                if (g_paused.compare_exchange_strong(expected, true)) {
                    if (g_driver != nullptr) {
                        g_driver->restore();
                    }
                } else {
                    g_paused.store(false);
                    if (g_driver != nullptr && g_compositor != nullptr) {
                        g_driver->apply_state(g_compositor->evaluate_composite(), g_dry_run);
                    }
                }
                return 0;
            }

            if (command_id == IDM_RESET_CURSOR) {
                perform_system_restoration();
                return 0;
            }

            if (command_id == IDM_EXIT) {
                DestroyWindow(hwnd);
                return 0;
            }
            break;
        }

        case WM_DESTROY: {
            PostQuitMessage(0);
            return 0;
        }

        default:
            break;
    }

    return DefWindowProcW(hwnd, message, wparam, lparam);
}

} // namespace

int WINAPI wWinMain(HINSTANCE hinstance, HINSTANCE, PWSTR, int) {
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

    // Enforce single-instance mutex to prevent overlapping daemon processes.
    HANDLE hmutex = CreateMutexW(nullptr, TRUE, kSingleInstanceMutexName);
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

    auto on_state_changed = [&](const dynamic_cursor::CompositeState& state) {
        if (g_paused.load()) {
            return;
        }

        driver.apply_state(state, dry_run);

        if (state.has_toast) {
            overlay.show_toast(state.toast);
        } else if (state.has_progress()) {
            overlay.show_hud("", state.progress);
        } else if (overlay.is_visible() && !overlay.is_following()) {
            overlay.hide();
        }
    };

    // Instantiate and start asynchronous named pipe IPC server.
    dynamic_cursor::IpcServer ipc_server(pipe_name, on_state_changed, &compositor);
    if (!ipc_server.start()) {
        DestroyWindow(hidden_window);
        UnregisterClassW(kWindowClassName, hinstance);
        return 1;
    }

    // Configure system tray icon.
    NOTIFYICONDATAW notify_data{};
    notify_data.cbSize = sizeof(NOTIFYICONDATAW);
    notify_data.hWnd = hidden_window;
    notify_data.uID = 1;
    notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    notify_data.uCallbackMessage = WM_TRAY_ICON_NOTIFY;
    notify_data.hIcon = LoadIconW(nullptr, MAKEINTRESOURCEW(32512));
    wcscpy_s(notify_data.szTip, L"Dynamic Cursor Compositor");

    if (!no_tray) {
        Shell_NotifyIconW(NIM_ADD, &notify_data);
    }

    // Standard Win32 message loop dispatching tray events and system messages.
    MSG msg{};
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    // Clean shutdown sequence.
    if (!no_tray) {
        Shell_NotifyIconW(NIM_DELETE, &notify_data);
    }

    ipc_server.stop();
    overlay.stop();
    driver.restore();
    perform_system_restoration();

    UnregisterClassW(kWindowClassName, hinstance);
    return 0;
}
