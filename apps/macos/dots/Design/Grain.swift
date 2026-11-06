import CoreGraphics
import SwiftUI

/// Film grain, to keep something alive that is slowly taking shape: a few tiles of Gaussian noise, made once,
/// shown one after another. Each tile is gray around the middle, so in `.overlay` blending it lightens and
/// darkens what is under it without tinting it.
enum Grain {
    static let side = 160
    static let frames: [Image] = (0..<8).map(tile)

    private static func tile(_ seed: Int) -> Image {
        var state = UInt64(seed + 1) &* 0x9E37_79B9_7F4A_7C15
        func uniform() -> Double {
            state = state &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
            return Double(state >> 11) / Double(1 << 53)
        }
        var pixels = [UInt8](repeating: 128, count: side * side)
        for i in pixels.indices {
            // Box-Muller: a normal draw from two uniform ones
            let z = (-2 * log(max(uniform(), 1e-9))).squareRoot() * cos(2 * .pi * uniform())
            pixels[i] = UInt8(max(0, min(255, 128 + 48 * z)))
        }
        let image = pixels.withUnsafeMutableBytes { bytes in
            CGContext(data: bytes.baseAddress, width: side, height: side, bitsPerComponent: 8, bytesPerRow: side,
                      space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)?.makeImage()
        }
        return image.map { Image(decorative: $0, scale: 2) } ?? Image(systemName: "circle")
    }
}

extension View {
    /// Live grain over this view, `amount` 0 (none) to 1 (strong), moving while `alive`.
    func grain(_ amount: Double, alive: Bool) -> some View {
        overlay {
            if amount > 0 {
                TimelineView(.animation(minimumInterval: 1.0 / 24, paused: !alive)) { context in
                    let frame = Int(context.date.timeIntervalSinceReferenceDate * 24) % Grain.frames.count
                    Grain.frames[frame].resizable(resizingMode: .tile).interpolation(.none)
                        .blendMode(.overlay).opacity(amount).allowsHitTesting(false)
                }
            }
        }
    }
}
