import XCTest
@testable import WatsonCapture

final class SelftestTests: XCTestCase {
    func testSelftestUsesStdinPathAndDropsInFlight() async throws {
        let spool = FileManager.default.temporaryDirectory
            .appendingPathComponent("watson-selftest-\(UUID().uuidString)", isDirectory: true)
        let box = EventBox()
        let stats = try await Selftest.run(spool: spool) { line in
            box.append(line)
        }

        XCTAssertEqual(stats.capturedWhilePaused, 0)
        XCTAssertEqual(stats.capturedWhileRunning, 5)
        XCTAssertEqual(stats.skippedWhilePaused, 6)
        XCTAssertEqual(stats.droppedInFlight, 1)
        XCTAssertTrue(stats.ok)

        let names = box.snapshot().compactMap { eventName($0) }
        XCTAssertEqual(names.filter { $0 == "captured" }.count, 5)
        XCTAssertTrue(names.contains("paused"))
        XCTAssertTrue(names.contains("resumed"))

        let skipped = box.snapshot().compactMap { parseEvent($0) }.filter { $0["event"] as? String == "skipped" }
        XCTAssertEqual(skipped.filter { $0["after_capture"] as? Bool == true }.count, 1)
        XCTAssertEqual(skipped.filter { $0["reason"] as? String == "paused" }.count, 6)

        let files = try FileManager.default.contentsOfDirectory(
            at: try dayDir(spool),
            includingPropertiesForKeys: nil
        )
        XCTAssertEqual(files.filter { $0.pathExtension == "png" }.count, 5)
        XCTAssertEqual(files.filter { $0.pathExtension == "json" }.count, 5)
    }

    func testPauseGoesThroughHandleStdinLine() {
        let state = RunState()
        let box = EventBox()
        XCTAssertFalse(state.isPaused())
        handleStdinLine(#"{"cmd":"pause"}"#, state: state, emit: { box.append($0) })
        XCTAssertTrue(state.isPaused())
        XCTAssertEqual(box.snapshot().compactMap { eventName($0) }, ["paused"])
        handleStdinLine(#"{"cmd":"resume"}"#, state: state, emit: { box.append($0) })
        XCTAssertFalse(state.isPaused())
        XCTAssertEqual(box.snapshot().compactMap { eventName($0) }, ["paused", "resumed"])
    }

    func testInFlightFrameDroppedWhenPauseAcksDuringCapture() async throws {
        let spool = FileManager.default.temporaryDirectory
            .appendingPathComponent("watson-inflight-\(UUID().uuidString)", isDirectory: true)
        try Spool.prepare(spool)
        let state = RunState()
        let source = FakeFrameSource()
        let probes = ControllableProbes()
        let box = EventBox()
        let engine = CaptureEngine(
            state: state,
            source: source,
            probes: probes,
            config: .default,
            spool: spool,
            captureMode: "selftest",
            emit: { box.append($0) }
        )

        source.armGate()
        async let inflight = engine.tick()
        await source.waitUntilCaptureStarted()
        handleStdinLine(#"{"cmd":"pause"}"#, state: state, emit: { box.append($0) })
        source.releaseCapture()
        let outcome = await inflight
        XCTAssertEqual(outcome, .skipped(.paused, afterCapture: true))
        XCTAssertEqual(state.snapshot().written, 0)
        let day = try? dayDir(spool)
        if let day {
            let leftover = try FileManager.default.contentsOfDirectory(at: day, includingPropertiesForKeys: nil)
            XCTAssertTrue(leftover.filter { $0.pathExtension == "png" || $0.pathExtension == "json" }.isEmpty)
        }
        let drop = try XCTUnwrap(box.snapshot().compactMap { parseEvent($0) }.first {
            $0["event"] as? String == "skipped" && $0["after_capture"] as? Bool == true
        })
        XCTAssertEqual(drop["reason"] as? String, "paused")
    }

    func testInFlightFrameDroppedWhenLockFlipsDuringCapture() async throws {
        let spool = FileManager.default.temporaryDirectory
            .appendingPathComponent("watson-lock-\(UUID().uuidString)", isDirectory: true)
        try Spool.prepare(spool)
        let state = RunState()
        let source = FakeFrameSource()
        let probes = ControllableProbes()
        let engine = CaptureEngine(
            state: state,
            source: source,
            probes: probes,
            config: .default,
            spool: spool,
            captureMode: "selftest",
            emit: { _ in }
        )

        source.armGate()
        async let inflight = engine.tick()
        await source.waitUntilCaptureStarted()
        probes.setLocked(true)
        source.releaseCapture()
        let outcome = await inflight
        XCTAssertEqual(outcome, .skipped(.locked, afterCapture: true))
        XCTAssertEqual(state.snapshot().written, 0)
    }

    func testCLIParseFlags() throws {
        let options = try CommandLineApp.parse([
            "--interval", "12",
            "--out", "/tmp/watson-spool",
            "--selftest",
            "--self-report-interval", "5",
            "--idle-seconds", "30",
            "--exclude-bundle", "com.example.x",
        ])
        XCTAssertEqual(options.interval, 12)
        XCTAssertEqual(options.out?.path, "/tmp/watson-spool")
        XCTAssertTrue(options.selftest)
        XCTAssertEqual(options.selfReportInterval, 5)
        XCTAssertEqual(options.idleSeconds, 30)
        XCTAssertEqual(options.extraExclusions, ["com.example.x"])
        XCTAssertTrue(options.filterConfig.excludedBundleIDs.contains("com.example.x"))
    }

    func testCLIParseRejectsUnknownAndNonPositiveInterval() {
        XCTAssertThrowsError(try CommandLineApp.parse(["--nope"]))
        XCTAssertThrowsError(try CommandLineApp.parse(["--interval", "0"]))
        XCTAssertThrowsError(try CommandLineApp.parse(["--interval", "-1"]))
        XCTAssertThrowsError(try CommandLineApp.parse(["--interval"]))
    }

    private func eventName(_ line: String) -> String? {
        parseEvent(line)?["event"] as? String
    }

    private func parseEvent(_ line: String) -> [String: Any]? {
        guard let data = line.trimmingCharacters(in: .newlines).data(using: .utf8) else {
            return nil
        }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    private func dayDir(_ spool: URL) throws -> URL {
        let contents = try FileManager.default.contentsOfDirectory(
            at: spool,
            includingPropertiesForKeys: [.isDirectoryKey]
        )
        return try XCTUnwrap(contents.first(where: { url in
            (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
        }))
    }
}

final class EventBox: @unchecked Sendable {
    private let lock = NSLock()
    private var lines: [String] = []

    func append(_ line: String) {
        lock.lock()
        lines.append(line)
        lock.unlock()
    }

    func snapshot() -> [String] {
        lock.lock()
        defer { lock.unlock() }
        return lines
    }
}
