import AppKit
import SwiftUI

/// A picture as an answer. While a Mac in the pool draws it, it sharpens step by step, each step fading into
/// the next, under live grain that thins out as the drawing comes along; then it is the picture.
struct PictureAnswer: View {
    var image: Data?
    /// How far the drawing has come; nil once it's done
    var progress: Drawing.Progress?
    var maxWidth: CGFloat = 460
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var drawing: Bool { progress.map { !$0.done } ?? false }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous).fill(D.raised)
                if let image, let picture = NSImage(data: image) {
                    Image(nsImage: picture).resizable().interpolation(.high).scaledToFit()
                        .id(progress?.step ?? -1)
                        .transition(.opacity)
                }
            }
            .grain(grainAmount, alive: drawing && !reduceMotion)
            .overlay(alignment: .bottomLeading) {
                if let progress, drawing {
                    DrawingProgress(progress: progress).padding(10).transition(.opacity)
                }
            }
            .aspectRatio(aspect, contentMode: .fit)
            .frame(maxWidth: maxWidth, alignment: .leading)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .animation(.easeInOut(duration: 0.4), value: progress?.step)
        }
    }

    /// A step can take half a minute; the grain keeps the picture alive in between. Heavy before the first
    /// preview, thinning with each step, gone once the picture is done.
    private var grainAmount: Double {
        guard let p = progress, !p.done else { return 0 }
        guard image != nil, p.of > 0 else { return 0.9 }
        return 0.15 + 0.6 * (1 - Double(min(p.step, p.of)) / Double(p.of))
    }

    private var aspect: CGFloat {
        guard let image, let size = NSImage(data: image)?.size, size.height > 0 else { return 1 }
        return size.width / size.height
    }
}

/// How far a drawing has come, on the picture itself, in the app's own dots: one a step, light once drawn, the
/// one being drawn breathing in orange, the rest still to come; then, once it can tell, about how long is left.
private struct DrawingProgress: View {
    var progress: Drawing.Progress
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: reduceMotion)) { context in
            let now = context.date
            HStack(spacing: 8) {
                HStack(spacing: 5) {
                    ForEach(0..<max(progress.of, 1), id: \.self) { i in
                        dot(i, at: now)
                    }
                }
                if let left = timeLeft(at: now) {
                    Text(left).font(D.ui(11.5, .medium).monospacedDigit()).foregroundStyle(D.t2)
                        .contentTransition(.numericText(countsDown: true))
                }
            }
            .padding(.horizontal, 10).frame(height: 24)
            .background(Capsule().fill(D.bg0.opacity(0.6)))
        }
        .animation(.easeOut(duration: 0.3), value: progress.step)
        .help("One Mac in the pool draws the picture, one step at a time, and sends it after each so you see it take shape.")
    }

    /// The dot for step `i`: drawn, being drawn (breathing), or to come
    @ViewBuilder private func dot(_ i: Int, at now: Date) -> some View {
        let current = min(progress.step, max(progress.of - 1, 0))
        if i < current {
            Circle().fill(D.t2).frame(width: 6, height: 6)
        } else if i == current {
            let phase = reduceMotion ? 1 : (sin(now.timeIntervalSinceReferenceDate * 3.2) + 1) / 2
            Circle().fill(D.accent).frame(width: 6, height: 6)
                .scaleEffect(1 + 0.35 * phase)
                .opacity(0.55 + 0.45 * phase)
        } else {
            Circle().fill(D.t2.opacity(0.25)).frame(width: 6, height: 6)
        }
    }

    private func timeLeft(at now: Date) -> String? {
        guard let left = progress.secondsLeft(at: now) else { return nil }
        let seconds = Int(left.rounded())
        let span = seconds < 90 ? seconds.counted("second") : Int((left / 60).rounded()).counted("minute")
        return "About \(span) left"
    }
}
