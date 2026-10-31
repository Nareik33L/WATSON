import CoreGraphics
import Foundation

/// Pre-filter skip reasons (§3.2), plus helper-only reasons that still must not write a frame.
public enum SkipReason: String, Equatable, Sendable {
    case paused
    case locked
    case displayAsleep = "display_asleep"
    case idle
    case excludedApp = "excluded_app"
    case excludedTitle = "excluded_title"
    case sharingNone = "sharing_none"
    case duplicate
    case noWindow = "no_window"
    case notGranted = "screen_recording_denied"
}

public enum FilterDecision: Equatable, Sendable {
    case capture
    case skip(SkipReason)
}

/// Immutable snapshot evaluated by `FilterEngine`. No system calls — cheap to unit-test.
public struct FilterSnapshot: Equatable, Sendable {
    public var isPaused: Bool
    public var isLocked: Bool
    public var isDisplayAsleep: Bool
    public var idleSeconds: Double
    public var bundleID: String
    public var windowTitle: String
    public var sharingState: Int
    public var phash: UInt64?
    public var previousPhash: UInt64?

    public init(
        isPaused: Bool = false,
        isLocked: Bool = false,
        isDisplayAsleep: Bool = false,
        idleSeconds: Double = 0,
        bundleID: String = "",
        windowTitle: String = "",
        sharingState: Int = 1,
        phash: UInt64? = nil,
        previousPhash: UInt64? = nil
    ) {
        self.isPaused = isPaused
        self.isLocked = isLocked
        self.isDisplayAsleep = isDisplayAsleep
        self.idleSeconds = idleSeconds
        self.bundleID = bundleID
        self.windowTitle = windowTitle
        self.sharingState = sharingState
        self.phash = phash
        self.previousPhash = previousPhash
    }
}

public struct FilterConfig: Equatable, Sendable {
    public var idleThresholdSeconds: Double
    public var phashDistance: Int
    public var excludedBundleIDs: Set<String>
    public var titlePatterns: [String]

    public init(
        idleThresholdSeconds: Double = 180,
        phashDistance: Int = 4,
        excludedBundleIDs: Set<String> = DefaultExclusions.bundleIDs,
        titlePatterns: [String] = DefaultExclusions.titlePatterns
    ) {
        self.idleThresholdSeconds = idleThresholdSeconds
        self.phashDistance = phashDistance
        self.excludedBundleIDs = excludedBundleIDs
        self.titlePatterns = titlePatterns
    }

    public static let `default` = FilterConfig()

    public func adding(bundleIDs: [String]) -> FilterConfig {
        var copy = self
        copy.excludedBundleIDs.formUnion(bundleIDs)
        return copy
    }
}

/// Starter exclusion lists from §5.1. Banking apps are too vendor-specific to enumerate here;
/// title patterns catch sign-in / private-browsing windows instead.
public enum DefaultExclusions {
    public static let bundleIDs: Set<String> = [
        "com.1password.1password",
        "com.1password.1password-mac",
        "com.agilebits.onepassword7",
        "com.apple.FaceTime",
        "com.apple.Health",
        "com.apple.MobileSMS",
        "com.apple.Passwords",
        "com.apple.Photos",
        "com.apple.keychainaccess",
        "com.bitwarden.desktop",
        "com.dashlane.dashlanephonegeneration",
        "com.lastpass.LastPass",
    ]

    public static let titlePatterns: [String] = [
        "Private Browsing",
        "Incognito",
        "InPrivate",
        "1Password",
        "Sign in",
        "Sign-in",
        "Sign In",
        "Verification code",
        "Verification Code",
        "password",
    ]
}

public enum FilterEngine {
    /// §3.2 filters 1–2 only (paused / locked / display asleep / idle). Cheap enough
    /// to run before window resolution and again after capture, before any write.
    public static func evaluateImmediate(_ snapshot: FilterSnapshot, config: FilterConfig = .default) -> FilterDecision {
        if snapshot.isPaused { return .skip(.paused) }
        if snapshot.isLocked { return .skip(.locked) }
        if snapshot.isDisplayAsleep { return .skip(.displayAsleep) }
        if snapshot.idleSeconds > config.idleThresholdSeconds { return .skip(.idle) }
        return .capture
    }

    /// §3.2 filters 1–5 (and helper-only `no_window`). Filter 6 (phash) is `evaluateDuplicate`.
    /// Work-hours are not evaluated here: the supervisor pauses the helper outside the schedule.
    public static func evaluatePreCapture(_ snapshot: FilterSnapshot, config: FilterConfig = .default) -> FilterDecision {
        if case .skip(let reason) = evaluateImmediate(snapshot, config: config) {
            return .skip(reason)
        }
        if snapshot.bundleID.isEmpty { return .skip(.noWindow) }
        if config.excludedBundleIDs.contains(snapshot.bundleID) { return .skip(.excludedApp) }
        if titleIsExcluded(snapshot.windowTitle, patterns: config.titlePatterns) { return .skip(.excludedTitle) }
        if snapshot.sharingState == 0 { return .skip(.sharingNone) }
        return .capture
    }

    /// §3.2 filter 6: Hamming distance ≤ 4 against the previous frame from the same window.
    public static func evaluateDuplicate(_ snapshot: FilterSnapshot, config: FilterConfig = .default) -> FilterDecision {
        guard let current = snapshot.phash, let previous = snapshot.previousPhash else {
            return .capture
        }
        if PerceptualHash.shouldSkipDuplicate(
            current: current,
            previous: previous,
            threshold: config.phashDistance
        ) {
            return .skip(.duplicate)
        }
        return .capture
    }

    public static func titleIsExcluded(_ title: String, patterns: [String]) -> Bool {
        for pattern in patterns where !pattern.isEmpty {
            if title.range(of: pattern, options: [.caseInsensitive, .diacriticInsensitive]) != nil {
                return true
            }
        }
        return false
    }
}

/// 64-bit average hash. Cheap enough to run in-process on every captured frame.
public enum PerceptualHash {
    public static func hammingDistance(_ a: UInt64, _ b: UInt64) -> Int {
        (a ^ b).nonzeroBitCount
    }

    public static func shouldSkipDuplicate(current: UInt64, previous: UInt64, threshold: Int = 4) -> Bool {
        hammingDistance(current, previous) <= threshold
    }

    public static func hex(_ value: UInt64) -> String {
        String(format: "%016llx", value)
    }

    public static func fromHex(_ string: String) -> UInt64? {
        let trimmed = string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count == 16, let value = UInt64(trimmed, radix: 16) else {
            return nil
        }
        return value
    }

    /// 8×8 grayscale mean-threshold hash. Returns 0 if the image cannot be sampled.
    public static func hash(image: CGImage) -> UInt64 {
        let width = 8
        let height = 8
        var pixels = [UInt8](repeating: 0, count: width * height)
        let colorSpace = CGColorSpaceCreateDeviceGray()
        let bitmapInfo = CGBitmapInfo.byteOrderDefault.union(CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue))
        guard let context = CGContext(
            data: &pixels,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width,
            space: colorSpace,
            bitmapInfo: bitmapInfo.rawValue
        ) else {
            return 0
        }
        context.interpolationQuality = .low
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))

        var total = 0
        for pixel in pixels {
            total += Int(pixel)
        }
        let mean = total / pixels.count

        var bits: UInt64 = 0
        for (index, pixel) in pixels.enumerated() {
            if Int(pixel) >= mean {
                bits |= 1 << (63 - index)
            }
        }
        return bits
    }
}
