import AppKit
import Foundation
import Vision

struct OCRImageResult: Codable {
    let index: Int
    let text: String
    let line_count: Int
    let char_count: Int
    let text_area_ratio: Double
    let is_text_image: Bool
    let error: String?
}

func recognize(index: Int, urlString: String) -> OCRImageResult {
    guard let url = URL(string: urlString) else {
        return OCRImageResult(index: index, text: "", line_count: 0,
            char_count: 0, text_area_ratio: 0, is_text_image: false,
            error: "图片地址无效")
    }
    do {
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        guard let image = NSImage(data: data),
              let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
        else {
            throw NSError(domain: "NoteOCR", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "图片解码失败"])
        }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
        let observations = (request.results ?? [])
            .sorted {
                if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.015 {
                    return $0.boundingBox.midY > $1.boundingBox.midY
                }
                return $0.boundingBox.minX < $1.boundingBox.minX
            }
        var lines: [String] = []
        var area = 0.0
        for observation in observations {
            guard let candidate = observation.topCandidates(1).first,
                  candidate.confidence >= 0.25 else { continue }
            let value = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
            if value.isEmpty { continue }
            lines.append(value)
            area += Double(observation.boundingBox.width * observation.boundingBox.height)
        }
        let text = lines.joined(separator: "\n")
        let chars = text.filter { !$0.isWhitespace }.count
        let areaRatio = min(1.0, area)
        let isTextImage = chars >= 20 && (lines.count >= 3 || areaRatio >= 0.06)
        return OCRImageResult(index: index, text: text, line_count: lines.count,
            char_count: chars, text_area_ratio: areaRatio,
            is_text_image: isTextImage, error: nil)
    } catch {
        return OCRImageResult(index: index, text: "", line_count: 0,
            char_count: 0, text_area_ratio: 0, is_text_image: false,
            error: error.localizedDescription)
    }
}

let urls = Array(CommandLine.arguments.dropFirst())
let results = urls.enumerated().map { recognize(index: $0.offset, urlString: $0.element) }
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
let payload = try encoder.encode(["images": results])
FileHandle.standardOutput.write(payload)
