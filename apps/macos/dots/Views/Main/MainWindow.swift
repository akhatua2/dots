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
        .overlay(alignment: .top) { TitleDrag() }
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
        .background(WindowChrome(route: model.route))
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
        case .ask: AskScreen()
        case .settings: SettingsView(inline: true)
        case .pool(let id):
            if let pool = model.state.pool(id) { PoolView(pool: pool) } else { ExploreView() }
        }
    }
}

// MARK: - Sidebar

struct Sidebar: View {
    @Environment(AppModel.self) private var model
    @AppStorage("sidebar.poolsFolded") private var poolsFolded = false
    @AppStorage("sidebar.chatsFolded") private var chatsFolded = false

    private func newChat() {
        model.ask.clear()
        model.chats.startNew()
        model.showAsk()
    }

    private func open(_ chat: Chat) {
        model.ask.clear()
        model.chats.open(chat.id)
        model.showAsk()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Wordmark(size: 28)
                .padding(.leading, 18)
                .padding(.top, 44)
                .padding(.bottom, 20)

            VStack(spacing: 2) {
                NavRow(symbol: "safari", title: "Explore", selected: model.route == .explore) { model.open(.explore) }
                NavRow(symbol: nil, title: "Ask", trailing: "⌥ Space", selected: model.route == .ask) { model.showAsk() }
                NavRow(symbol: "curlybraces", title: "API", selected: model.route == .api) { model.open(.api) }
                NavRow(symbol: "person", title: "Profile", selected: model.route == .profile,
                       badge: model.state.badges.contains(where: \.isNew)) { model.open(.profile) }
            }
            .padding(.horizontal, 8)

            // Your pools, then your chats, each folding away under its heading.
            LiveScroll {
                VStack(alignment: .leading, spacing: 0) {
                    SidebarSection(title: "Your pools", folded: $poolsFolded) {
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
                    SidebarSection(title: "Chats", folded: $chatsFolded, action: ("square.and.pencil", "New chat", newChat)) {
                        ForEach(model.chats.chats) { chat in
                            ChatRow(chat: chat, selected: model.route == .ask && chat.id == model.chats.currentID) { open(chat) }
                                .contextMenu {
                                    Button("Delete Chat", role: .destructive) {
                                        if chat.id == model.chats.currentID { model.ask.clear() }
                                        model.chats.delete(chat.id)
                                    }
                                }
                        }
                        if model.chats.chats.isEmpty {
                            Text("Your conversations show up here.")
                                .font(D.ui(12)).foregroundStyle(D.t5)
                                .padding(.horizontal, 10).padding(.vertical, 6)
                        }
                    }
                }
                .padding(.horizontal, 8)
            }
            .scrollIndicators(.never)

            Spacer(minLength: 8)

            NavRow(symbol: "gearshape", title: "Settings", trailing: "⌘,", selected: model.route == .settings) { model.showSettings() }
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

// MARK: - Window chrome

/// The window has no title bar, so the strip where one would be moves it, and a double click zooms it.
private struct TitleDrag: View {
    var body: some View {
        Color.clear
            .frame(height: 28)
            .contentShape(Rectangle())
            .gesture(WindowDragGesture())
            .allowsWindowActivationEvents(true)
            .onTapGesture(count: 2) { NSApp.keyWindow?.performZoom(nil) }
    }
}

/// Hides the title bar's own background and separator, so the content runs to the top edge
/// and the traffic lights sit on it. Without this, macOS draws a bar and a line above the content.
private struct WindowChrome: NSViewRepresentable {
    /// Each screen brings its own scroll view, and with it a new strip to hide, so a change of screen re-applies this.
    var route: Route

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { Self.apply(to: view.window) }
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {
        DispatchQueue.main.async { Self.apply(to: view.window) }
        // The new screen's scroll view is laid out a moment after this update.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { Self.apply(to: view.window) }
    }

    private static func apply(to window: NSWindow?) {
        guard let window else { return }
        window.titlebarAppearsTransparent = true
        window.titlebarSeparatorStyle = .none
        window.titleVisibility = .hidden
        window.styleMask.insert(.fullSizeContentView)
        // Only the strip along the top moves the window (see TitleDrag), so dragging in a list or a chat selects.
        window.isMovableByWindowBackground = false
        if let root = window.contentView?.superview { hideScrollPockets(in: root) }
    }

    /// macOS 26 puts a "scroll pocket" under the title bar: a strip with a hard edge, drawn whenever a scroll view
    /// sits beneath it. dots has no title bar to blend with, so the strip shows as a stray line across the top.
    /// SwiftUI has no switch that reaches the one in the title bar, so it is found by class name and hidden.
    /// If AppKit renames it, nothing matches and the window is simply left as macOS draws it.
    private static func hideScrollPockets(in view: NSView) {
        for sub in view.subviews {
            let name = String(describing: type(of: sub))
            if name == "NSScrollPocket" || name == "_NSTitlebarDecorationView" { sub.isHidden = true }
            hideScrollPockets(in: sub)
        }
    }
}


/// A heading in the sidebar that folds its rows away, with an optional button on the right.
struct SidebarSection<Content: View>: View {
    var title: String
    @Binding var folded: Bool
    var action: (symbol: String, label: String, run: () -> Void)? = nil
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Button { withAnimation(.easeOut(duration: 0.18)) { folded.toggle() } } label: {
                    HStack(spacing: 5) {
                        Text(title).font(D.ui(11, .semibold))
                        Image(systemName: "chevron.right")
                            .font(.system(size: 8, weight: .bold))
                            .rotationEffect(.degrees(folded ? 0 : 90))
                    }
                    .foregroundStyle(Color(hex: 0x6E6E6E))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(folded ? "Show \(title)" : "Hide \(title)")
                Spacer()
                if let action {
                    Button(action: action.run) { Image(systemName: action.symbol).font(.system(size: 11.5, weight: .medium)) }
                        .buttonStyle(.plain).foregroundStyle(D.t4)
                        .help(action.label)
                }
            }
            .padding(.horizontal, 10)
            .padding(.top, 20)
            .padding(.bottom, 6)
            if !folded { content }
        }
    }
}

/// A chat in the sidebar: its first question, and when it was last used.
struct ChatRow: View {
    var chat: Chat
    var selected: Bool
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 1) {
                Text(chat.title).font(D.ui(13, selected ? .semibold : .regular)).foregroundStyle(selected ? D.t1 : D.t2)
                    .lineLimit(1)
                Text(chat.updated, format: .relative(presentation: .named)).font(D.ui(11)).foregroundStyle(D.t5)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(selected ? Color(hex: 0x262626) : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
