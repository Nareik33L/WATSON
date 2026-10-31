import XCTest
@testable import WatsonCapture

final class ProtocolTests: XCTestCase {
    func testParsePauseResumeStatusQuit() {
        XCTAssertEqual(try CommandParser.parse(#"{"cmd":"pause"}"#).get(), .pause)
        XCTAssertEqual(try CommandParser.parse(#"{"cmd":"resume"}"#).get(), .resume)
        XCTAssertEqual(try CommandParser.parse(#"{"cmd":"status"}"#).get(), .status)
        XCTAssertEqual(try CommandParser.parse(#"{"cmd":"quit"}"#).get(), .quit)
    }

    func testParseAcceptsCommandAliasAndWhitespace() {
        XCTAssertEqual(try CommandParser.parse("  {\"command\":\"PAUSE\"}  ").get(), .pause)
    }

    func testParseIgnoresExtraKeys() {
        XCTAssertEqual(
            try CommandParser.parse(#"{"cmd":"pause","duration_minutes":15}"#).get(),
            .pause
        )
    }

    func testParseRejectsEmptyInvalidAndUnknown() {
        XCTAssertEqual(CommandParser.parse("   "), .failure(.empty))
        XCTAssertEqual(CommandParser.parse("not json"), .failure(.invalidJSON))
        XCTAssertEqual(CommandParser.parse("[1]"), .failure(.invalidJSON))
        XCTAssertEqual(CommandParser.parse(#"{"foo":1}"#), .failure(.missingCmd))
        XCTAssertEqual(CommandParser.parse(#"{"cmd":"zoom"}"#), .failure(.unknownCommand("zoom")))
    }

    func testEventLineIsSingleLineJSONWithEventAndTs() throws {
        let line = EventJSON.line(event: "paused", fields: ["reason": "user"], at: Date(timeIntervalSince1970: 0))
        XCTAssertTrue(line.hasSuffix("\n"))
        XCTAssertEqual(line.filter { $0 == "\n" }.count, 1)
        let object = try decodeObject(line)
        XCTAssertEqual(object["event"] as? String, "paused")
        XCTAssertEqual(object["reason"] as? String, "user")
        XCTAssertEqual(object["ts"] as? String, "1970-01-01T00:00:00Z")
    }

    func testTimestampIsISO8601UTC() {
        XCTAssertEqual(EventJSON.timestamp(Date(timeIntervalSince1970: 0)), "1970-01-01T00:00:00Z")
    }

    func testConstants() {
        XCTAssertEqual(helperBundleID, "com.watson.capture")
        XCTAssertFalse(helperVersion.isEmpty)
    }

    func testSelfReportEventEncodesJSONSerializationTypes() throws {
        let line = EventJSON.line(event: "self_report", fields: [
            "uptime_s": 3600.0,
            "cpu_seconds": 12.4,
            "cpu_percent_avg": 0.34,
            "cpu_percent_last_interval": 0.5,
            "rss_bytes": 42_000_000,
            "rss_peak_bytes": 42_000_000,
            "frames_written": 80,
        ])
        let object = try decodeObject(line)
        XCTAssertEqual(object["event"] as? String, "self_report")
        XCTAssertEqual(object["rss_bytes"] as? Int, 42_000_000)
        XCTAssertEqual(object["rss_peak_bytes"] as? Int, 42_000_000)
        XCTAssertEqual(object["cpu_percent_avg"] as? Double, 0.34)
    }

    private func decodeObject(_ line: String) throws -> [String: Any] {
        let data = try XCTUnwrap(line.trimmingCharacters(in: .newlines).data(using: .utf8))
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }
}
