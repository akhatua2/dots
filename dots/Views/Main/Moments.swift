import SwiftUI

/// The join and the unlock: the swirl opens over the window, says what happened, and closes again.
struct MomentView: View {
    @Environment(AppModel.self) private var model
    var moment: Moment
    /// Seconds into the moment, when drawn as a still for `--snapshot`.
    var frozenAt: Double? = nil
    @State private var start = Date()
    @State private var closing: Date?

    private var hasUnlock: Bool {
        switch moment {
        case .joined(_, _, let unlock, _): unlock != nil
        case .unlocked: true
        }
    }

    /// When the join hands over to the unlock, and when it all closes on its own
    private var unlockAt: Double { if case .unlocked = moment { 0.35 } else { 2.6 } }
    private var duration: Double { hasUnlock ? unlockAt + 3.4 : 3.2 }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: frozenAt != nil)) { context in
            let t = frozenAt ?? context.date.timeIntervalSince(start)
            let closeT = closing.map { context.date.timeIntervalSince($0) } ?? (frozenAt == nil ? max(0, t - duration) : 0)
            let open = ease(t / 0.9) * (1 - ease(closeT / 0.6))
            let fade = 1 - ease(closeT / 0.35)
            ZStack {
                SwirlCanvas(phase: 0.35 + t * 0.05, reveal: open)
                if open > 0.02 {
                    content(t: t, fade: fade)
                    if !(hasUnlock && t >= unlockAt) { youMarker(t: t, fade: fade) }
                }
            }
            .onChange(of: closeT >= 0.62) { _, done in if done { model.moment = nil } }
        }
        .contentShape(Rectangle())
        .onTapGesture { if closing == nil { closing = .now } }
        .onKeyPress(.escape) { closing = .now; return .handled }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isModal)
    }

    @ViewBuilder
    private func content(t: Double, fade: Double) -> some View {
        let s = model.state
        let showUnlock = hasUnlock && t >= unlockAt
        let a = showUnlock ? ease((t - unlockAt) / 0.5) : ease((t - 0.45) / 0.5) * (hasUnlock ? 1 - ease((t - unlockAt + 0.35) / 0.3) : 1)
        VStack(spacing: 14) {
            if showUnlock, let info = unlockInfo {
                Text("UNLOCKED").font(D.ui(14, .bold)).tracking(4).foregroundStyle(D.t3)
                Text(info.tier.name).font(D.display(96)).foregroundStyle(D.t1).minimumScaleFactor(0.5).lineLimit(1)
                Text("\(info.tier.paramsLong.capitalizedFirst). Your Mac took \(info.pool.name) to \(info.event.toGB.grouped) GB.")
                    .font(D.ui(19)).foregroundStyle(D.t2)
                    .shadow(color: D.bg0, radius: 6).shadow(color: D.bg0, radius: 14)
            } else if case let .joined(poolID, number, _, waiting) = moment, let pool = s.pool(poolID) {
                Text("You're in.").font(D.display(96)).foregroundStyle(D.t1)
                Text("Your \(s.thisMac.kind) is Mac \(number.grouped) in \(pool.name).")
                    .font(D.ui(19)).foregroundStyle(D.t2)
                    .shadow(color: D.bg0, radius: 6).shadow(color: D.bg0, radius: 14)
                if let waiting {
                    Text("\(waiting). It starts lending as soon as it can.").font(D.ui(15)).foregroundStyle(D.t4)
                }
            }
        }
        .multilineTextAlignment(.center)
        .padding(40)
        .opacity(a * fade)
        .scaleEffect(0.96 + 0.04 * a)
    }

    /// Your Mac: one orange cell in the pool's swirl, out in the field below the words
    private func youMarker(t: Double, fade: Double) -> some View {
        GeometryReader { geo in
            let pop = ease((t - 0.8) / 0.4)
            HStack(spacing: 8) {
                Rectangle().fill(D.accent).frame(width: 11, height: 11)
                    .shadow(color: D.accent.opacity(0.6), radius: 8)
                Text("you").font(D.ui(13, .bold)).foregroundStyle(D.accentText)
                    .padding(.horizontal, 5).background(D.bg0)
            }
            .scaleEffect(0.6 + 0.4 * pop)
            .position(x: geo.size.width * 0.72, y: geo.size.height * 0.76)
            .opacity(pop * fade)
        }
        .allowsHitTesting(false)
    }

    private var unlockInfo: (pool: Pool, tier: ModelTier, event: UnlockEvent)? {
        let event: UnlockEvent?
        let poolID: String
        switch moment {
        case let .joined(p, _, u, _): poolID = p; event = u
        case let .unlocked(p, e): poolID = p; event = e
        }
        guard let event, let pool = model.state.pool(poolID), let tier = Ladder.tier(event.modelID) else { return nil }
        return (pool, tier, event)
    }

    private func ease(_ x: Double) -> Double {
        let x = min(1, max(0, x))
        return x * x * x * (x * (6 * x - 15) + 10)
    }
}
