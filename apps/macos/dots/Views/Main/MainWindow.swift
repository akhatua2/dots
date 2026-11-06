import SwiftUI

struct MainWindow: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        @Bindable var model = model
        HStack(spacing: 0) {
            Sidebar()
                .frame(width: 232)
            Rectangle().fill(D.line).frame(width: 1)
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(D.bg2)
        }
        .background(D.bg1)
        .overlay {
            if let moment = model.moment {
                MomentView(moment: moment)
                    .id(moment.id)
                    .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.25), value: model.moment?.id)
        .sheet(item: $model.sheet) { sheet in
            Group {
                switch sheet {
                case .startPool: StartPoolSheet()
                case .invite(let poolID): InviteSheet(poolID: poolID)
                case .createKey: CreateKeySheet()
                case .openInvite: OpenInviteSheet()
                case .boost(let poolID): BoostSheet(poolID: poolID)
                case .recap: RecapSheet()
                }
            }
            .environment(model)
        }
        .onAppear {
            model.openWindowAction = openWindow
            showRecapIfReady()
        }
        .onChange(of: model.recapReady) { _, _ in showRecapIfReady() }
        .ignoresSafeArea()
        .preferredColorScheme(.dark)
    }

    private func showRecapIfReady() {
        guard model.recapReady, model.sheet == nil, model.moment == nil else { return }
        model.recapReady = false
        model.sheet = .recap
    }

    @ViewBuilder private var content: some View {
        switch model.route {
        case .explore: ExploreView()
        case .api: APIView()
        case .profile: ProfileView()
        case .pool(let id):
            if let pool = model.state.pool(id) { PoolView(pool: pool) } else { ExploreView() }
        }
    }
}

// MARK: - Sidebar

struct Sidebar: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Wordmark(size: 28)
                .padding(.leading, 18)
                .padding(.top, 44)
                .padding(.bottom, 20)

            VStack(spacing: 2) {
                NavRow(symbol: "safari", title: "Explore", selected: model.route == .explore) { model.open(.explore) }
                NavRow(symbol: nil, title: "Ask", trailing: "⌥ Space", selected: false) { AskPanelController.shared.show() }
                NavRow(symbol: "curlybraces", title: "API", selected: model.route == .api) { model.open(.api) }
                NavRow(symbol: "person", title: "Profile", selected: model.route == .profile,
                       badge: model.state.badges.contains(where: \.isNew)) { model.open(.profile) }
            }
            .padding(.horizontal, 8)

            Text("Your pools")
                .font(D.ui(11, .semibold)).foregroundStyle(Color(hex: 0x6E6E6E))
                .padding(.leading, 18)
                .padding(.top, 24)
                .padding(.bottom, 6)

            VStack(spacing: 2) {
                ForEach(model.state.myPools) { pool in
                    PoolRow(pool: pool, selected: model.route == .pool(pool.id),
                            lending: model.state.lendingPoolID == pool.id) { model.open(.pool(pool.id)) }
                }
                Button { model.sheet = .startPool } label: {
                    HStack(spacing: 11) {
                        Image(systemName: "plus").font(.system(size: 12, weight: .semibold)).frame(width: 28)
                        Text("Start a Pool").font(D.ui(13.5, .medium))
                        Spacer()
                    }
                    .foregroundStyle(D.t4)
                    .frame(height: 38)
                    .padding(.horizontal, 10)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 8)

            Spacer(minLength: 16)

            NavRow(symbol: "gearshape", title: "Settings", trailing: "⌘,", selected: false) { model.showSettings(openSettings) }
                .padding(.horizontal, 8)

            ThisMacCard()
                .padding(10)
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .background(D.bg1)
    }
}

struct Wordmark: View {
    var size: CGFloat
    var body: some View {
        HStack(alignment: .lastTextBaseline, spacing: size * 0.06) {
            Text("dots").font(D.display(size)).foregroundStyle(D.t1)
            Circle().fill(D.accent).frame(width: size * 0.2, height: size * 0.2)
        }
        .accessibilityElement()
        .accessibilityLabel("dots")
    }
}

struct NavRow: View {
    var symbol: String?
    var title: String
    var trailing: String? = nil
    var selected: Bool
    var badge = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Group {
                    if let symbol {
                        Image(systemName: symbol).font(.system(size: 13, weight: .medium))
                    } else {
                        Circle().fill(D.accent).frame(width: 8, height: 8)
                    }
                }
                .frame(width: 18)
                .foregroundStyle(selected ? D.t1 : D.t3)
                Text(title).font(D.ui(13.5, selected ? .semibold : .medium)).foregroundStyle(selected ? D.t1 : D.t3)
                if badge { Circle().fill(D.accent).frame(width: 6, height: 6) }
                Spacer()
                if let trailing { Text(trailing).font(D.ui(11.5)).foregroundStyle(D.t5) }
            }
            .padding(.horizontal, 10)
            .frame(height: 30)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(selected ? D.sel : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct PoolRow: View {
    @Environment(\.isSnapshot) private var isSnapshot
    var pool: Pool
    var selected: Bool
    var lending: Bool
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 11) {
                MiniRings(pool: pool, live: lending)
                Text(pool.name).font(D.ui(13.5, selected ? .semibold : .medium)).foregroundStyle(selected ? D.t1 : D.t2).lineLimit(1)
                Spacer(minLength: 0)
                if lending { Circle().fill(D.accent).frame(width: 6, height: 6).modifier(Blink(on: !isSnapshot)) }
            }
            .padding(.horizontal, 10)
            .frame(height: 38)
            .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(selected ? Color(hex: 0x262626) : .clear))
            .contentShape(Rectangle())
            .help("\(pool.totalMacs.grouped) Macs · \(pool.model?.name ?? "No model yet")")
        }
        .buttonStyle(.plain)
    }
}

/// This Mac's lending and your credits, always in view.
struct ThisMacCard: View {
    @Environment(AppModel.self) private var model
    @Environment(\.isSnapshot) private var isSnapshot

    var body: some View {
        let status = model.lendingStatus
        let s = model.state
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                if status.isActive {
                    Circle().fill(D.accent).frame(width: 7, height: 7).modifier(Blink(on: !isSnapshot))
                } else {
                    LiveDot(live: false, size: 7)
                }
                Text(status.isActive ? "Live in \(s.lendingPool?.name ?? "your pool")" : status.headline)
                    .font(D.ui(13, .semibold)).foregroundStyle(D.t1).lineLimit(1)
            }
            Text(status.isActive ? "\(Int(s.credits).grouped) credits · +\(model.creditsPerHour) an hour" : "\(Int(s.credits).grouped) credits")
                .font(D.ui(12.5).monospacedDigit()).foregroundStyle(D.t3).lineLimit(1)
                .contentTransition(.numericText())
                .padding(.leading, 15)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color(hex: 0x1C1C1C)))
        .contentShape(Rectangle())
        .onTapGesture { model.open(.profile) }
    }
}
