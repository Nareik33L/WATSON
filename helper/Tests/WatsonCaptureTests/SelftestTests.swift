import XCTest
@testable import WatsonCapture

final class SelftestTests: XCTestCase {
    func testSelftestEmitsNoFramesWhilePaused() throws {
        let spool = FileManager.default.temporaryDirectory
            .appendingPathComponent("watson-selftest-\(UUID().uuidString)", isDirectory: true)
        var events: [String] = []
        let stats = try Selftest.run(spool: spool) { line in
            events.append(line)
        }

        XCTAssertEqual(stats.capturedWhilePaused, 0)
        XCTAssertEqual(stats.capturedWhileRunning, 5)
        XCTAssertEqual(stats.skippedWhilePaused, 5)
        XCTAssertTrue(stats.ok)

        let names = events.compactMap { eventName($0) }
        XCTAssertEqual(names.filter { $0 == "captured" }.count, 5)
        XCTAssertEqual(names.filter { $0 == "skipped" }.count, 5)
        XCTAssertTrue(names.contains("paused"))
        XCTAssertTrue(names.contains("resumed"))

        // Pause sits between the first three captures and the skipped burst.
        let pausedAt = try XCTUnwrap(names.firstIndex(of: "paused"))
        let resumedAt = try XCTUnwrap(names.firstIndex(of: "resumed"))
        XCTAssertTrue(names[..<pausedAt].allSatisfy { $0 == "captured" })
        XCTAssertTrue(names[pausedAt + 1..<resumedAt].allSatisfy { $0 == "skipped" })

        let files = try FileManager.default.contentsOfDirectory(
            at: try dayDir(spool),
            includingPropertiesForKeys: nil
        )
        let pngs = files.filter { $0.pathExtension == "png" }
        let jsons = files.filter { $0.pathExtension == "json" }
        XCTAssertEqual(pngs.count, 5)
        XCTAssertEqual(jsons.count, 5)

        let sidecar = try SidecarJSON.decode(Data(contentsOf: jsons[0]))
        XCTAssertEqual(sidecar.capture_mode, "selftest")
        XCTAssertEqual(sidecar.url, nil)
        XCTAssertFalse(sidecar.ocr.lines.isEmpty)
        XCTAssertEqual(sidecar.phash.count, 16)
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
        guard let data = line.trimmingCharacters(in: .newlines).data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return nil
        }
        return object["event"] as? String
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
