import SwiftUI

/// Speculative decoding, made visible while an answer streams: a dot for each token the draft model guessed
/// in the latest check, orange when the big model kept it. Beside it, how many tokens each check yields.
struct SpeculationMeter: View {
    var latest: DraftCheck
    var checks: [DraftCheck]

    var body: some View {
        HStack(spacing: 8) {
            HStack(spacing: 3) {
                ForEach(0..<latest.proposed, id: \.self) { i in
                    Circle()
                        .fill(i < latest.kept ? D.accent : D.btn)
                        .frame(width: 5, height: 5)
                }
            }
            .animation(.easeOut(duration: 0.12), value: latest)
            Text(String(format: "Drafting ahead · %.1f tokens a check", perCheck))
                .font(D.ui(12).monospacedDigit())
                .foregroundStyle(D.t5)
        }
        .frame(height: 26)  // the height of the copy and thumbs row that takes its place
        .help("A small draft model guesses the next tokens; the pool checks them all in one pass. Orange: kept.")
    }

    private var perCheck: Double {
        let kept = checks.reduce(0) { $0 + $1.kept }
        return Double(kept + checks.count) / Double(max(1, checks.count))
    }
}
