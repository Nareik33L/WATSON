import Darwin
import XCTest
@testable import WatsonCapture

final class PermissionsTests: XCTestCase {
    func testPrepareTightensExistingDirectoryTo0700() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("watson-perms-dir-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o755]
        )
        let nested = root.appendingPathComponent("2026-10-31", isDirectory: true)
        try FileManager.default.createDirectory(
            at: nested,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o755]
        )
        XCTAssertEqual(try Spool.posixPermissions(at: root), 0o755)
        XCTAssertEqual(try Spool.posixPermissions(at: nested), 0o755)

        try Spool.prepare(root)
        XCTAssertEqual(try Spool.posixPermissions(at: root), 0o700)
        XCTAssertEqual(try Spool.posixPermissions(at: nested), 0o700)
    }

    func testWriteCreatesFiles0600EvenWithPermissiveUmask() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("watson-perms-file-\(UUID().uuidString)", isDirectory: true)
        try Spool.prepare(root)

        let previous = umask(0)
        defer { _ = umask(previous) }

        let sidecar = FrameSidecar(
            frame_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            captured_at: "2026-10-31T00:00:00Z",
            display_id: nil,
            bundle_id: "com.example.selftest",
            app_name: "Selftest",
            window_title: "Selftest",
            window_id: 1,
            is_private_browsing: false,
            url: nil,
            ocr: OCRPayload(lines: []),
            phash: PerceptualHash.hex(0),
            width: 8,
            height: 8,
            capture_mode: "selftest"
        )
        let paths = try Spool.write(png: Data([0x89, 0x50, 0x4E, 0x47]), sidecar: sidecar, root: root)
        XCTAssertEqual(try Spool.posixPermissions(at: paths.png), 0o600)
        XCTAssertEqual(try Spool.posixPermissions(at: paths.json), 0o600)
        XCTAssertEqual(try Spool.posixPermissions(at: paths.png.deletingLastPathComponent()), 0o700)

        let leftovers = try FileManager.default.contentsOfDirectory(
            at: paths.png.deletingLastPathComponent(),
            includingPropertiesForKeys: nil
        )
        XCTAssertTrue(leftovers.filter { $0.pathExtension == "tmp" || $0.lastPathComponent.hasPrefix(".") }.isEmpty)
    }
}
