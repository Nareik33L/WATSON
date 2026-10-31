// swift-tools-version: 6.0
// WATSON capture helper — macOS 14+, no Xcode project.
// Default build is helper/scripts/build.sh (swiftc). SwiftPM is --spm / CI.

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
