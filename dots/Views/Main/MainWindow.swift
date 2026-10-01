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
                }
            }
            .environment(model)
        }
        .onAppear { model.openWindowAction = openWindow }
        .ignoresSafeArea()
        .preferredColorScheme(.dark)
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
            Wordmark(size: 23)
                .padding(.leading, 18)
                .padding(.top, 46)
                .padding(.bottom, 20)

            VStack(spacing: 2) {
                NavRow(symbol: "safari", title: "Explore", selected: model.route == .explore) { model.open(.explore) }
                NavRow(symbol: nil, title: "Ask", trailing: "⌥ Space", selected: false) { AskPanelController.shared.show() }
                NavRow(symbol: "curlybraces", title: "API", selected: model.route == .api) { model.open(.api) }
                NavRow(symbol: "person", title: "Profile", selected: model.route == .profile,
                       badge: model.state.badges.contains(where: \.isNew)) { model.open(.profile) }
            }
            .padding(.horizontal, 8)

            CapLabel("Your pools")
                .padding(.leading, 18)
                .padding(.top, 26)
                .padding(.bottom, 8)

            VStack(spacing: 2) {
                ForEach(model.state.myPools) { pool in
                    PoolRow(pool: pool, selected: model.route == .pool(pool.id),
                            lending: model.state.lendingPoolID == pool.id) { model.open(.pool(pool.id)) }
                }
                Button { model.sheet = .startPool } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "plus").font(.system(size: 12, weight: .semibold)).frame(width: 28)
                        Text("Start a pool").font(D.ui(13, .medium))
                        Spacer()
                    }
                    .foregroundStyle(D.t4)
                    .frame(height: 32)
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
    var pool: Pool
    var selected: Bool
    var lending: Bool
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                EmblemView(macs: pool.totalMacs, phase: pool.phase, size: 28)
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 6) {
                        Text(pool.name).font(D.ui(13, .semibold)).foregroundStyle(D.t2).lineLimit(1)
                        if lending { Circle().fill(D.accent).frame(width: 6, height: 6) }
                    }
                    Text("\(pool.totalMacs) Macs · \(pool.model?.name ?? "No model yet")")
                        .font(D.ui(11)).foregroundStyle(D.t4).lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 8)
            .frame(height: 42)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(selected ? D.sel : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// This Mac's lending and your credits, always in view.
struct ThisMacCard: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let status = model.lendingStatus
        let s = model.state
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 8) {
                LiveDot(live: status.isActive).padding(.top, 4)
                VStack(alignment: .leading, spacing: 2) {
                    Text(status.isActive ? "Lending \(s.lending.limitGB) GB" : status.headline)
                        .font(D.ui(13, .semibold)).foregroundStyle(D.t1).lineLimit(1)
                    Text(status.isActive ? "\(s.thisMac.kind) · \(s.lendingPool?.name ?? "")" : status.detail)
                        .font(D.ui(11.5)).foregroundStyle(D.t4).lineLimit(1)
                }
            }
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "smallcircle.filled.circle").font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(D.t2).padding(.top, 2)
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(Int(s.credits).grouped) credits").font(D.ui(13, .semibold)).foregroundStyle(D.t1)
                        .contentTransition(.numericText())
                    Text(status.isActive ? "+\(model.creditsPerHour) an hour" : "Earn by lending")
                        .font(D.ui(11.5)).foregroundStyle(D.t4)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(radius: 11)
        .onTapGesture { model.open(.profile) }
    }
}
