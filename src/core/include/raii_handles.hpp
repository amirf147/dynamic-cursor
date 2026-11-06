// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#pragma once

#ifndef DYNAMIC_CURSOR_RAII_HANDLES_HPP
#define DYNAMIC_CURSOR_RAII_HANDLES_HPP

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#ifdef _MSC_VER
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#endif

#include <utility>

namespace dynamic_cursor {

/// Move-only RAII wrapper managing an HCURSOR lifecycle via DestroyIcon.
class UniqueCursor {
public:
    constexpr UniqueCursor() noexcept : handle_(nullptr) {}
    explicit UniqueCursor(HCURSOR handle) noexcept : handle_(handle) {}

    ~UniqueCursor() noexcept {
        reset();
    }

    UniqueCursor(const UniqueCursor&) = delete;
    UniqueCursor& operator=(const UniqueCursor&) = delete;

    UniqueCursor(UniqueCursor&& other) noexcept : handle_(std::exchange(other.handle_, nullptr)) {}

    UniqueCursor& operator=(UniqueCursor&& other) noexcept {
        if (this != &other) {
            reset(other.release());
        }
        return *this;
    }

    [[nodiscard]] HCURSOR get() const noexcept {
        return handle_;
    }

    [[nodiscard]] explicit operator bool() const noexcept {
        return handle_ != nullptr;
    }

    HCURSOR release() noexcept {
        return std::exchange(handle_, nullptr);
    }

    void reset(HCURSOR new_handle = nullptr) noexcept {
        if (handle_ != nullptr && handle_ != new_handle) {
            DestroyIcon(handle_);
        }
        handle_ = new_handle;
    }

private:
    HCURSOR handle_{nullptr};
};

/// Move-only RAII wrapper managing an HBITMAP lifecycle via DeleteObject.
class UniqueBitmap {
public:
    constexpr UniqueBitmap() noexcept : handle_(nullptr) {}
    explicit UniqueBitmap(HBITMAP handle) noexcept : handle_(handle) {}

    ~UniqueBitmap() noexcept {
        reset();
    }

    UniqueBitmap(const UniqueBitmap&) = delete;
    UniqueBitmap& operator=(const UniqueBitmap&) = delete;

    UniqueBitmap(UniqueBitmap&& other) noexcept : handle_(std::exchange(other.handle_, nullptr)) {}

    UniqueBitmap& operator=(UniqueBitmap&& other) noexcept {
        if (this != &other) {
            reset(other.release());
        }
        return *this;
    }

    [[nodiscard]] HBITMAP get() const noexcept {
        return handle_;
    }

    [[nodiscard]] explicit operator bool() const noexcept {
        return handle_ != nullptr;
    }

    HBITMAP release() noexcept {
        return std::exchange(handle_, nullptr);
    }

    void reset(HBITMAP new_handle = nullptr) noexcept {
        if (handle_ != nullptr && handle_ != new_handle) {
            DeleteObject(handle_);
        }
        handle_ = new_handle;
    }

private:
    HBITMAP handle_{nullptr};
};

/// Move-only RAII wrapper managing a device context HDC lifecycle via DeleteDC.
class UniqueDc {
public:
    constexpr UniqueDc() noexcept : handle_(nullptr) {}
    explicit UniqueDc(HDC handle) noexcept : handle_(handle) {}

    ~UniqueDc() noexcept {
        reset();
    }

    UniqueDc(const UniqueDc&) = delete;
    UniqueDc& operator=(const UniqueDc&) = delete;

    UniqueDc(UniqueDc&& other) noexcept : handle_(std::exchange(other.handle_, nullptr)) {}

    UniqueDc& operator=(UniqueDc&& other) noexcept {
        if (this != &other) {
            reset(other.release());
        }
        return *this;
    }

    [[nodiscard]] HDC get() const noexcept {
        return handle_;
    }

    [[nodiscard]] explicit operator bool() const noexcept {
        return handle_ != nullptr;
    }

    HDC release() noexcept {
        return std::exchange(handle_, nullptr);
    }

    void reset(HDC new_handle = nullptr) noexcept {
        if (handle_ != nullptr && handle_ != new_handle) {
            DeleteDC(handle_);
        }
        handle_ = new_handle;
    }

private:
    HDC handle_{nullptr};
};

/// Move-only RAII wrapper managing a Win32 HANDLE lifecycle via CloseHandle.
class UniqueHandle {
public:
    constexpr UniqueHandle() noexcept : handle_(nullptr) {}
    explicit UniqueHandle(HANDLE handle) noexcept : handle_(handle) {}

    ~UniqueHandle() noexcept {
        reset();
    }

    UniqueHandle(const UniqueHandle&) = delete;
    UniqueHandle& operator=(const UniqueHandle&) = delete;

    UniqueHandle(UniqueHandle&& other) noexcept : handle_(std::exchange(other.handle_, nullptr)) {}

    UniqueHandle& operator=(UniqueHandle&& other) noexcept {
        if (this != &other) {
            reset(other.release());
        }
        return *this;
    }

    [[nodiscard]] HANDLE get() const noexcept {
        return handle_;
    }

    [[nodiscard]] bool is_valid() const noexcept {
        return handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE;
    }

    [[nodiscard]] explicit operator bool() const noexcept {
        return is_valid();
    }

    HANDLE release() noexcept {
        return std::exchange(handle_, nullptr);
    }

    void reset(HANDLE new_handle = nullptr) noexcept {
        if (is_valid() && handle_ != new_handle) {
            CloseHandle(handle_);
        }
        handle_ = new_handle;
    }

private:
    HANDLE handle_{nullptr};
};

/// Duplicates a master cached cursor handle using CopyIcon before passing to SetSystemCursor.
/// user32!SetSystemCursor destroys the HCURSOR handle passed to it.
/// Master handles must remain cached in memory and never passed directly.
[[nodiscard]] inline HCURSOR duplicate_cursor_for_system(HCURSOR hMaster) noexcept {
    if (hMaster == nullptr) {
        return nullptr;
    }
    return static_cast<HCURSOR>(CopyIcon(hMaster));
}

} // namespace dynamic_cursor

#endif // DYNAMIC_CURSOR_RAII_HANDLES_HPP
