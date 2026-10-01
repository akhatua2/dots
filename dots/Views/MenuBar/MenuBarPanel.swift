import SwiftUI

/// The dots menu: lend this Mac, on your terms, and see what it's earning.
struct MenuBarPanel: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openSettings) private var openSettings
    @Environment(\.isSnapshot) private var isSnapshot

    var body: some View {
        let s = model.state
        let status = model.lendingStatus
        let mac = s.thisMac
        VStack(alignment: .leading, spacing: 0) {
            // This Mac and the master switch
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("This Mac").font(D.ui(12)).foregroundStyle(D.t4)
                    Text("\(mac.kind) · \(mac.memoryGB) GB").font(D.ui(15, .semibold)).foregroundStyle(D.t1)
                }
                Spacer()
                DSwitch(isOn: Binding(get: { s.lending.isOn }, set: { model.setLendingOn($0) }), accent: true, label: "Lend this Mac")
                    .disabled(mac.maxLendGB == 0)
            }
            .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 12)

            HStack(alignment: .top, spacing: 9) {
                LiveDot(live: status.isActive).padding(.top, 5)
                VStack(alignment: .leading, spacing: 2) {
                    Text(status.headline).font(D.ui(13.5, .medium)).foregroundStyle(D.t2)
                    Text(status.detail).font(D.ui(12)).foregroundStyle(D.t4)
                }
                Spacer()
                if case .paused = status {
                    Button("Resume") { model.resume() }.buttonStyle(DButtonStyle(kind: .grey))
                }
            }
            .padding(.horizontal, 16).padding(.bottom, 12)

            if s.lending.isOn, s.myPools.count > 0, isSnapshot {
                lendingToLabel(s).padding(.horizontal, 16).padding(.bottom, 12)
            } else if s.lending.isOn, s.myPools.count > 0 {
                Menu {
                    ForEach(s.myPools) { pool in
                        Button {
                            model.join(pool)
                        } label: {
                            Text("\(pool.name) · \(pool.model?.name ?? "")")
                        }
                    }
                } label: {
                    lendingToLabel(s)
                }
                .menuStyle(.button)
                .buttonStyle(.plain)
                .padding(.horizontal, 16).padding(.bottom, 12)
            }

            Hairline()

            // How much, and when
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    CapLabel("Lend up to")
                    Spacer()
                    Text("\(s.lending.limitGB) GB").font(D.ui(15, .bold).monospacedDigit()).foregroundStyle(D.t1)
                }
                LendSlider(value: Binding(get: { s.lending.limitGB }, set: { model.setLimit($0) }),
                           maxLend: mac.maxLendGB, memory: mac.memoryGB,
                           marker: nextLine(for: s))
                HStack(alignment: .firstTextBaseline) {
                    if let line = nextLine(for: s) {
                        Text(s.lending.limitGB >= line.gb ? "Holds \(line.label)" : "\(line.gb - s.lending.limitGB) GB short of \(line.label)")
                            .font(D.ui(11.5)).foregroundStyle(s.lending.limitGB >= line.gb ? D.accentText : D.t4)
                            .lineLimit(1).layoutPriority(1)
                    }
                    Spacer(minLength: 8)
                    Text("\(mac.keptGB) GB kept for your Mac").font(D.ui(11.5)).foregroundStyle(D.t5).lineLimit(1)
                }
                toggleRow("Only when plugged in", on: s.lending.onlyWhenPluggedIn,
                          note: model.signals.onPower ? nil : "On battery now") { model.setOnlyWhenPluggedIn($0) }
                toggleRow("Pause while I'm using it", on: s.lending.pauseWhileInUse,
                          note: nil) { model.setPauseWhileInUse($0) }
            }
            .padding(16)

            Hairline()

            // Credits
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "smallcircle.filled.circle").font(.system(size: 15, weight: .medium)).foregroundStyle(D.t2)
                    .padding(.top, 2)
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(Int(s.credits).grouped)").font(D.ui(19, .bold).monospacedDigit()).foregroundStyle(D.t1)
                        .contentTransition(.numericText())
                    Text(status.isActive ? "credits · earning about \(model.creditsPerHour) an hour" : "credits")
                        .font(D.ui(12)).foregroundStyle(D.t4)
                }
                Spacer()
                Button("Ask") { AskPanelController.shared.show() }.buttonStyle(DButtonStyle(kind: .grey))
            }
            .padding(16)

            Hairline()

            HStack {
                Button(status.isActive || status == .inUse || status == .waitingForPower ? "Pause for an hour" : "Quit dots") {
                    if status.isActive || status == .inUse || status == .waitingForPower { model.pauseForAnHour() } else { NSApp.terminate(nil) }
                }
                .buttonStyle(.plain).font(D.ui(13)).foregroundStyle(D.t2)
                Spacer()
                Button { model.showSettings(openSettings) } label: {
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
            .padding(.horizontal, 16).frame(height: 42)
        }
        .frame(width: 320)
        .background(D.card)
        .preferredColorScheme(.dark)
    }

    private func lendingToLabel(_ s: NetworkState) -> some View {
        HStack {
            Text("Lending to").font(D.ui(12.5)).foregroundStyle(D.t4)
            Text(s.lendingPool?.name ?? "Pick a pool").font(D.ui(12.5, .semibold)).foregroundStyle(D.t1)
            Image(systemName: "chevron.up.chevron.down").font(.system(size: 9, weight: .bold)).foregroundStyle(D.t4)
            Spacer()
        }
    }

    /// The next model line your lending could reach in the pool you lend to.
    private func nextLine(for s: NetworkState) -> (gb: Int, label: String)? {
        guard let pool = s.lendingPool else { return nil }
        let mine = pool.macs.first { $0.macID == s.thisMacID && $0.state == .lending }?.lendGB ?? 0
        let others = pool.pooledGB - mine
        // The line to hold (the model your Mac got the pool to), or the next one to reach
        if let current = pool.model, others < current.minGB, current.minGB - others <= s.thisMac.maxLendGB {
            return (current.minGB - others, current.name)
        }
        if let next = Ladder.next(after: others), next.minGB - others <= s.thisMac.maxLendGB {
            return (next.minGB - others, next.name)
        }
        return nil
    }

    private func toggleRow(_ title: String, on: Bool, note: String?, set: @escaping (Bool) -> Void) -> some View {
        HStack {
            Text(title).font(D.ui(13.5)).foregroundStyle(D.t2)
            if let note { Text(note).font(D.ui(11.5)).foregroundStyle(D.t4) }
            Spacer()
            DSwitch(isOn: Binding(get: { on }, set: set), label: title)
        }
    }
}
