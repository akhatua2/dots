import SwiftUI

/// Ordered dithering on square cells: the look behind every dotted picture in dots.
enum Dither {
    static let bayer8: [Double] = [
        0, 32, 8, 40, 2, 34, 10, 42, 48, 16, 56, 24, 50, 18, 58, 26,
        12, 44, 4, 36, 14, 46, 6, 38, 60, 28, 52, 20, 62, 30, 54, 22,
        3, 35, 11, 43, 1, 33, 9, 41, 51, 19, 59, 27, 49, 17, 57, 25,
        15, 47, 7, 39, 13, 45, 5, 37, 63, 31, 55, 23, 61, 29, 53, 21,
    ].map { ($0 + 0.5) / 64 }

    /// Grid indices can be negative (the grid is centred), and `& 7` wraps them like the reels do.
    @inline(__always) static func threshold(_ i: Int, _ j: Int) -> Double { bayer8[(j & 7) * 8 + (i & 7)] }
}

/// The swirl's density at a point, in units of the swirl's radius.
enum SwirlField {
    static func density(_ u: Double, _ v: Double, phase: Double, gain: Double = 1,
                        e0: Double = 0.3, e1: Double = 1.8, k: Double = 1.5) -> Double {
        let l = (u * u + v * v).squareRoot() + 1e-6
        let a = atan2(v, u)
        let off = k / pow(l, 1.2) + 2 * a / (2 * .pi) + phase
        let env = pow(min(1, max(0, (l - e0) / (e1 - e0))), 1.3)
        return min(1, env * gain) * (off - floor(off))
    }
}

/// A pool's emblem: a small dithered swirl with more cells the more Macs the pool has,
/// so a big pool reads finer than a new one.
struct EmblemView: View {
    var macs: Int
    var phase: Double
    var size: CGFloat
    var radius: CGFloat = 0.3

    var body: some View {
        Canvas(rendersAsynchronously: false) { ctx, sz in
            let n = max(8, 2 * Int(((2.5 * log2(Double(max(macs, 1))) + 3) / 2).rounded()))
            let h = n / 2
            let cw = sz.width / CGFloat(n)
            ctx.fill(Path(CGRect(origin: .zero, size: sz)), with: .color(D.bg0))
            var path = Path()
            for j in 0..<n {
                for i in 0..<n {
                    let u = (Double(i - h) + 0.5) / Double(h)
                    let v = (Double(j - h) + 0.5) / Double(h)
                    guard (u * u + v * v).squareRoot() <= 1.02 else { continue }
                    if SwirlField.density(u, v, phase: phase, gain: 1.25, e0: -0.2, e1: 1.0, k: 0.55) > Dither.threshold(i - h, j - h) {
                        path.addRect(CGRect(x: (CGFloat(i) * cw).rounded(.down), y: (CGFloat(j) * cw).rounded(.down),
                                            width: cw.rounded(.up), height: cw.rounded(.up)))
                    }
                }
            }
            ctx.fill(path, with: .color(D.emblem))
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * radius, style: .continuous))
        .accessibilityHidden(true)
    }
}

/// The full swirl for the join and unlock moments. A circle opens from `origin` onto a swirl
/// that stays put (`reveal` 0 to 1), and the swirl turns slowly with `phase`.
struct SwirlCanvas: View {
    var phase: Double
    var reveal: Double
    var origin: UnitPoint = .center
    var cell: CGFloat = 7

    var body: some View {
        Canvas { ctx, sz in
            let cx = sz.width / 2, cy = sz.height / 2
            let full = (sz.width * sz.width + sz.height * sz.height).squareRoot()
            let k = max(0, min(1, reveal))
            guard k > 0.001 else { return }
            let ox = origin.x * sz.width, oy = origin.y * sz.height
            let mx = ox + (cx - ox) * k, my = oy + (cy - oy) * k
            let r = full * k
            ctx.clip(to: Path(ellipseIn: CGRect(x: mx - r, y: my - r, width: 2 * r, height: 2 * r)))
            ctx.fill(Path(CGRect(origin: .zero, size: sz)), with: .color(D.bg0))
            let R = Double(min(sz.width, sz.height)) * 0.62
            let i0 = Int(floor(-cx / cell)) - 1, i1 = Int(ceil((sz.width - cx) / cell)) + 1
            let j0 = Int(floor(-cy / cell)) - 1, j1 = Int(ceil((sz.height - cy) / cell)) + 1
            var path = Path()
            for j in j0..<j1 {
                for i in i0..<i1 {
                    let u = (Double(i) + 0.5) * Double(cell) / R
                    let v = (Double(j) + 0.5) * Double(cell) / R
                    if SwirlField.density(u, v, phase: phase) > Dither.threshold(i, j) {
                        path.addRect(CGRect(x: cx + CGFloat(i) * cell, y: cy + CGFloat(j) * cell, width: cell, height: cell))
                    }
                }
            }
            ctx.fill(path, with: .color(D.swirl))
        }
        .accessibilityHidden(true)
    }
}
