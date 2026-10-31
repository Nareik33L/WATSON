import CoreGraphics
import Foundation
import Vision

/// Vision OCR (D2). Lives in the helper so the PNG never needs to cross into Python.
public enum OCR {
    /// Accurate, on-device recognition. Language correction is off so the text stays verbatim.
    public static func recognize(image: CGImage) throws -> [OCRLine] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([request])
        let observations = request.results ?? []
        return observations.compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let bbox = OCRGeometry.pixelBBox(
                normalized: observation.boundingBox,
                imageWidth: image.width,
                imageHeight: image.height
            )
            return OCRLine(
                text: candidate.string,
                bbox: bbox,
                confidence: Double(candidate.confidence)
            )
        }
    }
}

/// Vision reports a normalized box with origin at the **bottom-left**. Sidecars use
/// integer `[x_min, y_min, x_max, y_max]` with origin at the **top-left**, matching
/// the committed `tools/bench/samples/frames/*.json` fixtures.
public enum OCRGeometry {
    public static func pixelBBox(normalized: CGRect, imageWidth: Int, imageHeight: Int) -> [Int] {
        let width = CGFloat(max(imageWidth, 1))
        let height = CGFloat(max(imageHeight, 1))
        let xMin = normalized.origin.x * width
        let boxHeight = normalized.height * height
        let yMinFromTop = height - (normalized.origin.y * height) - boxHeight
        let xMax = xMin + (normalized.width * width)
        let yMax = yMinFromTop + boxHeight
        return [
            clamp(Int(xMin.rounded()), 0, imageWidth),
            clamp(Int(yMinFromTop.rounded()), 0, imageHeight),
            clamp(Int(xMax.rounded()), 0, imageWidth),
            clamp(Int(yMax.rounded()), 0, imageHeight),
        ]
    }

    private static func clamp(_ value: Int, _ lower: Int, _ upper: Int) -> Int {
        min(max(value, lower), upper)
    }
}
