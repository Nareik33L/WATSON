// swift-tools-version: 6.0
// WATSON capture helper — SwiftPM only, macOS 14+, no Xcode project.
// Build with Command Line Tools: `swift build` (see README.md).

import PackageDescription

let package = Package(
    name: "watson-capture",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "watson-capture", targets: ["watson-capture"]),
        .library(name: "WatsonCapture", targets: ["WatsonCapture"]),
    ],
    targets: [
        .target(
            name: "WatsonCapture",
            path: "Sources/watson-capture"
        ),
        .executableTarget(
            name: "watson-capture",
            dependencies: ["WatsonCapture"],
            path: "Sources/watson-capture-cli"
        ),
        .testTarget(
            name: "WatsonCaptureTests",
            dependencies: ["WatsonCapture"],
            path: "Tests/WatsonCaptureTests"
        ),
    ]
)
