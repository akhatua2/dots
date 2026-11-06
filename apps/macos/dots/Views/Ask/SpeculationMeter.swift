import SwiftUI

/// Speculative decoding, made visible. A small draft model guesses the next few tokens; the big model
/// checks them all in one pass. Each check is a column of squares, lowest guess first: orange for a guess
/// the big model kept, dark for one it threw out. Newest on the right.
struct SpeculationMeter: View {
    var checks: [DraftCheck]
    /// Still answering: the strip grows and the line says it is drafting
    var live: Bool
    private let shown = 28

    var body: some View {
        let start = max(0, checks.count - shown)
        HStack(alignment: .center, spacing: 12) {
            HStack(alignment: .bottom, spacing: 3) {
                ForEach(start..<checks.count, id: \.self) { i in
                    CheckColumn(check: checks[i])
                        .transition(.asymmetric(insertion: .scale(scale: 0.3, anchor: .bottom).combined(with: .opacity),
                                                removal: .opacity))
                }
            }
            .animation(.spring(response: 0.3, dampingFraction: 0.65), value: checks.count)
            Text(summary)
                .font(D.ui(12).monospacedDigit())
                .foregroundStyle(D.t4)
                .contentTransition(.numericText())
                .animation(.easeOut(duration: 0.2), value: summary)
        }
        .help("A small draft model guesses ahead; the pool checks every guess in one pass. Orange: kept.")
    }

    private var summary: String {
        let kept = checks.reduce(0) { $0 + $1.kept }
        let proposed = checks.reduce(0) { $0 + $1.proposed }
        let perCheck = Double(kept + checks.count) / Double(max(1, checks.count))
        return live
            ? String(format: "Drafting ahead · %.1f tokens a check", perCheck)
            : "Drafted · \(kept) of \(proposed) guesses kept"
    }
}

private struct CheckColumn: View {
    var check: DraftCheck

    var body: some View {
        VStack(spacing: 2) {
            ForEach((0..<check.proposed).reversed(), id: \.self) { j in
                RoundedRectangle(cornerRadius: 1.2, style: .continuous)
                    .fill(j < check.kept ? D.accent : D.btn)
                    .frame(width: 5, height: 5)
            }
        }
    }
}
