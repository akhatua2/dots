import SwiftUI

/// The dots menu: your pool and the master switch, how much this Mac lends and the level it reaches,
/// tonight so far, when it lends, overnight, and your credits.
struct MenuBarPanel: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @Environment(\.isSnapshot) private var isSnapshot

    var body: some View {
        let s = model.state
        let status = model.lendingStatus
        VStack(alignment: .leading, spacing: 8) {
            header(s, status)
            if s.thisMac.maxLendGB > 0 { lendModule(s) }
            if let pool = s.lendingPool { tonightModule(s, pool) }
            VStack(spacing: 0) {
                toggleRow("Only when plugged in", on: s.lending.onlyWhenPluggedIn,
                          note: model.signals.onPower ? nil : "On battery now") { model.setOnlyWhenPluggedIn($0) }
                    .padding(.bottom, 10)
                Rectangle().fill(Color.white.opacity(0.08)).frame(height: 0.5)
                toggleRow("Pause while I'm using it", on: s.lending.pauseWhileInUse, note: nil) { model.setPauseWhileInUse($0) }
                    .padding(.top, 10)
            }
            .module()
            overnightTile
            creditsRow(s, status)
            Rectangle().fill(Color.white.opacity(0.08)).frame(height: 0.5).padding(.horizontal, 4)
            footer(status)
        }
        .padding(12)
        .frame(width: 320)
        .background(isSnapshot ? Color(hex: 0x1E1E20) : Color(hex: 0x1E1E20, opacity: 0.74))
        .preferredColorScheme(.dark)
    }

    // MARK: Parts

    private func header(_ s: NetworkState, _ status: LendingStatus) -> some View {
        HStack(spacing: 11) {
            if let pool = s.lendingPool {
                MiniRings(pool: pool, size: 36, live: status.isActive)
                    .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Color.white.opacity(0.12), lineWidth: 0.5))
            } else {
                Image(systemName: "laptopcomputer").font(.system(size: 15, weight: .medium)).foregroundStyle(D.t3)
                    .frame(width: 36, height: 36)
                    .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(D.bg0))
            }
            VStack(alignment: .leading, spacing: 2) {
                poolName(s)
                HStack(spacing: 6) {
                    if status.isActive { Circle().fill(D.accent).frame(width: 6, height: 6).modifier(Blink(on: !isSnapshot)) }
                    Text(statusLine(status)).lineLimit(1)
                }
                .font(D.ui(11.5)).foregroundStyle(Color(hex: 0xEBEBF5, opacity: 0.6))
            }
            Spacer(minLength: 6)
            OrangeSwitch(isOn: Binding(get: { s.lending.isOn }, set: { model.setLendingOn($0) }), label: "Lend this Mac", width: 38)
                .disabled(s.thisMac.maxLendGB == 0)
        }
        .padding(.horizontal, 4).padding(.top, 4).padding(.bottom, 6)
    }

    /// The pool this Mac lends to, and a menu to move it to another of your pools.
    @ViewBuilder private func poolName(_ s: NetworkState) -> some View {
        let name = Text(s.lendingPool?.name ?? "\(s.thisMac.kind) · \(s.thisMac.memoryGB) GB").font(D.ui(13.5, .semibold)).foregroundStyle(D.t1)
        if isSnapshot || s.myPools.count < 2 {
            name
        } else {
            Menu {
                ForEach(s.myPools) { pool in
                    Button { model.join(pool) } label: { Text("\(pool.name) · \(pool.model?.name ?? "")") }
                }
            } label: {
                HStack(spacing: 5) {
                    name
                    Image(systemName: "chevron.up.chevron.down").font(.system(size: 8, weight: .bold)).foregroundStyle(D.t4)
                }
            }
            .menuStyle(.button).menuIndicator(.hidden).buttonStyle(.plain)
            .fixedSize()
        }
    }

    private func statusLine(_ status: LendingStatus) -> String {
        guard status.isActive, let w = model.backend.worker else { return "\(status.headline) · \(status.detail)" }
        if w.network { return "Network work, through \(w.poolName)" }
        let n = w.lastLayer - w.firstLayer + 1
        return w.lastLayer == w.layers - 1 ? "Running the final \(n) layers" : "Running layers \(w.firstLayer + 1) to \(w.lastLayer + 1)"
    }

    /// How much this Mac lends, with a mark where the pool reaches its next level.
    private func lendModule(_ s: NetworkState) -> some View {
        let mac = s.thisMac
        let limit = s.lending.limitGB
        let line = nextLine(for: s)
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                if let line {
                    Text(limit >= line.gb ? "Level \(line.level) unlocked" : "\(line.gb - limit) GB to Level \(line.level)")
                        .font(D.ui(15, .semibold)).tracking(-0.2).foregroundStyle(limit >= line.gb ? D.accentText : D.t1)
                    Spacer()
                    Text(line.label).font(D.ui(11.5)).foregroundStyle(Color(hex: 0xEBEBF5, opacity: 0.6))
                } else {
                    Text("Lend up to").font(D.ui(15, .semibold)).tracking(-0.2).foregroundStyle(D.t1)
                    Spacer()
                    Text("\(limit) GB").font(D.ui(13, .semibold).monospacedDigit()).foregroundStyle(D.t2)
                }
            }
            ThinSlider(value: Binding(get: { limit }, set: { model.setLimit($0) }), range: 0...max(1, mac.maxLendGB),
                       mark: line?.gb, label: "Lend up to")
                .help("\(mac.keptGB) GB always stays with your Mac" + (line.map { ". The mark is where the pool reaches Level \($0.level)." } ?? "."))
            Text("Lending \(limit) of \(mac.maxLendGB) GB")
                .font(D.ui(11.5).monospacedDigit()).foregroundStyle(Color(hex: 0xEBEBF5, opacity: 0.6))
        }
        .module()
    }

    /// Tonight so far, in the pool this Mac lends to.
    private func tonightModule(_ s: NetworkState, _ pool: Pool) -> some View {
        let night = Date().nightStart
        let tonight = (s.nights ?? []).filter { $0.date == night && $0.poolID == pool.id }
        let runs = tonight.reduce(0) { $0 + $1.runs }
        let tokens = tonight.reduce(0) { $0 + $1.tokenCount }
        let earned = s.creditLog.filter { $0.date >= night && $0.amount > 0 && $0.poolID == pool.id && ($0.kind == "lend" || $0.kind == "network") }.reduce(0) { $0 + $1.amount }
        return StatRow(stats: [.init(value: runs.grouped, label: "runs tonight"), .init(value: Runs.short(tokens), label: "tokens"),
                               .init(value: "+\(earned.grouped)", label: "credits", accent: true)], size: 18)
            .module()
    }

    private var overnightTile: some View {
        let on = model.overnightUntil != nil
        return Button { model.lendOvernight(!on) } label: {
            HStack(spacing: 12) {
                Image(systemName: "moon.fill").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(on ? D.onAccent : D.t1)
                    .frame(width: 30, height: 30)
                    .background(Circle().fill(on ? D.accent : Color.white.opacity(0.1)))
                Text(on ? "Lending Overnight" : "Lend Overnight").font(D.ui(13, .semibold)).foregroundStyle(D.t1)
                Spacer()
                Image(systemName: on ? "checkmark" : "chevron.right").font(.system(size: 11, weight: .bold))
                    .foregroundStyle(on ? D.accentText : Color(hex: 0xEBEBF5, opacity: 0.4))
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 11, style: .continuous).fill(Color.white.opacity(0.06)))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
        .disabled(model.state.thisMac.maxLendGB == 0)
        .help(on ? "Awake until \(model.overnightUntil?.formatted(date: .omitted, time: .shortened) ?? "7 AM"), then it sleeps. Click to stop."
                 : "Stays awake until 7 AM, then sleeps")
    }

    private func creditsRow(_ s: NetworkState, _ status: LendingStatus) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "smallcircle.filled.circle").font(.system(size: 15, weight: .medium)).foregroundStyle(D.t2)
            Text("\(Int(s.credits).grouped) credits").font(D.ui(13.5, .semibold).monospacedDigit()).foregroundStyle(D.t1)
                .contentTransition(.numericText())
                .help("About \(Pricing.answers(s.credits).grouped) Opus prompts")
            Spacer()
            Button("Ask") { model.showAsk() }.buttonStyle(DButtonStyle(kind: .grey))
        }
        .padding(.horizontal, 6).padding(.vertical, 4)
    }

    private func footer(_ status: LendingStatus) -> some View {
        let pausable = status.isActive || status == .inUse || status == .waitingForPower
        return HStack {
            if case .paused = status {
                Button("Resume") { model.resume() }.buttonStyle(.plain).font(D.ui(13)).foregroundStyle(D.t2)
            } else {
                Button(pausable ? "Pause for an Hour" : "Quit dots") {
                    if pausable { model.pauseForAnHour() } else { NSApp.terminate(nil) }
                }
                .buttonStyle(.plain).font(D.ui(13)).foregroundStyle(D.t2)
            }
            Spacer()
            Button { model.showSettings() } label: {
                Image(systemName: "gearshape").font(.system(size: 13, weight: .medium)).frame(width: 28, height: 28).contentShape(Rectangle())
            }
            .buttonStyle(.plain).foregroundStyle(D.t3)
            .help("Settings")
            .accessibilityLabel("Settings")
            .padding(.trailing, 6)
            Button("Open dots") {
                model.openWindowAction = openWindow
                model.openMainWindow()
            }
            .buttonStyle(.plain).font(D.ui(13)).foregroundStyle(D.t2)
        }
        .padding(.horizontal, 6).frame(height: 30)
    }

    /// The next level this Mac's lending could reach in its pool, or the one it holds: the GB to lend, the level, the model.
    private func nextLine(for s: NetworkState) -> (gb: Int, level: Int, label: String)? {
        guard let pool = s.lendingPool else { return nil }
        let mine = pool.macs.first { $0.macID == s.thisMacID && $0.state == .lending }?.lendGB ?? 0
        let others = pool.pooledGB - mine
        func level(_ t: ModelTier) -> Int { (Ladder.tiers.firstIndex { $0.id == t.id } ?? 0) + 1 }
        if let current = Ladder.best(for: pool.pooledGB), others < current.minGB, current.minGB - others <= s.thisMac.maxLendGB {
            return (current.minGB - others, level(current), current.name)
        }
        if let next = Ladder.next(after: others), next.minGB - others <= s.thisMac.maxLendGB {
            return (next.minGB - others, level(next), next.name)
        }
        return nil
    }

    private func toggleRow(_ title: String, on: Bool, note: String?, set: @escaping (Bool) -> Void) -> some View {
        HStack {
            Text(title).font(D.ui(13)).foregroundStyle(D.t1)
            if let note { Text(note).font(D.ui(11.5)).foregroundStyle(D.t4) }
            Spacer()
            OrangeSwitch(isOn: Binding(get: { on }, set: set), label: title, width: 32)
        }
    }
}

private extension View {
    /// A group in the menu: a lighter platter on the panel.
    func module() -> some View {
        self.padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 11, style: .continuous).fill(Color.white.opacity(0.06)))
    }
}
