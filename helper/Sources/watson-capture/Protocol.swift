import Foundation

/// Stable TCC / codesign identifier. Must match Info.plist `CFBundleIdentifier`
/// and `codesign --identifier` (see `helper/scripts/sign.sh`).
public let helperBundleID = "com.watson.capture"
public let helperVersion = "0.1.0"

// MARK: - Stdin commands (line-delimited JSON)

/// Control messages the supervisor (or a human) writes to stdin, one JSON object per line.
///
/// ```
/// {"cmd":"pause"}
/// {"cmd":"resume"}
/// {"cmd":"status"}
/// {"cmd":"quit"}
/// ```
///
/// Extra keys (e.g. `duration_minutes`) are ignored: the helper stays paused until
/// `resume` or process exit. Timed pause is a supervisor concern.
public enum ControlCommand: String, Equatable, Sendable {
    case pause
    case resume
    case status
    case quit
}

public enum CommandParseError: Error, Equatable, Sendable, CustomStringConvertible {
    case empty
    case invalidJSON
    case missingCmd
    case unknownCommand(String)

    public var description: String {
        switch self {
        case .empty:
            return "empty line"
        case .invalidJSON:
            return "line is not a JSON object"
        case .missingCmd:
            return "missing cmd"
        case .unknownCommand(let name):
            return "unknown cmd '\(name)'"
        }
    }
}

public enum CommandParser {
    /// Parse one stdin line. Blank / whitespace-only lines are `.empty`.
    public static func parse(_ line: String) -> Result<ControlCommand, CommandParseError> {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            return .failure(.empty)
        }
        guard let data = trimmed.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return .failure(.invalidJSON)
        }
        guard let raw = object["cmd"] as? String ?? object["command"] as? String else {
            return .failure(.missingCmd)
        }
        let name = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard let command = ControlCommand(rawValue: name) else {
            return .failure(.unknownCommand(name))
        }
        return .success(command)
    }
}

// MARK: - Stdout events (line-delimited JSON)

public enum EventJSON {
    /// ISO-8601 UTC timestamps used on every event and in sidecars.
    public static func timestamp(_ date: Date = Date()) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        return formatter.string(from: date)
    }

    /// Encode one stdout event. Always includes `event` and `ts`. Sorted keys, no pretty-print
    /// (one object per line).
    public static func line(event: String, fields: [String: Any] = [:], at date: Date = Date()) -> String {
        var object = fields
        object["event"] = event
        object["ts"] = timestamp(date)
        guard JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else {
            return #"{"event":"error","message":"event encode failed"}"# + "\n"
        }
        return text + "\n"
    }
}

public enum StdoutEmitter {
    private static let lock = NSLock()

    public static func emit(_ line: String) {
        let payload = line.hasSuffix("\n") ? line : line + "\n"
        guard let data = payload.data(using: .utf8) else { return }
        lock.lock()
        FileHandle.standardOutput.write(data)
        lock.unlock()
    }

    public static func event(_ name: String, fields: [String: Any] = [:], at date: Date = Date()) {
        emit(EventJSON.line(event: name, fields: fields, at: date))
    }
}

public enum StderrLog {
    public static func line(_ message: String) {
        let payload = "watson-capture: \(message)\n"
        if let data = payload.data(using: .utf8) {
            FileHandle.standardError.write(data)
        }
    }
}

// MARK: - Sidecar schema (§3.1)

/// Frame sidecar written next to each PNG (`<frame-id>.json`).
///
/// Schema (Revision 2 §3.1). Python later reads this as-is.
///
/// | field | type | notes |
/// |---|---|---|
/// | `frame_id` | string | lowercase UUID |
/// | `captured_at` | string | ISO-8601 UTC |
/// | `display_id` | number \| null | `CGDirectDisplayID` of the display that owns the window |
/// | `bundle_id` | string | frontmost application |
/// | `app_name` | string | localized name, best effort |
/// | `window_title` | string | empty if TCC/Accessibility withholds it |
/// | `window_id` | number | `CGWindowID` |
/// | `is_private_browsing` | bool | best effort from the title (`Private Browsing` / `Incognito` / `InPrivate`) |
/// | `url` | string \| null | always null in M0-B (Accessibility deferred, plan decision 6) |
/// | `ocr.lines[]` | array | `{text, bbox, confidence}` |
/// | `ocr.lines[].bbox` | `[x_min, y_min, x_max, y_max]` | integer pixel coordinates, top-left origin |
/// | `phash` | string | 16 lowercase hex chars, 64-bit average hash |
/// | `width`, `height` | number | PNG pixel size |
/// | `capture_mode` | string | `"frontmost"` (D1) or `"selftest"` |
public struct FrameSidecar: Codable, Equatable, Sendable {
    public var frame_id: String
    public var captured_at: String
    public var display_id: UInt32?
    public var bundle_id: String
    public var app_name: String
    public var window_title: String
    public var window_id: UInt32
    public var is_private_browsing: Bool
    public var url: String?
    public var ocr: OCRPayload
    public var phash: String
    public var width: Int
    public var height: Int
    public var capture_mode: String

    public init(
        frame_id: String,
        captured_at: String,
        display_id: UInt32?,
        bundle_id: String,
        app_name: String,
        window_title: String,
        window_id: UInt32,
        is_private_browsing: Bool,
        url: String?,
        ocr: OCRPayload,
        phash: String,
        width: Int,
        height: Int,
        capture_mode: String
    ) {
        self.frame_id = frame_id
        self.captured_at = captured_at
        self.display_id = display_id
        self.bundle_id = bundle_id
        self.app_name = app_name
        self.window_title = window_title
        self.window_id = window_id
        self.is_private_browsing = is_private_browsing
        self.url = url
        self.ocr = ocr
        self.phash = phash
        self.width = width
        self.height = height
        self.capture_mode = capture_mode
    }
}

public struct OCRPayload: Codable, Equatable, Sendable {
    public var lines: [OCRLine]

    public init(lines: [OCRLine]) {
        self.lines = lines
    }
}

public struct OCRLine: Codable, Equatable, Sendable {
    public var text: String
    /// `[x_min, y_min, x_max, y_max]` in pixels, top-left origin. Matches `tools/bench` fixtures.
    public var bbox: [Int]
    public var confidence: Double

    public init(text: String, bbox: [Int], confidence: Double) {
        self.text = text
        self.bbox = bbox
        self.confidence = confidence
    }
}

public enum SidecarJSON {
    public static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }()

    public static let decoder = JSONDecoder()

    public static func encode(_ sidecar: FrameSidecar) throws -> Data {
        try encoder.encode(sidecar)
    }

    public static func decode(_ data: Data) throws -> FrameSidecar {
        try decoder.decode(FrameSidecar.self, from: data)
    }
}

public enum PrivateBrowsing {
    public static func detect(windowTitle: String) -> Bool {
        let title = windowTitle.lowercased()
        return title.contains("private browsing")
            || title.contains("incognito")
            || title.contains("inprivate")
    }
}
