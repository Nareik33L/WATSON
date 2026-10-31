import XCTest
@testable import WatsonCapture

final class SidecarTests: XCTestCase {
    func testRoundTripIncludesEverySection31Field() throws {
        let sidecar = FrameSidecar(
            frame_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            captured_at: "2026-10-31T09:15:00Z",
            display_id: 1,
            bundle_id: "com.example.mail",
            app_name: "Mail",
            window_title: "Re: Acme Q4",
            window_id: 42,
            is_private_browsing: false,
            url: nil,
            ocr: OCRPayload(lines: [
                OCRLine(text: "Hello", bbox: [12, 44, 300, 62], confidence: 0.98),
            ]),
            phash: "0123456789abcdef",
            width: 320,
            height: 180,
            capture_mode: "frontmost"
        )

        let data = try SidecarJSON.encode(sidecar)
        let decoded = try SidecarJSON.decode(data)
        XCTAssertEqual(decoded, sidecar)

        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        for key in [
            "frame_id", "captured_at", "display_id", "bundle_id", "app_name",
            "window_title", "window_id", "is_private_browsing", "url", "ocr",
            "phash", "width", "height", "capture_mode",
        ] {
            XCTAssertTrue(object.keys.contains(key), "missing \(key)")
        }
        XCTAssertTrue(object["url"] is NSNull, "url must be present as JSON null")
        let ocr = try XCTUnwrap(object["ocr"] as? [String: Any])
        let lines = try XCTUnwrap(ocr["lines"] as? [[String: Any]])
        XCTAssertEqual(lines[0]["bbox"] as? [Int], [12, 44, 300, 62])
    }

    func testNullDisplayIDAndEmptyOCR() throws {
        let sidecar = FrameSidecar(
            frame_id: "id",
            captured_at: "2026-10-31T00:00:00Z",
            display_id: nil,
            bundle_id: "com.example",
            app_name: "Ex",
            window_title: "",
            window_id: 0,
            is_private_browsing: false,
            url: nil,
            ocr: OCRPayload(lines: []),
            phash: PerceptualHash.hex(0),
            width: 8,
            height: 8,
            capture_mode: "selftest"
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: try SidecarJSON.encode(sidecar)) as? [String: Any]
        )
        XCTAssertEqual(object["display_id"] as? NSNull, NSNull())
        XCTAssertEqual(object["phash"] as? String, "0000000000000000")
        XCTAssertEqual(object["capture_mode"] as? String, "selftest")
    }

    func testPhashHexRoundTrip() {
        XCTAssertEqual(PerceptualHash.hex(0x0123_4567_89AB_CDEF), "0123456789abcdef")
        XCTAssertEqual(PerceptualHash.fromHex("0123456789abcdef"), 0x0123_4567_89AB_CDEF)
        XCTAssertNil(PerceptualHash.fromHex("xyz"))
        XCTAssertNil(PerceptualHash.fromHex("abc"))
    }

    func testPrivateBrowsingDetection() {
        XCTAssertTrue(PrivateBrowsing.detect(windowTitle: "Safari — Private Browsing"))
        XCTAssertTrue(PrivateBrowsing.detect(windowTitle: "Incognito"))
        XCTAssertTrue(PrivateBrowsing.detect(windowTitle: "Edge InPrivate"))
        XCTAssertFalse(PrivateBrowsing.detect(windowTitle: "Inbox"))
    }

    func testOCRBBoxConvertsVisionBottomLeftToTopLeftPixels() {
        // Full-image box in Vision space: origin (0,0) size (1,1) → [0,0,width,height]
        XCTAssertEqual(
            OCRGeometry.pixelBBox(
                normalized: CGRect(x: 0, y: 0, width: 1, height: 1),
                imageWidth: 320,
                imageHeight: 180
            ),
            [0, 0, 320, 180]
        )
        // A strip along the Vision bottom (y=0) is the top-left origin's bottom of the image.
        let bottomStrip = OCRGeometry.pixelBBox(
            normalized: CGRect(x: 0, y: 0, width: 1, height: 0.1),
            imageWidth: 100,
            imageHeight: 100
        )
        XCTAssertEqual(bottomStrip, [0, 90, 100, 100])
    }
}
