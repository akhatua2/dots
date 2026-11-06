import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// The art as an image to keep: tonight's edition at desktop size, with a quiet caption, saved where you choose.
@MainActor
enum ArtExport {
    static func save(pool: Pool, history: PoolHistory, thisMacKind: String) {
        let now = Date()
        let edition = history.nights.count
        let poster = ArtPoster(pool: pool, history: history, now: now)
            .environment(\.artClock, now)
            .environment(\.colorScheme, .dark)
        let renderer = ImageRenderer(content: poster)
        renderer.scale = 2
        guard let cg = renderer.cgImage,
              let png = NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]) else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.png]
        panel.nameFieldStringValue = "\(pool.name) No. \(edition).png"
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            try? png.write(to: url)
        }
    }
}

/// Tonight's edition at 1440 by 900: the rings left of centre, the count at the heart, and the caption in a corner.
struct ArtPoster: View {
    var pool: Pool
    var history: PoolHistory
    var now: Date

    var body: some View {
        let runs = history.runsTonight(now: now)
        ZStack(alignment: .topLeading) {
            D.bg0
            RingsArt(history: history, size: 846, cells: 423, lastNights: 30, showNow: false)
                .position(x: 604, y: 450)
            VStack(spacing: 4) {
                Text("\(history.nights.count)").font(D.ui(40, .light).monospacedDigit()).foregroundStyle(D.t2)
                Text("NIGHTS").font(D.ui(10, .bold)).tracking(2.4).foregroundStyle(Color(hex: 0x5A5A5A))
            }
            .position(x: 604, y: 450)
            VStack(alignment: .trailing, spacing: 6) {
                Text("\(pool.name.uppercased()) · NO. \(history.nights.count)").foregroundStyle(D.t3)
                Text("\(now.formatted(.dateTime.day().month(.abbreviated).year()).uppercased()) · \(runs.grouped) RUNS · \(Runs.tokens(runs)) TOKENS")
                HStack(spacing: 7) {
                    Rectangle().fill(D.accent).frame(width: 7, height: 7)
                    Text("YOUR MAC")
                }
            }
            .font(D.mono(11)).tracking(1.6).foregroundStyle(Color(hex: 0x6E6E6E))
            .frame(width: 1_440 - 48, height: 900 - 40, alignment: .bottomTrailing)
        }
        .frame(width: 1_440, height: 900)
    }
}
