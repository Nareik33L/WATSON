import CoreGraphics
import XCTest
@testable import WatsonCapture

final class FiltersTests: XCTestCase {
    func testOrderPausedWinsFirst() {
        let snapshot = FilterSnapshot(
            isPaused: true,
            isLocked: true,
            idleSeconds: 9_000,
            bundleID: "com.apple.MobileSMS",
            windowTitle: "Incognito",
            sharingState: 0
        )
        XCTAssertEqual(FilterEngine.evaluatePreCapture(snapshot), .skip(.paused))
    }

    func testLockedThenAsleepThenIdle() {
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(isLocked: true, bundleID: "a.b")),
            .skip(.locked)
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(isDisplayAsleep: true, bundleID: "a.b")),
            .skip(.displayAsleep)
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(idleSeconds: 181, bundleID: "a.b")),
            .skip(.idle)
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(idleSeconds: 180, bundleID: "a.b")),
            .capture
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(idleSeconds: 179, bundleID: "a.b")),
            .capture
        )
    }

    func testExcludedBundleAndTitle() {
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(bundleID: "com.apple.MobileSMS")),
            .skip(.excludedApp)
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(bundleID: "com.apple.Safari", windowTitle: "Private Browsing")),
            .skip(.excludedTitle)
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(bundleID: "com.google.Chrome", windowTitle: "Incognito")),
            .skip(.excludedTitle)
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(bundleID: "com.apple.Safari", windowTitle: "1Password")),
            .skip(.excludedTitle)
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(bundleID: "com.apple.Safari", windowTitle: "Sign in to GitHub")),
            .skip(.excludedTitle)
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(bundleID: "com.apple.Safari", windowTitle: "Change your password")),
            .skip(.excludedTitle)
        )
    }

    func testImmediateSkipsBeforeWindowLookup() {
        XCTAssertEqual(FilterEngine.evaluateImmediate(FilterSnapshot(isPaused: true)), .skip(.paused))
        XCTAssertEqual(FilterEngine.evaluateImmediate(FilterSnapshot(isLocked: true)), .skip(.locked))
        XCTAssertEqual(FilterEngine.evaluateImmediate(FilterSnapshot(isDisplayAsleep: true)), .skip(.displayAsleep))
        XCTAssertEqual(FilterEngine.evaluateImmediate(FilterSnapshot(idleSeconds: 181)), .skip(.idle))
        XCTAssertEqual(FilterEngine.evaluateImmediate(FilterSnapshot(bundleID: "")), .capture)
    }

    func testSharingNoneAndMissingWindow() {
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(bundleID: "a.b", sharingState: 0)),
            .skip(.sharingNone)
        )
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(bundleID: "")),
            .skip(.noWindow)
        )
    }

    func testDefaultExclusionListCoversSection51StarterSet() {
        for bundle in [
            "com.apple.MobileSMS",
            "com.apple.FaceTime",
            "com.apple.Photos",
            "com.apple.Passwords",
            "com.1password.1password",
            "com.bitwarden.desktop",
        ] {
            XCTAssertTrue(DefaultExclusions.bundleIDs.contains(bundle), bundle)
        }
        XCTAssertTrue(DefaultExclusions.titlePatterns.contains { $0.lowercased() == "password" })
    }

    func testPhashDuplicateThresholdIsFour() {
        let same: UInt64 = 0xFFFF_FFFF_FFFF_FFFF
        XCTAssertTrue(PerceptualHash.shouldSkipDuplicate(current: same, previous: same, threshold: 4))
        // Distance 4: flip 4 bits.
        let four: UInt64 = same ^ 0b1111
        XCTAssertEqual(PerceptualHash.hammingDistance(same, four), 4)
        XCTAssertTrue(PerceptualHash.shouldSkipDuplicate(current: four, previous: same, threshold: 4))
        // Distance 5: must pass.
        let five: UInt64 = same ^ 0b1_1111
        XCTAssertEqual(PerceptualHash.hammingDistance(same, five), 5)
        XCTAssertFalse(PerceptualHash.shouldSkipDuplicate(current: five, previous: same, threshold: 4))

        XCTAssertEqual(
            FilterEngine.evaluateDuplicate(FilterSnapshot(phash: four, previousPhash: same)),
            .skip(.duplicate)
        )
        XCTAssertEqual(
            FilterEngine.evaluateDuplicate(FilterSnapshot(phash: five, previousPhash: same)),
            .capture
        )
        XCTAssertEqual(
            FilterEngine.evaluateDuplicate(FilterSnapshot(phash: same, previousPhash: nil)),
            .capture
        )
    }

    func testIdenticalSyntheticImagesHashEqual() {
        guard let a = syntheticGray(), let b = syntheticGray() else {
            return XCTFail("could not build test images")
        }
        XCTAssertEqual(PerceptualHash.hash(image: a), PerceptualHash.hash(image: b))
        XCTAssertEqual(PerceptualHash.hammingDistance(PerceptualHash.hash(image: a), PerceptualHash.hash(image: b)), 0)
    }

    func testExtraBundleExclusion() {
        let config = FilterConfig.default.adding(bundleIDs: ["com.example.secret"])
        XCTAssertEqual(
            FilterEngine.evaluatePreCapture(FilterSnapshot(bundleID: "com.example.secret"), config: config),
            .skip(.excludedApp)
        )
    }

    private func syntheticGray() -> CGImage? {
        var pixels = [UInt8](repeating: 128, count: 64)
        let space = CGColorSpaceCreateDeviceGray()
        guard let ctx = CGContext(
            data: &pixels,
            width: 8,
            height: 8,
            bitsPerComponent: 8,
            bytesPerRow: 8,
            space: space,
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        ) else {
            return nil
        }
        return ctx.makeImage()
    }
}
