import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

// MARK: - CLI

public struct CLIOptions: Equatable, Sendable {
    public var interval: TimeInterval = 45
    public var out: URL?
    public var selftest = false
    public var preflight = false
    public var selfReportInterval: TimeInterval = 60
    public var idleSeconds: TimeInterval = 180
    public var extraExclusions: [String] = []

    public var filterConfig: FilterConfig {
        FilterConfig(idleThresholdSeconds: idleSeconds).adding(bundleIDs: extraExclusions)
    }

    public var spoolURL: URL {
        out ?? CommandLineApp.defaultSpoolURL()
    }
}

public enum CommandLineApp {
    public static func defaultSpoolURL() -> URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/WATSON/spool", isDirectory: true)
    }

    public static func main() async {
        let argv = Array(CommandLine.arguments.dropFirst())
        if argv.contains("-h") || argv.contains("--help") {
            FileHandle.standardOutput.write(Data(helpText.utf8))
            return
        }
        if argv.contains("-v") || argv.contains("--version") {
            FileHandle.standardOutput.write(Data("watson-capture \(helperVersion)\n".utf8))
            return
        }

        let options: CLIOptions
        do {
            options = try parse(argv)
        } catch {
            StderrLog.line(error.localizedDescription)
            Darwin.exit(2)
        }

        if options.preflight {
            Darwin.exit(runPreflight())
        }
        if options.selftest {
            Darwin.exit(runSelftest(options: options))
        }
        await runCapture(options: options)
    }

    public static func parse(_ argv: [String]) throws -> CLIOptions {
        var options = CLIOptions()
        var index = 0
        while index < argv.count {
            let arg = argv[index]
            func takeValue() throws -> String {
                let next = index + 1
                guard next < argv.count else {
                    throw CLIError.missingValue(arg)
                }
                index = next
                return argv[next]
            }
            switch arg {
            case "--interval":
                options.interval = try parseInterval(takeValue(), flag: arg)
            case "--out":
                options.out = URL(fileURLWithPath: try takeValue(), isDirectory: true)
            case "--selftest":
                options.selftest = true
            case "--preflight":
                options.preflight = true
            case "--self-report-interval":
                options.selfReportInterval = try parseInterval(takeValue(), flag: arg)
            case "--idle-seconds":
                options.idleSeconds = try parseInterval(takeValue(), flag: arg)
            case "--exclude-bundle":
                options.extraExclusions.append(try takeValue())
            default:
                throw CLIError.unknownFlag(arg)
            }
            index += 1
        }
        return options
    }

    private static func parseInterval(_ raw: String, flag: String) throws -> TimeInterval {
        guard let value = Double(raw), value > 0 else {
            throw CLIError.invalidValue(flag, raw)
        }
        return value
    }

    public static let helpText = """
    watson-capture — WATSON frontmost-window capture helper (M0-B)

    Usage:
      watson-capture [options]

    Options:
      --interval <seconds>             Capture period (default: 45)
      --out <dir>                      Spool directory (default: ~/Library/Application Support/WATSON/spool)
      --selftest                       Prove pause emits no frames; no ScreenCaptureKit
      --preflight                      Print Screen Recording grant and exit (no capture)
      --self-report-interval <seconds> CPU/RSS event period (default: 60)
      --idle-seconds <seconds>         Idle skip threshold (default: 180)
      --exclude-bundle <id>            Extra excluded bundle ID (repeatable)
      --version
      --help

    Stdin (one JSON object per line):
      {"cmd":"pause"}
      {"cmd":"resume"}
      {"cmd":"status"}
      {"cmd":"quit"}

    Stdout: one JSON event per line. See helper/README.md.

    """
}

private enum CLIError: Error, LocalizedError {
    case unknownFlag(String)
    case missingValue(String)
    case invalidValue(String, String)

    var errorDescription: String? {
        switch self {
        case .unknownFlag(let flag):
            return "unknown flag \(flag)"
        case .missingValue(let flag):
            return "\(flag) requires a value"
        case .invalidValue(let flag, let value):
            return "\(flag) has invalid value '\(value)'"
        }
    }
}

// MARK: - Mutable run state

final class RunState: @unchecked Sendable {
    private let lock = NSLock()
    private var paused = false
    private var quitRequested = false
    private var lastPhash: [UInt32: UInt64] = [:]
    private var framesWritten = 0
    private var framesSkipped = 0
    private var duplicates = 0
    private var lastSkip: String?
    private var signalSources: [DispatchSourceSignal] = []

    func isPaused() -> Bool {
        lock.lock(); defer { lock.unlock() }
        return paused
    }

    func setPaused(_ value: Bool) {
        lock.lock(); defer { lock.unlock() }
        paused = value
    }

    func requestQuit() {
        lock.lock(); defer { lock.unlock() }
        quitRequested = true
    }

    func shouldQuit() -> Bool {
        lock.lock(); defer { lock.unlock() }
        return quitRequested
    }

    func previousPhash(windowID: UInt32) -> UInt64? {
        lock.lock(); defer { lock.unlock() }
        return lastPhash[windowID]
    }

    func remember(windowID: UInt32, phash: UInt64) {
        lock.lock(); defer { lock.unlock() }
        lastPhash[windowID] = phash
    }

    func markWritten() {
        lock.lock(); defer { lock.unlock() }
        framesWritten += 1
    }

    func markSkipped(_ reason: SkipReason) {
        lock.lock(); defer { lock.unlock() }
        framesSkipped += 1
        lastSkip = reason.rawValue
        if reason == .duplicate {
            duplicates += 1
        }
    }

    func snapshot() -> (paused: Bool, written: Int, skipped: Int, duplicates: Int, lastSkip: String?) {
        lock.lock(); defer { lock.unlock() }
        return (paused, framesWritten, framesSkipped, duplicates, lastSkip)
    }

    func keep(signalSource: DispatchSourceSignal) {
        lock.lock(); defer { lock.unlock() }
        signalSources.append(signalSource)
    }
}

// MARK: - System probes (lock / sleep / idle)

enum SystemProbes {
    /// `CGSessionCopyCurrentDictionary` → `CGSSessionScreenIsLocked` (§3.2).
    static func isScreenLocked() -> Bool {
        guard let cfDict = CGSessionCopyCurrentDictionary() else {
            return false
        }
        let dict = cfDict as NSDictionary
        if let locked = dict["CGSSessionScreenIsLocked"] as? Bool {
            return locked
        }
        if let number = dict["CGSSessionScreenIsLocked"] as? NSNumber {
            return number.boolValue
        }
        return false
    }

    static func isDisplayAsleep() -> Bool {
        CGDisplayIsAsleep(CGMainDisplayID()) != 0
    }

    /// Seconds since any HID event (`CGEventType` raw value `~0`).
    static func idleSeconds() -> Double {
        let anyEvent = CGEventType(rawValue: ~UInt32(0)) ?? .null
        return CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: anyEvent)
    }
}

/// Flags flipped by `NSWorkspace` notifications (plan §13 sleep/wake risk). Polled every tick too.
final class WorkspaceFlags: @unchecked Sendable {
    private let lock = NSLock()
    private var displayAsleep = false
    private var observers: [NSObjectProtocol] = []

    func start() {
        let center = NSWorkspace.shared.notificationCenter
        let pairs: [(NSNotification.Name, Bool)] = [
            (NSWorkspace.screensDidSleepNotification, true),
            (NSWorkspace.screensDidWakeNotification, false),
            (NSWorkspace.willSleepNotification, true),
            (NSWorkspace.didWakeNotification, false),
        ]
        for (name, asleep) in pairs {
            let token = center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.setDisplayAsleep(asleep)
                StdoutEmitter.event(asleep ? "display_asleep" : "display_awake")
            }
            observers.append(token)
        }
        let lockToken = center.addObserver(
            forName: NSWorkspace.sessionDidResignActiveNotification,
            object: nil,
            queue: .main
        ) { _ in
            StdoutEmitter.event("session_resigned")
        }
        let unlockToken = center.addObserver(
            forName: NSWorkspace.sessionDidBecomeActiveNotification,
            object: nil,
            queue: .main
        ) { _ in
            StdoutEmitter.event("session_active")
        }
        observers.append(lockToken)
        observers.append(unlockToken)
    }

    func setDisplayAsleep(_ value: Bool) {
        lock.lock(); defer { lock.unlock() }
        displayAsleep = value
    }

    func isDisplayAsleep() -> Bool {
        lock.lock(); defer { lock.unlock() }
        return displayAsleep
    }
}

// MARK: - Frontmost window (D1)

struct FrontmostWindow: Sendable {
    var windowID: UInt32
    var bundleID: String
    var appName: String
    var title: String
    var sharingState: Int
    var displayID: UInt32
    var pixelWidth: Int
    var pixelHeight: Int
}

enum WindowCapture {
    enum CaptureFailure: Error, CustomStringConvertible {
        case noFrontmostApp
        case noWindow
        case screenshotFailed
        case pngEncodeFailed

        var description: String {
            switch self {
            case .noFrontmostApp: return "no frontmost application"
            case .noWindow: return "no frontmost window"
            case .screenshotFailed: return "ScreenCaptureKit screenshot failed"
            case .pngEncodeFailed: return "PNG encode failed"
            }
        }
    }

    static func resolveFrontmost() async throws -> FrontmostWindow {
        let app = await MainActor.run { NSWorkspace.shared.frontmostApplication }
        guard let app else { throw CaptureFailure.noFrontmostApp }

        let pid = app.processIdentifier
        let bundleID = app.bundleIdentifier ?? ""
        let appName = app.localizedName ?? bundleID

        guard let cgWindow = topWindow(ownerPID: pid) else {
            throw CaptureFailure.noWindow
        }

        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let scWindow = content.windows.first(where: { $0.windowID == cgWindow.id })
            ?? content.windows.first(where: { $0.owningApplication?.processID == pid && $0.isOnScreen })

        let title = (scWindow?.title?.isEmpty == false ? scWindow?.title : nil)
            ?? cgWindow.title
        let frame = scWindow?.frame ?? cgWindow.bounds
        let (scale, display) = await MainActor.run { () -> (CGFloat, UInt32) in
            (NSScreen.main?.backingScaleFactor ?? 2.0, displayID(containing: frame))
        }

        return FrontmostWindow(
            windowID: cgWindow.id,
            bundleID: scWindow?.owningApplication?.bundleIdentifier ?? bundleID,
            appName: scWindow?.owningApplication?.applicationName ?? appName,
            title: title,
            sharingState: cgWindow.sharingState,
            displayID: display,
            pixelWidth: max(1, Int((frame.width * scale).rounded())),
            pixelHeight: max(1, Int((frame.height * scale).rounded()))
        )
    }

    static func screenshot(of window: FrontmostWindow) async throws -> CGImage {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let scWindow = content.windows.first(where: { $0.windowID == window.windowID }) else {
            throw CaptureFailure.noWindow
        }
        let filter = SCContentFilter(desktopIndependentWindow: scWindow)
        let configuration = SCStreamConfiguration()
        configuration.width = window.pixelWidth
        configuration.height = window.pixelHeight
        configuration.showsCursor = false
        configuration.scalesToFit = true
        return try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<CGImage, Error>) in
            SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            ) { image, error in
                if let image {
                    continuation.resume(returning: image)
                } else {
                    continuation.resume(throwing: error ?? CaptureFailure.screenshotFailed)
                }
            }
        }
    }

    private struct CGWindowRecord {
        var id: UInt32
        var title: String
        var sharingState: Int
        var bounds: CGRect
    }

    private static func topWindow(ownerPID: pid_t) -> CGWindowRecord? {
        let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
        guard let raw = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
            return nil
        }
        for info in raw {
            let owner = (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value
            let layer = (info[kCGWindowLayer as String] as? NSNumber)?.intValue ?? -1
            guard owner == ownerPID, layer == 0 else { continue }
            let id = (info[kCGWindowNumber as String] as? NSNumber)?.uint32Value ?? 0
            let title = info[kCGWindowName as String] as? String ?? ""
            let sharing = (info[kCGWindowSharingState as String] as? NSNumber)?.intValue ?? 1
            let bounds = cgBounds(info[kCGWindowBounds as String])
            return CGWindowRecord(id: id, title: title, sharingState: sharing, bounds: bounds)
        }
        return nil
    }

    private static func cgBounds(_ value: Any?) -> CGRect {
        guard let dict = value as? NSDictionary else { return .zero }
        var rect = CGRect.zero
        if !CGRectMakeWithDictionaryRepresentation(dict, &rect) {
            return .zero
        }
        return rect
    }

    private static func displayID(containing rect: CGRect) -> UInt32 {
        let screens = NSScreen.screens
        for screen in screens where screen.frame.intersects(rect) {
            if let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber {
                return number.uint32Value
            }
        }
        return CGMainDisplayID()
    }
}

enum PNGWriter {
    static func data(from image: CGImage) -> Data? {
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            data,
            UTType.png.identifier as CFString,
            1,
            nil
        ) else {
            return nil
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return data as Data
    }

    /// 8×8 gray synthetic frame for `--selftest` (no ScreenCaptureKit).
    static func selftestImage() -> CGImage? {
        var pixels = [UInt8](repeating: 180, count: 64)
        for i in 0..<64 where i % 2 == 0 {
            pixels[i] = 40
        }
        let colorSpace = CGColorSpaceCreateDeviceGray()
        guard let context = CGContext(
            data: &pixels,
            width: 8,
            height: 8,
            bitsPerComponent: 8,
            bytesPerRow: 8,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        ) else {
            return nil
        }
        return context.makeImage()
    }
}

// MARK: - Spool I/O

enum Spool {
    static func prepare(_ root: URL) throws {
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var mutable = root
        try mutable.setResourceValues(values)
    }

    static func dayDirectory(root: URL, now: Date = Date()) throws -> URL {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        let url = root.appendingPathComponent(formatter.string(from: now), isDirectory: true)
        try prepare(url)
        return url
    }

    static func write(png: Data, sidecar: FrameSidecar, root: URL) throws -> (png: URL, json: URL) {
        let day = try dayDirectory(root: root)
        let pngURL = day.appendingPathComponent("\(sidecar.frame_id).png")
        let jsonURL = day.appendingPathComponent("\(sidecar.frame_id).json")
        try png.write(to: pngURL, options: .atomic)
        try SidecarJSON.encode(sidecar).write(to: jsonURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: pngURL.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: jsonURL.path)
        return (pngURL, jsonURL)
    }
}

// MARK: - CPU / RSS self-report

public enum ProcessMeter {
    public static func cpuSeconds() -> Double {
        var usage = rusage()
        getrusage(RUSAGE_SELF, &usage)
        let user = Double(usage.ru_utime.tv_sec) + Double(usage.ru_utime.tv_usec) / 1_000_000
        let system = Double(usage.ru_stime.tv_sec) + Double(usage.ru_stime.tv_usec) / 1_000_000
        return user + system
    }

    public static func rssBytes() -> UInt64 {
        // macOS: ru_maxrss is peak resident bytes. Avoids mach_task_self_ (not Sendable in Swift 6).
        var usage = rusage()
        getrusage(RUSAGE_SELF, &usage)
        return UInt64(usage.ru_maxrss)
    }

    public static func reportFields(
        startedAt: Date,
        lastCPU: Double,
        lastWall: Date,
        now: Date = Date()
    ) -> (fields: [String: Any], cpu: Double, wall: Date) {
        let cpu = cpuSeconds()
        let uptime = max(now.timeIntervalSince(startedAt), 0.001)
        let interval = max(now.timeIntervalSince(lastWall), 0.001)
        let avg = (cpu / uptime) * 100
        let recent = ((cpu - lastCPU) / interval) * 100
        let fields: [String: Any] = [
            "uptime_s": (uptime * 10).rounded() / 10,
            "cpu_seconds": (cpu * 1000).rounded() / 1000,
            "cpu_percent_avg": (avg * 100).rounded() / 100,
            "cpu_percent_last_interval": (recent * 100).rounded() / 100,
            "rss_bytes": Int(rssBytes()),
        ]
        return (fields, cpu, now)
    }
}

// MARK: - Permission

enum ScreenRecording {
    static func granted() -> Bool {
        CGPreflightScreenCaptureAccess()
    }

    static func request() -> Bool {
        CGRequestScreenCaptureAccess()
    }
}

func runPreflight() -> Int32 {
    let granted = ScreenRecording.granted()
    StdoutEmitter.event("hello", fields: [
        "version": helperVersion,
        "pid": Int(ProcessInfo.processInfo.processIdentifier),
        "bundle_id": helperBundleID,
        "mode": "preflight",
    ])
    StdoutEmitter.event("preflight", fields: [
        "granted": granted,
        "identifier": helperBundleID,
        "api": "CGPreflightScreenCaptureAccess",
    ])
    return granted ? 0 : 1
}

// MARK: - Selftest (no ScreenCaptureKit)

public struct SelftestStats: Equatable, Sendable {
    public var capturedWhileRunning: Int
    public var capturedWhilePaused: Int
    public var skippedWhilePaused: Int
    public var ok: Bool

    public init(capturedWhileRunning: Int, capturedWhilePaused: Int, skippedWhilePaused: Int) {
        self.capturedWhileRunning = capturedWhileRunning
        self.capturedWhilePaused = capturedWhilePaused
        self.skippedWhilePaused = skippedWhilePaused
        self.ok = capturedWhilePaused == 0 && capturedWhileRunning > 0 && skippedWhilePaused > 0
    }
}

public enum Selftest {
    /// Drive the same filter + write path used in live capture, with a synthetic 8×8 PNG.
    /// Does not call ScreenCaptureKit, Vision, or `CGRequestScreenCaptureAccess`.
    public static func run(
        spool: URL,
        emit: (String) -> Void = { StdoutEmitter.emit($0) }
    ) throws -> SelftestStats {
        try Spool.prepare(spool)
        let state = RunState()
        let config = FilterConfig.default
        var capturedRunning = 0
        var capturedPaused = 0
        var skippedPaused = 0

        func tick() throws {
            let paused = state.isPaused()
            let snapshot = FilterSnapshot(
                isPaused: paused,
                bundleID: "com.example.selftest",
                windowTitle: "Selftest",
                sharingState: 1
            )
            switch FilterEngine.evaluatePreCapture(snapshot, config: config) {
            case .skip(let reason):
                state.markSkipped(reason)
                emit(EventJSON.line(event: "skipped", fields: ["reason": reason.rawValue]))
                if reason == .paused { skippedPaused += 1 }
            case .capture:
                let sidecar = try writeSyntheticFrame(spool: spool, state: state)
                emit(EventJSON.line(event: "captured", fields: [
                    "frame_id": sidecar.frame_id,
                    "capture_mode": sidecar.capture_mode,
                ]))
                if paused {
                    capturedPaused += 1
                } else {
                    capturedRunning += 1
                }
            }
        }

        for _ in 0..<3 { try tick() }
        state.setPaused(true)
        emit(EventJSON.line(event: "paused"))
        for _ in 0..<5 { try tick() }
        state.setPaused(false)
        emit(EventJSON.line(event: "resumed"))
        for _ in 0..<2 { try tick() }

        return SelftestStats(
            capturedWhileRunning: capturedRunning,
            capturedWhilePaused: capturedPaused,
            skippedWhilePaused: skippedPaused
        )
    }

    private static func writeSyntheticFrame(spool: URL, state: RunState) throws -> FrameSidecar {
        guard let image = PNGWriter.selftestImage(), let png = PNGWriter.data(from: image) else {
            throw WindowCapture.CaptureFailure.pngEncodeFailed
        }
        let frameID = UUID().uuidString.lowercased()
        let phash = PerceptualHash.hash(image: image)
        let sidecar = FrameSidecar(
            frame_id: frameID,
            captured_at: EventJSON.timestamp(),
            display_id: nil,
            bundle_id: "com.example.selftest",
            app_name: "Selftest",
            window_title: "Selftest",
            window_id: 0,
            is_private_browsing: false,
            url: nil,
            ocr: OCRPayload(lines: [
                OCRLine(text: "selftest", bbox: [0, 0, 8, 8], confidence: 1.0),
            ]),
            phash: PerceptualHash.hex(phash),
            width: image.width,
            height: image.height,
            capture_mode: "selftest"
        )
        _ = try Spool.write(png: png, sidecar: sidecar, root: spool)
        state.markWritten()
        state.remember(windowID: 0, phash: phash)
        return sidecar
    }
}

func runSelftest(options: CLIOptions) -> Int32 {
    let spool = options.out ?? FileManager.default.temporaryDirectory
        .appendingPathComponent("watson-selftest-\(UUID().uuidString.lowercased())", isDirectory: true)
    StdoutEmitter.event("hello", fields: [
        "version": helperVersion,
        "pid": Int(ProcessInfo.processInfo.processIdentifier),
        "bundle_id": helperBundleID,
        "mode": "selftest",
        "out": spool.path,
    ])
    do {
        let stats = try Selftest.run(spool: spool)
        StdoutEmitter.event("selftest_result", fields: [
            "ok": stats.ok,
            "captured_while_running": stats.capturedWhileRunning,
            "captured_while_paused": stats.capturedWhilePaused,
            "skipped_while_paused": stats.skippedWhilePaused,
            "out": spool.path,
        ])
        return stats.ok ? 0 : 1
    } catch {
        StdoutEmitter.event("error", fields: ["message": String(describing: error)])
        return 1
    }
}

// MARK: - Live capture loop

func runCapture(options: CLIOptions) async {
    _ = await NSApplication.shared
    let state = RunState()
    let flags = WorkspaceFlags()
    await MainActor.run { flags.start() }
    installSignalHandlers(state: state)
    startStdinReader(state: state)

    let started = Date()
    var lastCPU = ProcessMeter.cpuSeconds()
    var lastReport = started
    let config = options.filterConfig

    do {
        try Spool.prepare(options.spoolURL)
    } catch {
        StdoutEmitter.event("error", fields: ["message": "spool: \(error.localizedDescription)"])
        Darwin.exit(1)
    }

    StdoutEmitter.event("hello", fields: [
        "version": helperVersion,
        "pid": Int(ProcessInfo.processInfo.processIdentifier),
        "bundle_id": helperBundleID,
        "mode": "capture",
        "interval_s": options.interval,
        "out": options.spoolURL.path,
    ])

    if !ScreenRecording.granted() {
        StdoutEmitter.event("permission_needed", fields: [
            "identifier": helperBundleID,
            "hint": "Grant Screen Recording, then rerun. See helper/README.md.",
        ])
        _ = ScreenRecording.request()
        StdoutEmitter.event("permission_requested")
    }

    while !state.shouldQuit() {
        await captureTick(options: options, state: state, flags: flags, config: config)

        let now = Date()
        if now.timeIntervalSince(lastReport) >= options.selfReportInterval {
            var report = ProcessMeter.reportFields(startedAt: started, lastCPU: lastCPU, lastWall: lastReport, now: now)
            let snap = state.snapshot()
            report.fields["frames_written"] = snap.written
            report.fields["frames_skipped"] = snap.skipped
            report.fields["duplicates"] = snap.duplicates
            StdoutEmitter.event("self_report", fields: report.fields)
            lastCPU = report.cpu
            lastReport = report.wall
        }

        await sleepUntilNextTick(interval: options.interval, state: state)
    }

    var finalReport = ProcessMeter.reportFields(startedAt: started, lastCPU: lastCPU, lastWall: lastReport)
    let snap = state.snapshot()
    finalReport.fields["frames_written"] = snap.written
    finalReport.fields["frames_skipped"] = snap.skipped
    finalReport.fields["duplicates"] = snap.duplicates
    StdoutEmitter.event("self_report", fields: finalReport.fields)
    StdoutEmitter.event("stopped")
}

private func captureTick(
    options: CLIOptions,
    state: RunState,
    flags: WorkspaceFlags,
    config: FilterConfig
) async {
    if !ScreenRecording.granted() {
        state.markSkipped(.notGranted)
        StdoutEmitter.event("skipped", fields: ["reason": SkipReason.notGranted.rawValue])
        return
    }

    let window: FrontmostWindow
    do {
        window = try await WindowCapture.resolveFrontmost()
    } catch {
        state.markSkipped(.noWindow)
        StdoutEmitter.event("skipped", fields: [
            "reason": SkipReason.noWindow.rawValue,
            "message": String(describing: error),
        ])
        return
    }

    let pre = FilterSnapshot(
        isPaused: state.isPaused(),
        isLocked: SystemProbes.isScreenLocked(),
        isDisplayAsleep: flags.isDisplayAsleep() || SystemProbes.isDisplayAsleep(),
        idleSeconds: SystemProbes.idleSeconds(),
        bundleID: window.bundleID,
        windowTitle: window.title,
        sharingState: window.sharingState,
        previousPhash: state.previousPhash(windowID: window.windowID)
    )

    if case .skip(let reason) = FilterEngine.evaluatePreCapture(pre, config: config) {
        state.markSkipped(reason)
        StdoutEmitter.event("skipped", fields: skipFields(reason: reason, window: window))
        return
    }

    let image: CGImage
    do {
        image = try await WindowCapture.screenshot(of: window)
    } catch {
        StdoutEmitter.event("error", fields: ["message": String(describing: error)])
        return
    }

    let phash = PerceptualHash.hash(image: image)
    var post = pre
    post.phash = phash
    if case .skip(let reason) = FilterEngine.evaluateDuplicate(post, config: config) {
        state.markSkipped(reason)
        StdoutEmitter.event("skipped", fields: skipFields(reason: reason, window: window))
        return
    }

    let lines: [OCRLine]
    do {
        lines = try OCR.recognize(image: image)
    } catch {
        StdoutEmitter.event("error", fields: ["message": "ocr: \(error.localizedDescription)"])
        lines = []
    }

    guard let png = PNGWriter.data(from: image) else {
        StdoutEmitter.event("error", fields: ["message": "png encode failed"])
        return
    }

    let sidecar = FrameSidecar(
        frame_id: UUID().uuidString.lowercased(),
        captured_at: EventJSON.timestamp(),
        display_id: window.displayID,
        bundle_id: window.bundleID,
        app_name: window.appName,
        window_title: window.title,
        window_id: window.windowID,
        is_private_browsing: PrivateBrowsing.detect(windowTitle: window.title),
        url: nil,
        ocr: OCRPayload(lines: lines),
        phash: PerceptualHash.hex(phash),
        width: image.width,
        height: image.height,
        capture_mode: "frontmost"
    )

    do {
        let paths = try Spool.write(png: png, sidecar: sidecar, root: options.spoolURL)
        state.markWritten()
        state.remember(windowID: window.windowID, phash: phash)
        StdoutEmitter.event("captured", fields: [
            "frame_id": sidecar.frame_id,
            "bundle_id": sidecar.bundle_id,
            "png": paths.png.path,
            "sidecar": paths.json.path,
            "ocr_lines": lines.count,
            "phash": sidecar.phash,
        ])
    } catch {
        StdoutEmitter.event("error", fields: ["message": "write: \(error.localizedDescription)"])
    }
}

private func skipFields(reason: SkipReason, window: FrontmostWindow) -> [String: Any] {
    [
        "reason": reason.rawValue,
        "bundle_id": window.bundleID,
    ]
}

private func sleepUntilNextTick(interval: TimeInterval, state: RunState) async {
    let deadline = Date().addingTimeInterval(interval)
    while Date() < deadline, !state.shouldQuit() {
        try? await Task.sleep(nanoseconds: 50_000_000)
    }
}

private func startStdinReader(state: RunState) {
    DispatchQueue.global(qos: .utility).async {
        let handle = FileHandle.standardInput
        var buffer = Data()
        while !state.shouldQuit() {
            let chunk = handle.availableData
            if chunk.isEmpty {
                break
            }
            buffer.append(chunk)
            while let range = buffer.range(of: Data([0x0A])) {
                let lineData = buffer.subdata(in: buffer.startIndex..<range.lowerBound)
                buffer.removeSubrange(..<range.upperBound)
                let line = String(data: lineData, encoding: .utf8) ?? ""
                handleStdinLine(line, state: state)
            }
        }
    }
}

func handleStdinLine(_ line: String, state: RunState) {
    switch CommandParser.parse(line) {
    case .failure(.empty):
        return
    case .failure(let error):
        StdoutEmitter.event("error", fields: ["message": error.description])
    case .success(.pause):
        state.setPaused(true)
        StdoutEmitter.event("paused")
    case .success(.resume):
        state.setPaused(false)
        StdoutEmitter.event("resumed")
    case .success(.status):
        let snap = state.snapshot()
        var fields: [String: Any] = [
            "paused": snap.paused,
            "screen_recording": ScreenRecording.granted(),
            "locked": SystemProbes.isScreenLocked(),
            "display_asleep": SystemProbes.isDisplayAsleep(),
            "idle_s": (SystemProbes.idleSeconds() * 10).rounded() / 10,
            "frames_written": snap.written,
            "frames_skipped": snap.skipped,
            "duplicates": snap.duplicates,
        ]
        if let last = snap.lastSkip {
            fields["last_skip"] = last
        }
        StdoutEmitter.event("status", fields: fields)
    case .success(.quit):
        state.requestQuit()
    }
}

private func installSignalHandlers(state: RunState) {
    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)
    let queue = DispatchQueue.main
    for sig in [SIGINT, SIGTERM] {
        let source = DispatchSource.makeSignalSource(signal: sig, queue: queue)
        source.setEventHandler {
            state.requestQuit()
        }
        source.resume()
        state.keep(signalSource: source)
    }
}
