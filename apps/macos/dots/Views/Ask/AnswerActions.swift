import AppKit
import SwiftUI

/// Under each answer: copy it, and say whether it helped.
struct AnswerActions: View {
    @Environment(AppModel.self) private var model
    var turn: ChatTurn
    @State private var copied = false

    var body: some View {
        HStack(spacing: 2) {
            ActionIcon(symbol: copied ? "checkmark" : "doc.on.doc", help: copied ? "Copied" : "Copy", action: copy)
            ActionIcon(symbol: turn.rating == 1 ? "hand.thumbsup.fill" : "hand.thumbsup", help: "Good answer",
                       on: turn.rating == 1) { model.chats.rate(turn.id, 1) }
            ActionIcon(symbol: turn.rating == -1 ? "hand.thumbsdown.fill" : "hand.thumbsdown", help: "Bad answer",
                       on: turn.rating == -1) { model.chats.rate(turn.id, -1) }
            if let drafted {
                Text(drafted).font(D.ui(12).monospacedDigit()).foregroundStyle(D.t5).padding(.leading, 8)
                    .help(helpText)
            }
        }
        .padding(.leading, -7)  // the icons' own padding, so they line up with the answer's text
    }

    /// With a draft model: how much faster it made the answer, against the same pool without one
    private var drafted: String? {
        guard let r = turn.receipt, (r.draftChecks ?? 0) > 0 else { return nil }
        guard let s = r.speedup else { return "Drafted ahead" }
        if s >= 1.05 { return String(format: "%.1f× faster, drafted ahead", s) }
        if s <= 0.95 { return String(format: "%.1f× slower, drafted ahead", 1 / s) }
        return "Drafted ahead, same speed"
    }

    private var helpText: String {
        guard let r = turn.receipt, let kept = r.draftKept, let proposed = r.draftProposed else { return "" }
        let measured = r.speedup == nil ? " The speedup shows once the pool has answered without drafting ahead." : ""
        return "A small draft model guessed ahead; the pool kept \(kept) of its \(proposed) guesses, checking them in batches." + measured
    }

    private func copy() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(turn.answer, forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
    }
}

private struct ActionIcon: View {
    var symbol: String
    var help: String
    var on = false
    var action: () -> Void
    @State private var hovered = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12.5, weight: .medium))
                .foregroundStyle(on || hovered ? D.t2 : D.t5)
                .frame(width: 28, height: 26)
                .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(hovered ? D.raised : .clear))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(help)
        .accessibilityLabel(help)
        .onHover { hovered = $0 }
    }
}
