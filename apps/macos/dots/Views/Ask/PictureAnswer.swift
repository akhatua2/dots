import AppKit
import SwiftUI

/// A picture as an answer. While a Mac in the pool draws it, it sharpens step by step, each step fading into
/// the next, with how far the drawing has come beneath; then it is the picture.
struct PictureAnswer: View {
    var image: Data?
    var step = 0
    var of = 0
    var done = true

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous).fill(D.raised)
                if let image, let picture = NSImage(data: image) {
                    Image(nsImage: picture).resizable().interpolation(.high).scaledToFit()
                        .id(step)
                        .transition(.opacity)
                }
            }
            .aspectRatio(aspect, contentMode: .fit)
            .frame(maxWidth: 460, alignment: .leading)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .animation(.easeInOut(duration: 0.4), value: step)
            if !done {
                DrawingProgress(step: step, of: of)
            }
        }
    }

    private var aspect: CGFloat {
        guard let image, let size = NSImage(data: image)?.size, size.height > 0 else { return 1 }
        return size.width / size.height
    }
}

/// How far a drawing has come: a live dot, the step, and a thin line filling up. The height of the row of
/// actions that replaces it.
private struct DrawingProgress: View {
    var step: Int
    var of: Int

    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(D.accent).frame(width: 5, height: 5)
            Text(step > 0 && of > 0 ? "Drawing · step \(min(step, of)) of \(of)" : "Starting the drawing")
                .font(D.ui(12).monospacedDigit()).foregroundStyle(D.t5)
            GeometryReader { geo in
                Capsule().fill(D.btn)
                    .overlay(alignment: .leading) {
                        Capsule().fill(D.t4).frame(width: geo.size.width * fraction)
                    }
            }
            .frame(width: 90, height: 2)
            .animation(.easeOut(duration: 0.3), value: step)
        }
        .frame(height: 26)
        .help("One Mac in the pool draws the picture. It sends it after every step, so you see it take shape.")
    }

    private var fraction: CGFloat { of > 0 ? CGFloat(min(step, of)) / CGFloat(of) : 0 }
}
