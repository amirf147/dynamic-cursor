// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Amir Farhadi

#pragma once

#ifndef DYNAMIC_CURSOR_PROTOCOL_HPP
#define DYNAMIC_CURSOR_PROTOCOL_HPP

#include <cstdint>
#include <cstring>
#include <charconv>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace dynamic_cursor {

/// Default named pipe identifier used for IPC communication.
inline constexpr const wchar_t* kDefaultPipeName = LR"(\\.\pipe\dynamic_cursor)";

/// Magic identifier for binary packets ("DCUR" in little-endian).
constexpr uint32_t kBinaryPacketMagic = 0x44435552;

/// Length prefix header size in bytes for framing.
constexpr size_t kFrameHeaderSize = 4;

/// Base cursor shapes supported by the hardware plane.
enum class BaseShape : uint8_t {
    DefaultArrow = 0,
    PrecisionCross = 1,
    ReadingMinimal = 2,
    TextIBeam = 3,
    HandPoint = 4
};

/// Corner badge sprite identifiers rendered in the lower-right quadrant.
enum class BadgeId : uint16_t {
    None = 0,
    MicOff = 1,
    MicOn = 2,
    TvIcon = 3,
    VimNormal = 4,
    WebGlobe = 5,
    CodeBrackets = 6,
    TerminalPrompt = 7,
    DocWriting = 8
};

/// Progress presentation styles.
enum class ProgressStyle : uint8_t {
    Minimal = 0,    // Attached directly to hardware cursor bitmap
    Overlay = 1,    // Floating companion overlay HUD card
    Both = 2        // Both minimal in-cursor and companion overlay card
};

/// Protocol operations for layer mutations.
enum class Operation : uint8_t {
    Upsert = 1,
    Remove = 2,
    Clear = 3
};

#pragma pack(push, 1)
/// Packed 32-byte binary protocol packet for high-frequency channels.
struct BinaryPacket {
    uint32_t magic{kBinaryPacketMagic};
    uint8_t op{static_cast<uint8_t>(Operation::Upsert)};
    uint8_t shape{0};
    uint16_t badge{0};
    int32_t priority{0};
    uint32_t tint_argb{0};
    float progress{-1.0f};
    uint32_t ttl_ms{0};
    uint32_t layer_hash{0};
    uint8_t progress_style{0};
    uint8_t reserved[3]{0, 0, 0};
};
#pragma pack(pop)

static_assert(sizeof(BinaryPacket) == 32, "BinaryPacket must be exactly 32 bytes");

/// Specification for ephemeral micro-notifications routed to the companion overlay.
struct ToastSpec {
    std::string text;
    uint32_t duration_ms{1500};
    bool follow_cursor{true};

    bool operator==(const ToastSpec&) const = default;
};

/// Decoded layer payload containing visual channels and lease tracking metadata.
struct LayerPayload {
    std::string layer_id;
    int32_t priority{0};
    BaseShape shape{BaseShape::DefaultArrow};
    bool has_shape{false};
    uint32_t tint_argb{0};
    bool has_tint{false};
    BadgeId badge{BadgeId::None};
    bool has_badge{false};
    float progress{-1.0f};
    bool has_progress{false};
    ProgressStyle progress_style{ProgressStyle::Minimal};
    bool has_progress_style{false};
    ToastSpec toast;
    bool has_toast{false};
    uint32_t ttl_ms{0};
    uint64_t client_session_id{0};
    uint64_t created_tick_ms{0};
};

/// Decodes a 4-byte little-endian unsigned integer from buffer bytes.
[[nodiscard]] constexpr uint32_t decode_frame_length(const uint8_t* header_bytes) noexcept {
    return static_cast<uint32_t>(header_bytes[0])
        | (static_cast<uint32_t>(header_bytes[1]) << 8)
        | (static_cast<uint32_t>(header_bytes[2]) << 16)
        | (static_cast<uint32_t>(header_bytes[3]) << 24);
}

/// Encodes a 4-byte little-endian unsigned integer into buffer bytes.
constexpr void encode_frame_length(uint32_t length, uint8_t* out_header_bytes) noexcept {
    out_header_bytes[0] = static_cast<uint8_t>(length & 0xFF);
    out_header_bytes[1] = static_cast<uint8_t>((length >> 8) & 0xFF);
    out_header_bytes[2] = static_cast<uint8_t>((length >> 16) & 0xFF);
    out_header_bytes[3] = static_cast<uint8_t>((length >> 24) & 0xFF);
}

/// Parses BaseShape from textual name.
[[nodiscard]] inline std::optional<BaseShape> base_shape_from_string(std::string_view name) noexcept {
    if (name == "default_arrow" || name == "arrow" || name == "default" || name == "0") {
        return BaseShape::DefaultArrow;
    }
    if (name == "precision_cross" || name == "cross" || name == "1") {
        return BaseShape::PrecisionCross;
    }
    if (name == "reading_minimal" || name == "reading" || name == "minimal" || name == "2") {
        return BaseShape::ReadingMinimal;
    }
    if (name == "text_ibeam" || name == "ibeam" || name == "text" || name == "3") {
        return BaseShape::TextIBeam;
    }
    if (name == "hand_point" || name == "hand" || name == "link" || name == "4") {
        return BaseShape::HandPoint;
    }
    return std::nullopt;
}

/// Parses BadgeId from textual name.
[[nodiscard]] inline std::optional<BadgeId> badge_id_from_string(std::string_view name) noexcept {
    if (name == "none" || name == "0" || name.empty()) {
        return BadgeId::None;
    }
    if (name == "mic_off" || name == "mic_muted" || name == "muted" || name == "1") {
        return BadgeId::MicOff;
    }
    if (name == "mic_on" || name == "listening" || name == "2") {
        return BadgeId::MicOn;
    }
    if (name == "tv_icon" || name == "tv" || name == "3") {
        return BadgeId::TvIcon;
    }
    if (name == "vim_normal" || name == "vim" || name == "4") {
        return BadgeId::VimNormal;
    }
    if (name == "web_globe" || name == "web" || name == "browser" || name == "globe" || name == "5") {
        return BadgeId::WebGlobe;
    }
    if (name == "code_brackets" || name == "code" || name == "ide" || name == "editor" || name == "6") {
        return BadgeId::CodeBrackets;
    }
    if (name == "terminal_prompt" || name == "terminal" || name == "shell" || name == "console" || name == "7") {
        return BadgeId::TerminalPrompt;
    }
    if (name == "doc_writing" || name == "writing" || name == "document" || name == "doc" || name == "prose" || name == "8") {
        return BadgeId::DocWriting;
    }
    return std::nullopt;
}

/// Parses ProgressStyle from textual name.
[[nodiscard]] inline std::optional<ProgressStyle> progress_style_from_string(std::string_view name) noexcept {
    if (name == "minimal" || name == "in_cursor" || name == "cursor" || name == "0" || name.empty()) {
        return ProgressStyle::Minimal;
    }
    if (name == "overlay" || name == "companion" || name == "hud" || name == "card" || name == "1") {
        return ProgressStyle::Overlay;
    }
    if (name == "both" || name == "all" || name == "2") {
        return ProgressStyle::Both;
    }
    return std::nullopt;
}

/// Parses Operation from textual name.
[[nodiscard]] inline std::optional<Operation> operation_from_string(std::string_view name) noexcept {
    if (name == "upsert" || name == "set" || name == "1") {
        return Operation::Upsert;
    }
    if (name == "remove" || name == "delete" || name == "drop" || name == "2") {
        return Operation::Remove;
    }
    if (name == "clear" || name == "purge" || name == "reset" || name == "3") {
        return Operation::Clear;
    }
    return std::nullopt;
}

/// Internal JSON parser representation and tokenizer without third-party dependencies.
namespace json_internal {

enum class ValueType {
    Null,
    Boolean,
    Number,
    String,
    Array,
    Object
};

struct Node {
    ValueType type{ValueType::Null};
    bool bool_val{false};
    double num_val{0.0};
    std::string str_val;
    std::vector<Node> array_val;
    std::vector<std::pair<std::string, Node>> object_val;

    [[nodiscard]] const Node* find(std::string_view key) const noexcept {
        if (type != ValueType::Object) {
            return nullptr;
        }
        for (const auto& entry : object_val) {
            if (entry.first == key) {
                return &entry.second;
            }
        }
        return nullptr;
    }
};

class MiniParser {
public:
    explicit MiniParser(std::string_view text) : text_(text), pos_(0) {}

    bool parse(Node& root) {
        skip_whitespace();
        if (pos_ >= text_.size()) {
            return false;
        }
        if (!parse_value(root)) {
            return false;
        }
        skip_whitespace();
        return pos_ == text_.size();
    }

private:
    void skip_whitespace() noexcept {
        while (pos_ < text_.size()) {
            const char c = text_[pos_];
            if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
                ++pos_;
            } else {
                break;
            }
        }
    }

    bool parse_value(Node& node) {
        skip_whitespace();
        if (pos_ >= text_.size()) {
            return false;
        }
        const char c = text_[pos_];
        if (c == '"') {
            return parse_string(node);
        }
        if (c == '{') {
            return parse_object(node);
        }
        if (c == '[') {
            return parse_array(node);
        }
        if (c == 't' || c == 'f') {
            return parse_boolean(node);
        }
        if (c == 'n') {
            return parse_null(node);
        }
        if (c == '-' || (c >= '0' && c <= '9')) {
            return parse_number(node);
        }
        return false;
    }

    bool parse_string(Node& node) {
        if (pos_ >= text_.size() || text_[pos_] != '"') {
            return false;
        }
        ++pos_;
        std::string result;
        while (pos_ < text_.size()) {
            char c = text_[pos_++];
            if (c == '"') {
                node.type = ValueType::String;
                node.str_val = std::move(result);
                return true;
            }
            if (c == '\\') {
                if (pos_ >= text_.size()) {
                    return false;
                }
                char esc = text_[pos_++];
                switch (esc) {
                    case '"': result.push_back('"'); break;
                    case '\\': result.push_back('\\'); break;
                    case '/': result.push_back('/'); break;
                    case 'b': result.push_back('\b'); break;
                    case 'f': result.push_back('\f'); break;
                    case 'n': result.push_back('\n'); break;
                    case 'r': result.push_back('\r'); break;
                    case 't': result.push_back('\t'); break;
                    case 'u': {
                        if (pos_ + 4 > text_.size()) {
                            return false;
                        }
                        uint32_t codepoint = 0;
                        for (size_t i = 0; i < 4; ++i) {
                            char h = text_[pos_++];
                            codepoint <<= 4;
                            if (h >= '0' && h <= '9') codepoint |= static_cast<uint32_t>(h - '0');
                            else if (h >= 'a' && h <= 'f') codepoint |= static_cast<uint32_t>(h - 'a' + 10);
                            else if (h >= 'A' && h <= 'F') codepoint |= static_cast<uint32_t>(h - 'A' + 10);
                            else return false;
                        }
                        if (codepoint <= 0x7F) {
                            result.push_back(static_cast<char>(codepoint));
                        } else if (codepoint <= 0x7FF) {
                            result.push_back(static_cast<char>(0xC0 | ((codepoint >> 6) & 0x1F)));
                            result.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
                        } else {
                            result.push_back(static_cast<char>(0xE0 | ((codepoint >> 12) & 0x0F)));
                            result.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3F)));
                            result.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
                        }
                        break;
                    }
                    default:
                        result.push_back(esc);
                        break;
                }
            } else {
                result.push_back(c);
            }
        }
        return false;
    }

    bool parse_number(Node& node) {
        const size_t start = pos_;
        if (text_[pos_] == '-') {
            ++pos_;
        }
        while (pos_ < text_.size() && text_[pos_] >= '0' && text_[pos_] <= '9') {
            ++pos_;
        }
        if (pos_ < text_.size() && text_[pos_] == '.') {
            ++pos_;
            while (pos_ < text_.size() && text_[pos_] >= '0' && text_[pos_] <= '9') {
                ++pos_;
            }
        }
        if (pos_ < text_.size() && (text_[pos_] == 'e' || text_[pos_] == 'E')) {
            ++pos_;
            if (pos_ < text_.size() && (text_[pos_] == '+' || text_[pos_] == '-')) {
                ++pos_;
            }
            while (pos_ < text_.size() && text_[pos_] >= '0' && text_[pos_] <= '9') {
                ++pos_;
            }
        }
        std::string_view num_view = text_.substr(start, pos_ - start);
        double val = 0.0;
        auto res = std::from_chars(num_view.data(), num_view.data() + num_view.size(), val);
        if (res.ec != std::errc{}) {
            return false;
        }
        node.type = ValueType::Number;
        node.num_val = val;
        return true;
    }

    bool parse_boolean(Node& node) {
        if (text_.substr(pos_, 4) == "true") {
            pos_ += 4;
            node.type = ValueType::Boolean;
            node.bool_val = true;
            return true;
        }
        if (text_.substr(pos_, 5) == "false") {
            pos_ += 5;
            node.type = ValueType::Boolean;
            node.bool_val = false;
            return true;
        }
        return false;
    }

    bool parse_null(Node& node) {
        if (text_.substr(pos_, 4) == "null") {
            pos_ += 4;
            node.type = ValueType::Null;
            return true;
        }
        return false;
    }

    bool parse_object(Node& node) {
        if (pos_ >= text_.size() || text_[pos_] != '{') {
            return false;
        }
        ++pos_;
        node.type = ValueType::Object;
        node.object_val.clear();

        while (pos_ < text_.size()) {
            skip_whitespace();
            if (pos_ >= text_.size()) {
                return false;
            }
            if (text_[pos_] == '}') {
                ++pos_;
                return true;
            }
            Node key_node;
            if (!parse_string(key_node)) {
                return false;
            }
            skip_whitespace();
            if (pos_ >= text_.size() || text_[pos_] != ':') {
                return false;
            }
            ++pos_;
            Node val_node;
            if (!parse_value(val_node)) {
                return false;
            }
            node.object_val.emplace_back(std::move(key_node.str_val), std::move(val_node));
            skip_whitespace();
            if (pos_ >= text_.size()) {
                return false;
            }
            if (text_[pos_] == ',') {
                ++pos_;
            } else if (text_[pos_] == '}') {
                ++pos_;
                return true;
            } else {
                return false;
            }
        }
        return false;
    }

    bool parse_array(Node& node) {
        if (pos_ >= text_.size() || text_[pos_] != '[') {
            return false;
        }
        ++pos_;
        node.type = ValueType::Array;
        node.array_val.clear();

        while (pos_ < text_.size()) {
            skip_whitespace();
            if (pos_ >= text_.size()) {
                return false;
            }
            if (text_[pos_] == ']') {
                ++pos_;
                return true;
            }
            Node item;
            if (!parse_value(item)) {
                return false;
            }
            node.array_val.push_back(std::move(item));
            skip_whitespace();
            if (pos_ >= text_.size()) {
                return false;
            }
            if (text_[pos_] == ',') {
                ++pos_;
            } else if (text_[pos_] == ']') {
                ++pos_;
                return true;
            } else {
                return false;
            }
        }
        return false;
    }

    std::string_view text_;
    size_t pos_{0};
};

/// Converts hexadecimal string or numerical value into 32-bit ARGB format.
inline bool parse_color_token(const Node* node, bool is_rgba_format, uint32_t& out_argb) noexcept {
    if (node == nullptr || node->type == ValueType::Null) {
        return false;
    }
    if (node->type == ValueType::Number) {
        const uint64_t raw = static_cast<uint64_t>(node->num_val);
        if (is_rgba_format) {
            const uint32_t r = static_cast<uint32_t>((raw >> 24) & 0xFF);
            const uint32_t g = static_cast<uint32_t>((raw >> 16) & 0xFF);
            const uint32_t b = static_cast<uint32_t>((raw >> 8) & 0xFF);
            const uint32_t a = static_cast<uint32_t>(raw & 0xFF);
            out_argb = (a << 24) | (r << 16) | (g << 8) | b;
        } else {
            out_argb = static_cast<uint32_t>(raw);
        }
        return true;
    }
    if (node->type == ValueType::String) {
        std::string_view str = node->str_val;
        if (str.starts_with("#")) {
            str.remove_prefix(1);
        } else if (str.starts_with("0x") || str.starts_with("0X")) {
            str.remove_prefix(2);
        }
        uint64_t raw = 0;
        for (char c : str) {
            raw <<= 4;
            if (c >= '0' && c <= '9') raw |= static_cast<uint64_t>(c - '0');
            else if (c >= 'a' && c <= 'f') raw |= static_cast<uint64_t>(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') raw |= static_cast<uint64_t>(c - 'A' + 10);
            else return false;
        }
        if (str.size() == 6) {
            // RRGGBB format defaults to full opacity alpha (0xFF).
            const uint32_t rgb = static_cast<uint32_t>(raw & 0x00FFFFFF);
            out_argb = 0xFF000000 | rgb;
            return true;
        }
        if (str.size() == 8) {
            if (is_rgba_format) {
                const uint32_t r = static_cast<uint32_t>((raw >> 24) & 0xFF);
                const uint32_t g = static_cast<uint32_t>((raw >> 16) & 0xFF);
                const uint32_t b = static_cast<uint32_t>((raw >> 8) & 0xFF);
                const uint32_t a = static_cast<uint32_t>(raw & 0xFF);
                out_argb = (a << 24) | (r << 16) | (g << 8) | b;
            } else {
                out_argb = static_cast<uint32_t>(raw);
            }
            return true;
        }
    }
    return false;
}

} // namespace json_internal

/// Parses a 32-byte binary protocol packet into a LayerPayload and operation.
inline bool parse_binary_frame(const uint8_t* data, size_t length, LayerPayload& out_payload, uint8_t& out_op) noexcept {
    if (data == nullptr || length < sizeof(BinaryPacket)) {
        return false;
    }
    const auto* packet = reinterpret_cast<const BinaryPacket*>(data);
    if (packet->magic != kBinaryPacketMagic) {
        return false;
    }

    out_op = packet->op == 0 ? static_cast<uint8_t>(Operation::Upsert) : packet->op;
    out_payload.priority = packet->priority;

    if (packet->shape <= static_cast<uint8_t>(BaseShape::ReadingMinimal)) {
        out_payload.shape = static_cast<BaseShape>(packet->shape);
        out_payload.has_shape = true;
    } else {
        out_payload.has_shape = false;
    }

    if (packet->tint_argb != 0) {
        out_payload.tint_argb = packet->tint_argb;
        out_payload.has_tint = true;
    } else {
        out_payload.has_tint = false;
    }

    if (packet->badge <= static_cast<uint16_t>(BadgeId::DocWriting)) {
        out_payload.badge = static_cast<BadgeId>(packet->badge);
        out_payload.has_badge = (out_payload.badge != BadgeId::None);
    } else {
        out_payload.has_badge = false;
    }

    if (packet->progress >= 0.0f) {
        out_payload.progress = packet->progress;
        out_payload.has_progress = true;
    } else {
        out_payload.progress = -1.0f;
        out_payload.has_progress = false;
    }

    if (packet->progress_style <= static_cast<uint8_t>(ProgressStyle::Both)) {
        out_payload.progress_style = static_cast<ProgressStyle>(packet->progress_style);
        out_payload.has_progress_style = true;
    } else {
        out_payload.progress_style = ProgressStyle::Minimal;
        out_payload.has_progress_style = false;
    }

    out_payload.ttl_ms = packet->ttl_ms;
    if (packet->layer_hash != 0) {
        out_payload.layer_id = "bin_" + std::to_string(packet->layer_hash);
    } else {
        out_payload.layer_id = "binary_layer";
    }
    out_payload.has_toast = false;
    return true;
}

/// Parses a UTF-8 JSON frame into a LayerPayload and operation without external dependencies.
inline bool parse_json_frame(std::string_view json_str, LayerPayload& out_payload, uint8_t& out_op) {
    json_internal::Node root;
    json_internal::MiniParser parser(json_str);
    if (!parser.parse(root) || root.type != json_internal::ValueType::Object) {
        return false;
    }

    out_op = static_cast<uint8_t>(Operation::Upsert);
    const auto* op_node = root.find("op");
    if (op_node == nullptr) {
        op_node = root.find("action");
    }
    if (op_node != nullptr) {
        if (op_node->type == json_internal::ValueType::String) {
            auto parsed_op = operation_from_string(op_node->str_val);
            if (parsed_op.has_value()) {
                out_op = static_cast<uint8_t>(*parsed_op);
            }
        } else if (op_node->type == json_internal::ValueType::Number) {
            out_op = static_cast<uint8_t>(op_node->num_val);
        }
    }

    const auto* id_node = root.find("layer_id");
    if (id_node == nullptr) {
        id_node = root.find("layer");
    }
    if (id_node != nullptr && id_node->type == json_internal::ValueType::String) {
        out_payload.layer_id = id_node->str_val;
    } else {
        out_payload.layer_id = "default";
    }

    const auto* prio_node = root.find("priority");
    if (prio_node != nullptr && prio_node->type == json_internal::ValueType::Number) {
        out_payload.priority = static_cast<int32_t>(prio_node->num_val);
    } else {
        out_payload.priority = 0;
    }

    out_payload.has_shape = false;
    const auto* shape_node = root.find("base_shape");
    if (shape_node == nullptr) {
        shape_node = root.find("shape");
    }
    if (shape_node != nullptr && shape_node->type != json_internal::ValueType::Null) {
        if (shape_node->type == json_internal::ValueType::String) {
            auto parsed_shape = base_shape_from_string(shape_node->str_val);
            if (parsed_shape.has_value()) {
                out_payload.shape = *parsed_shape;
                out_payload.has_shape = true;
            }
        } else if (shape_node->type == json_internal::ValueType::Number) {
            const auto s = static_cast<uint8_t>(shape_node->num_val);
            if (s <= static_cast<uint8_t>(BaseShape::HandPoint)) {
                out_payload.shape = static_cast<BaseShape>(s);
                out_payload.has_shape = true;
            }
        }
    }

    out_payload.has_tint = false;
    const auto* tint_argb_node = root.find("tint_argb");
    if (tint_argb_node != nullptr) {
        uint32_t argb = 0;
        if (json_internal::parse_color_token(tint_argb_node, false, argb)) {
            out_payload.tint_argb = argb;
            out_payload.has_tint = true;
        }
    } else {
        const auto* tint_rgba_node = root.find("tint_rgba");
        if (tint_rgba_node == nullptr) {
            tint_rgba_node = root.find("tint");
        }
        if (tint_rgba_node != nullptr) {
            uint32_t argb = 0;
            if (json_internal::parse_color_token(tint_rgba_node, true, argb)) {
                out_payload.tint_argb = argb;
                out_payload.has_tint = true;
            }
        }
    }

    out_payload.has_badge = false;
    const auto* badge_node = root.find("badge_id");
    if (badge_node == nullptr) {
        badge_node = root.find("corner_badge");
    }
    if (badge_node == nullptr) {
        badge_node = root.find("badge");
    }
    if (badge_node != nullptr && badge_node->type != json_internal::ValueType::Null) {
        if (badge_node->type == json_internal::ValueType::String) {
            auto parsed_badge = badge_id_from_string(badge_node->str_val);
            if (parsed_badge.has_value()) {
                out_payload.badge = *parsed_badge;
                out_payload.has_badge = (out_payload.badge != BadgeId::None);
            }
        } else if (badge_node->type == json_internal::ValueType::Number) {
            const auto b = static_cast<uint16_t>(badge_node->num_val);
            if (b <= static_cast<uint16_t>(BadgeId::DocWriting)) {
                out_payload.badge = static_cast<BadgeId>(b);
                out_payload.has_badge = (out_payload.badge != BadgeId::None);
            }
        }
    }

    out_payload.has_progress = false;
    out_payload.progress = -1.0f;
    const auto* prog_node = root.find("progress");
    if (prog_node == nullptr) {
        prog_node = root.find("progress_bar");
    }
    if (prog_node == nullptr) {
        prog_node = root.find("progress_ratio");
    }
    if (prog_node != nullptr && prog_node->type == json_internal::ValueType::Number) {
        out_payload.progress = static_cast<float>(prog_node->num_val);
        out_payload.has_progress = (out_payload.progress >= 0.0f);
    }

    out_payload.has_progress_style = false;
    out_payload.progress_style = ProgressStyle::Minimal;
    const auto* prog_style_node = root.find("progress_style");
    if (prog_style_node == nullptr) {
        prog_style_node = root.find("progress_mode");
    }
    if (prog_style_node != nullptr) {
        if (prog_style_node->type == json_internal::ValueType::String) {
            auto parsed_style = progress_style_from_string(prog_style_node->str_val);
            if (parsed_style.has_value()) {
                out_payload.progress_style = *parsed_style;
                out_payload.has_progress_style = true;
            }
        } else if (prog_style_node->type == json_internal::ValueType::Number) {
            const auto ps = static_cast<uint8_t>(prog_style_node->num_val);
            if (ps <= static_cast<uint8_t>(ProgressStyle::Both)) {
                out_payload.progress_style = static_cast<ProgressStyle>(ps);
                out_payload.has_progress_style = true;
            }
        }
    }

    const auto* ttl_node = root.find("ttl_ms");
    if (ttl_node != nullptr && ttl_node->type == json_internal::ValueType::Number) {
        out_payload.ttl_ms = static_cast<uint32_t>(ttl_node->num_val);
    } else {
        out_payload.ttl_ms = 0;
    }

    out_payload.has_toast = false;
    const auto* toast_node = root.find("toast");
    if (toast_node != nullptr && toast_node->type != json_internal::ValueType::Null) {
        if (toast_node->type == json_internal::ValueType::Object) {
            const auto* text_node = toast_node->find("text");
            if (text_node != nullptr && text_node->type == json_internal::ValueType::String) {
                out_payload.toast.text = text_node->str_val;
                out_payload.has_toast = !out_payload.toast.text.empty();
            }
            const auto* dur_node = toast_node->find("duration_ms");
            if (dur_node != nullptr && dur_node->type == json_internal::ValueType::Number) {
                out_payload.toast.duration_ms = static_cast<uint32_t>(dur_node->num_val);
            } else {
                out_payload.toast.duration_ms = 1500;
            }
            const auto* follow_node = toast_node->find("follow_cursor");
            if (follow_node != nullptr && follow_node->type == json_internal::ValueType::Boolean) {
                out_payload.toast.follow_cursor = follow_node->bool_val;
            } else {
                out_payload.toast.follow_cursor = true;
            }
        } else if (toast_node->type == json_internal::ValueType::String) {
            out_payload.toast.text = toast_node->str_val;
            out_payload.toast.duration_ms = 1500;
            out_payload.toast.follow_cursor = true;
            out_payload.has_toast = !out_payload.toast.text.empty();
        }
    }

    return true;
}

} // namespace dynamic_cursor

#endif // DYNAMIC_CURSOR_PROTOCOL_HPP
