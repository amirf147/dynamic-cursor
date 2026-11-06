// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#pragma once

#ifndef DYNAMIC_CURSOR_IPC_SERVER_HPP
#define DYNAMIC_CURSOR_IPC_SERVER_HPP

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#ifdef _MSC_VER
#pragma comment(lib, "kernel32.lib")
#endif

#include "protocol.hpp"
#include "raii_handles.hpp"
#include "state_compositor.hpp"

#include <atomic>
#include <cstdint>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace dynamic_cursor {

/// Asynchronous Win32 Named Pipe server managing client leases and state arbitration.
class IpcServer {
public:
    static constexpr size_t kDefaultInstances = 4;
    static constexpr size_t kMaxInstances = 63;
    static constexpr DWORD kPipeBufferSize = 65536;
    static constexpr uint32_t kMaxPayloadSize = 16 * 1024 * 1024;

    explicit IpcServer(
        std::wstring pipe_name = kDefaultPipeName,
        std::function<void(const CompositeState&)> on_state_changed = nullptr,
        CursorStateCompositor* compositor = nullptr,
        size_t instance_count = kDefaultInstances)
        : pipe_name_(std::move(pipe_name)),
          on_state_changed_(std::move(on_state_changed)),
          owned_compositor_(),
          compositor_(compositor != nullptr ? compositor : &owned_compositor_),
          instance_count_((instance_count >= 1 && instance_count <= kMaxInstances) ? instance_count : kDefaultInstances),
          is_running_(false),
          active_clients_(0),
          next_client_session_id_(0),
          stop_event_(),
          ready_event_(),
          callback_mutex_(),
          worker_thread_() {}

    ~IpcServer() {
        stop();
    }

    IpcServer(const IpcServer&) = delete;
    IpcServer& operator=(const IpcServer&) = delete;
    IpcServer(IpcServer&&) = delete;
    IpcServer& operator=(IpcServer&&) = delete;

    /// Starts the background listener thread.
    /// Returns true when all pipe instances initialize successfully.
    bool start() {
        bool expected = false;
        if (!is_running_.compare_exchange_strong(expected, true)) {
            return true;
        }

        if (!stop_event_.is_valid()) {
            stop_event_.reset(CreateEventW(nullptr, TRUE, FALSE, nullptr));
            if (!stop_event_.is_valid()) {
                is_running_.store(false);
                return false;
            }
        } else {
            ResetEvent(stop_event_.get());
        }

        if (!ready_event_.is_valid()) {
            ready_event_.reset(CreateEventW(nullptr, TRUE, FALSE, nullptr));
            if (!ready_event_.is_valid()) {
                is_running_.store(false);
                return false;
            }
        } else {
            ResetEvent(ready_event_.get());
        }

        worker_thread_ = std::thread(&IpcServer::worker_loop, this);

        const DWORD wait_res = WaitForSingleObject(ready_event_.get(), 5000);
        if (wait_res != WAIT_OBJECT_0 || !is_running_.load()) {
            stop();
            return false;
        }

        return true;
    }

    /// Stops the worker thread and releases all pipe handles.
    void stop() {
        bool expected = true;
        if (!is_running_.compare_exchange_strong(expected, false)) {
            return;
        }

        if (stop_event_.is_valid()) {
            SetEvent(stop_event_.get());
        }

        if (worker_thread_.joinable()) {
            worker_thread_.join();
        }

        if (ready_event_.is_valid()) {
            ResetEvent(ready_event_.get());
        }
    }

    /// Indicates whether the server loop is actively executing.
    [[nodiscard]] bool is_running() const noexcept {
        return is_running_.load();
    }

    /// Returns the configured named pipe path.
    [[nodiscard]] const std::wstring& pipe_name() const noexcept {
        return pipe_name_;
    }

    /// Returns the configured concurrent instance capacity.
    [[nodiscard]] size_t instance_count() const noexcept {
        return instance_count_;
    }

    /// Returns the active number of connected clients.
    [[nodiscard]] size_t active_clients_count() const noexcept {
        return active_clients_.load();
    }

    /// Returns the active state compositor instance.
    [[nodiscard]] CursorStateCompositor& compositor() noexcept {
        return *compositor_;
    }

    /// Returns the active state compositor instance as const.
    [[nodiscard]] const CursorStateCompositor& compositor() const noexcept {
        return *compositor_;
    }

    /// Registers or replaces the state change callback.
    void set_state_changed_callback(std::function<void(const CompositeState&)> callback) {
        std::lock_guard<std::mutex> lock(callback_mutex_);
        on_state_changed_ = std::move(callback);
    }

private:
    enum class PipeState {
        Connecting,
        ReadingHeader,
        ReadingPayload
    };

    struct PipeSlot {
        UniqueHandle pipe_handle;
        OVERLAPPED overlapped{};
        UniqueHandle event_handle;
        PipeState state{PipeState::Connecting};
        uint64_t client_session_id{0};

        uint8_t header_buf[kFrameHeaderSize]{0};
        uint32_t header_bytes_read{0};

        uint32_t expected_payload_len{0};
        std::vector<uint8_t> payload_buf;
        uint32_t payload_bytes_read{0};
    };

    /// Invokes the registered state change callback safely.
    void invoke_state_changed(const CompositeState& state) noexcept {
        try {
            std::function<void(const CompositeState&)> cb;
            {
                std::lock_guard<std::mutex> lock(callback_mutex_);
                cb = on_state_changed_;
            }
            if (cb) {
                cb(state);
            }
        } catch (...) {
            // Callback exceptions are caught to protect server loop execution.
        }
    }

    /// Initiates an asynchronous connection wait on a pipe slot.
    void start_connecting_slot(PipeSlot& slot) {
        slot.state = PipeState::Connecting;
        slot.client_session_id = 0;
        slot.header_bytes_read = 0;
        slot.payload_bytes_read = 0;
        slot.expected_payload_len = 0;
        slot.payload_buf.clear();

        ResetEvent(slot.event_handle.get());
        std::memset(&slot.overlapped, 0, sizeof(OVERLAPPED));
        slot.overlapped.hEvent = slot.event_handle.get();

        const BOOL res = ConnectNamedPipe(slot.pipe_handle.get(), &slot.overlapped);
        if (res) {
            SetEvent(slot.event_handle.get());
        } else {
            const DWORD err = GetLastError();
            if (err == ERROR_PIPE_CONNECTED) {
                SetEvent(slot.event_handle.get());
            } else if (err == ERROR_IO_PENDING) {
                // Pending asynchronous connection.
            } else {
                SetEvent(slot.event_handle.get());
            }
        }
    }

    /// Initiates an asynchronous read for the 4-byte frame length header.
    void start_read_header_slot(PipeSlot& slot) {
        slot.state = PipeState::ReadingHeader;
        ResetEvent(slot.event_handle.get());
        std::memset(&slot.overlapped, 0, sizeof(OVERLAPPED));
        slot.overlapped.hEvent = slot.event_handle.get();

        const DWORD to_read = kFrameHeaderSize - slot.header_bytes_read;
        uint8_t* dest = slot.header_buf + slot.header_bytes_read;
        DWORD bytes_read = 0;

        const BOOL ok = ReadFile(slot.pipe_handle.get(), dest, to_read, &bytes_read, &slot.overlapped);
        if (ok) {
            SetEvent(slot.event_handle.get());
        } else {
            const DWORD err = GetLastError();
            if (err == ERROR_IO_PENDING) {
                // Pending asynchronous read.
            } else {
                SetEvent(slot.event_handle.get());
            }
        }
    }

    /// Initiates an asynchronous read for the frame payload bytes.
    void start_read_payload_slot(PipeSlot& slot) {
        slot.state = PipeState::ReadingPayload;
        ResetEvent(slot.event_handle.get());
        std::memset(&slot.overlapped, 0, sizeof(OVERLAPPED));
        slot.overlapped.hEvent = slot.event_handle.get();

        const DWORD to_read = slot.expected_payload_len - slot.payload_bytes_read;
        uint8_t* dest = slot.payload_buf.data() + slot.payload_bytes_read;
        DWORD bytes_read = 0;

        const BOOL ok = ReadFile(slot.pipe_handle.get(), dest, to_read, &bytes_read, &slot.overlapped);
        if (ok) {
            SetEvent(slot.event_handle.get());
        } else {
            const DWORD err = GetLastError();
            if (err == ERROR_IO_PENDING) {
                // Pending asynchronous read.
            } else {
                SetEvent(slot.event_handle.get());
            }
        }
    }

    /// Cleans up disconnected client resources and executes kernel lease rollback.
    void disconnect_slot(PipeSlot& slot) {
        const uint64_t session_id = slot.client_session_id;
        if (session_id != 0) {
            slot.client_session_id = 0;
            if (active_clients_ > 0) {
                --active_clients_;
            }
            const CompositeState new_state = compositor_->clear_client_layers(session_id);
            invoke_state_changed(new_state);
        }

        if (slot.pipe_handle.is_valid()) {
            DisconnectNamedPipe(slot.pipe_handle.get());
        }

        if (is_running_.load()) {
            start_connecting_slot(slot);
        }
    }

    /// Dispatches a completed wire frame into the state compositor.
    void dispatch_payload(PipeSlot& slot) {
        LayerPayload payload;
        uint8_t op_byte = 0;
        bool parsed = false;

        if (parse_binary_frame(slot.payload_buf.data(), slot.payload_buf.size(), payload, op_byte)) {
            parsed = true;
        } else {
            const std::string_view json_text(
                reinterpret_cast<const char*>(slot.payload_buf.data()),
                slot.payload_buf.size());
            if (parse_json_frame(json_text, payload, op_byte)) {
                parsed = true;
            }
        }

        if (!parsed) {
            return;
        }

        const auto op = static_cast<Operation>(op_byte);
        CompositeState new_state;

        if (op == Operation::Upsert) {
            new_state = compositor_->upsert_layer(slot.client_session_id, std::move(payload));
            invoke_state_changed(new_state);
        } else if (op == Operation::Remove) {
            new_state = compositor_->remove_layer(slot.client_session_id, payload.layer_id);
            invoke_state_changed(new_state);
        } else if (op == Operation::Clear) {
            new_state = compositor_->clear_client_layers(slot.client_session_id);
            invoke_state_changed(new_state);
        }
    }

    /// Handles completion events signaled for a specific pipe instance.
    void on_slot_event_signaled(PipeSlot& slot) {
        if (slot.state == PipeState::Connecting) {
            DWORD transferred = 0;
            const BOOL res = GetOverlappedResult(slot.pipe_handle.get(), &slot.overlapped, &transferred, FALSE);
            if (res || GetLastError() == ERROR_PIPE_CONNECTED) {
                slot.client_session_id = ++next_client_session_id_;
                ++active_clients_;
                slot.header_bytes_read = 0;
                start_read_header_slot(slot);
            } else {
                const DWORD err = GetLastError();
                if (err == ERROR_BROKEN_PIPE || err == ERROR_PIPE_NOT_CONNECTED) {
                    disconnect_slot(slot);
                }
            }
        } else if (slot.state == PipeState::ReadingHeader) {
            DWORD transferred = 0;
            const BOOL res = GetOverlappedResult(slot.pipe_handle.get(), &slot.overlapped, &transferred, FALSE);
            if (!res || transferred == 0) {
                disconnect_slot(slot);
            } else {
                slot.header_bytes_read += transferred;
                if (slot.header_bytes_read == kFrameHeaderSize) {
                    const uint32_t payload_len = decode_frame_length(slot.header_buf);
                    if (payload_len > kMaxPayloadSize) {
                        disconnect_slot(slot);
                    } else if (payload_len == 0) {
                        slot.header_bytes_read = 0;
                        start_read_header_slot(slot);
                    } else {
                        slot.expected_payload_len = payload_len;
                        slot.payload_buf.resize(payload_len);
                        slot.payload_bytes_read = 0;
                        start_read_payload_slot(slot);
                    }
                } else {
                    start_read_header_slot(slot);
                }
            }
        } else if (slot.state == PipeState::ReadingPayload) {
            DWORD transferred = 0;
            const BOOL res = GetOverlappedResult(slot.pipe_handle.get(), &slot.overlapped, &transferred, FALSE);
            if (!res || transferred == 0) {
                disconnect_slot(slot);
            } else {
                slot.payload_bytes_read += transferred;
                if (slot.payload_bytes_read == slot.expected_payload_len) {
                    dispatch_payload(slot);
                    slot.header_bytes_read = 0;
                    slot.payload_bytes_read = 0;
                    slot.expected_payload_len = 0;
                    start_read_header_slot(slot);
                } else {
                    start_read_payload_slot(slot);
                }
            }
        }
    }

    /// Background worker loop waiting on overlapped events and TTL deadlines.
    void worker_loop() {
        std::vector<PipeSlot> slots(instance_count_);
        std::vector<HANDLE> wait_handles;
        wait_handles.reserve(1 + instance_count_);
        wait_handles.push_back(stop_event_.get());

        bool init_ok = true;
        for (size_t i = 0; i < instance_count_; ++i) {
            slots[i].event_handle.reset(CreateEventW(nullptr, TRUE, FALSE, nullptr));
            if (!slots[i].event_handle.is_valid()) {
                init_ok = false;
                break;
            }

            slots[i].pipe_handle.reset(CreateNamedPipeW(
                pipe_name_.c_str(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                PIPE_UNLIMITED_INSTANCES,
                kPipeBufferSize,
                kPipeBufferSize,
                0,
                nullptr));

            if (!slots[i].pipe_handle.is_valid()) {
                init_ok = false;
                break;
            }

            wait_handles.push_back(slots[i].event_handle.get());
            start_connecting_slot(slots[i]);
        }

        if (!init_ok) {
            is_running_.store(false);
            SetEvent(ready_event_.get());
            return;
        }

        SetEvent(ready_event_.get());

        while (is_running_.load()) {
            const uint64_t now_before_wait = current_monotonic_tick_ms();
            uint64_t earliest_deadline = compositor_->earliest_ttl_deadline_ms();

            if (earliest_deadline > 0 && now_before_wait >= earliest_deadline) {
                const CompositeState new_state = compositor_->purge_expired_layers(now_before_wait);
                invoke_state_changed(new_state);
                earliest_deadline = compositor_->earliest_ttl_deadline_ms();
            }

            DWORD timeout_ms = INFINITE;
            if (earliest_deadline > 0) {
                const uint64_t now = current_monotonic_tick_ms();
                if (earliest_deadline <= now) {
                    timeout_ms = 0;
                } else {
                    const uint64_t delta = earliest_deadline - now;
                    timeout_ms = (delta > 0xFFFFFFFE) ? 0xFFFFFFFE : static_cast<DWORD>(delta);
                }
            }

            const auto wait_count = static_cast<DWORD>(wait_handles.size());
            const DWORD wait_res = WaitForMultipleObjects(
                wait_count,
                wait_handles.data(),
                FALSE,
                timeout_ms);

            if (wait_res == WAIT_OBJECT_0) {
                break;
            }

            if (wait_res >= WAIT_OBJECT_0 + 1 && wait_res < WAIT_OBJECT_0 + wait_count) {
                const size_t slot_idx = wait_res - (WAIT_OBJECT_0 + 1);
                on_slot_event_signaled(slots[slot_idx]);
            } else if (wait_res == WAIT_TIMEOUT) {
                const uint64_t now = current_monotonic_tick_ms();
                const CompositeState new_state = compositor_->purge_expired_layers(now);
                invoke_state_changed(new_state);
            } else {
                if (!is_running_.load()) {
                    break;
                }
            }
        }

        bool cleared_any = false;
        for (auto& slot : slots) {
            if (slot.pipe_handle.is_valid()) {
                CancelIoEx(slot.pipe_handle.get(), nullptr);
                if (slot.client_session_id != 0) {
                    compositor_->clear_client_layers(slot.client_session_id);
                    slot.client_session_id = 0;
                    cleared_any = true;
                }
                DisconnectNamedPipe(slot.pipe_handle.get());
                slot.pipe_handle.reset();
            }
            slot.event_handle.reset();
        }
        active_clients_.store(0);

        if (cleared_any) {
            invoke_state_changed(compositor_->evaluate_composite());
        }
    }

    std::wstring pipe_name_;
    std::function<void(const CompositeState&)> on_state_changed_;
    CursorStateCompositor owned_compositor_;
    CursorStateCompositor* compositor_{nullptr};
    size_t instance_count_{kDefaultInstances};

    std::atomic<bool> is_running_{false};
    std::atomic<size_t> active_clients_{0};
    std::atomic<uint64_t> next_client_session_id_{0};

    UniqueHandle stop_event_;
    UniqueHandle ready_event_;
    std::mutex callback_mutex_;
    std::thread worker_thread_;
};

} // namespace dynamic_cursor

#endif // DYNAMIC_CURSOR_IPC_SERVER_HPP