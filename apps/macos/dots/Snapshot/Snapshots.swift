import AppKit
import SwiftUI

/// Scrolls in the app; lays out flat when drawn into an image (scroll views are AppKit, and don't draw offscreen).
struct LiveScroll<Content: View>: View {
    @Environment(\.isSnapshot) private var isSnapshot
    @ViewBuilder var content: Content

    var body: some View {
        if isSnapshot {
            content.fixedSize(horizontal: false, vertical: true).frame(minHeight: 0, maxHeight: .infinity, alignment: .top)
        } else {
            ScrollView { content }.ignoresSafeArea(.container, edges: .top).withoutTopEdgeLine()
        }
    }
}

extension View {
    /// macOS 26 draws a line and a soft blur where a scroll view meets the title bar. dots has no title bar, so it goes.
    @ViewBuilder func withoutTopEdgeLine() -> some View {
        if #available(macOS 26, *) { scrollEdgeEffectHidden(true, for: .top) } else { self }
    }
}

/// `dots --snapshot <folder>` walks a fresh network through the main moments and saves each screen as a PNG.
/// It runs a mock cloud of its own, fresh and never saved, so it never touches your network.
@MainActor
enum Snapshots {
    static func render(to dir: URL) async {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let cloud = MockCloud(port: UInt16.random(in: 19_300..<19_500), fresh: true, persists: false)
        try? cloud.start()
        let client = CloudClient(baseURL: URL(string: "http://127.0.0.1:\(cloud.port)")!, token: AppModel.mockToken)
        let model = AppModel(backend: CloudBackend(client: client, persists: false), settings: AppSettings())
        model.useMock(cloud)
        model.isSnapshot = true
        model.persistsSettings = false
        // The API server doesn't run for snapshots; draw it the way it looks in the app
        model.gatewayState = "Running on port \(model.settings.gatewayPort)"
        model.signals.onPower = true
        model.signals.idleSeconds = 999
        await model.backend.syncNow()
        let window = CGSize(width: 1_440, height: 900)

        // 1. Explore, before your Mac lends anywhere, with Sam's invite at the top
        save(Shell(route: .explore), model, window, dir, "01-explore")

        // 2. Join Student Union: your Mac tips it over GLM 5.3's line
        let union = model.state.pools.first { $0.id == "student-union" }!
        save(Shell(route: .pool(union.id)), model, window, dir, "02-pool-before")
        model.join(union)
        if let moment = model.moment {
            save(Shell(route: .pool(union.id)).overlay(MomentView(moment: moment, frozenAt: 1.7)), model, window, dir, "03-joined")
            save(Shell(route: .pool(union.id)).overlay(MomentView(moment: moment, frozenAt: 4.2)), model, window, dir, "04-unlocked")
        }
        model.moment = nil
        model.endorse(model.state.pool(union.id)!)
        try? await Task.sleep(for: .milliseconds(300))
        save(Shell(route: .pool(union.id)), model, window, dir, "05-pool-after")

        // 3. The menu bar, while the hub has work for this Mac
        cloud.forcedWorking = true
        await model.backend.beatNow(active: true)
        save(MenuBarPanel(), model, CGSize(width: 320, height: 560), dir, "06-menubar", fitHeight: true)

        // 4. Ask: a lease, private, answered in Studio 9 while Priya's Mac sleeps
        let ask = model.ask
        ask.input = "Anything in here I should push back on?"
        ask.attachment = .init(name: "lease-flat-4b.pdf", text: "Tenancy agreement", url: URL(fileURLWithPath: "/tmp/lease.pdf"))
        let decision = Router.decide(question: ask.input, hasDocument: true, state: model.state, rules: model.settings.rules)
        ask.decision = decision
        ask.asked = ask.input
        ask.answer = SimulatedAnswers.lease
        if let chosen = decision.chosen, let pool = model.state.pool(chosen.poolID) {
            var receipt = AnswerRunner.receipt(for: chosen, decision: decision, via: "the dots hub", paidWithKey: false)
            receipt.macs = pool.macs.filter { $0.state == .lending }.compactMap { m in
                guard let d = model.state.device(m.macID), let p = model.state.person(d.ownerID) else { return nil }
                return ReceiptMac(id: m.macID, initial: d.id == model.state.thisMacID ? "You" : p.initial, name: p.name)
            }
            if let priya = receipt.macs.first(where: { $0.name == "Priya" }) {
                receipt.asleepMacID = priya.id
                receipt.handoffNote = "Priya's Mac went to sleep. The other three took over."
            }
            receipt.inputTokens = 1_420
            receipt.outputTokens = 96
            receipt.isSimulated = true
            receipt.finished = true
            ask.receipt = receipt
            ask.phase = .done
            ask.second = .init(candidate: decision.candidates.first { $0.destination == .frontier }!)
        }
        save(AskPanelView(), model, CGSize(width: 720, height: 800), dir, "07-ask", fitHeight: true)
        // The same answer mid-stream, with the draft model's next words waiting on the pool, greyed out
        let full = ask.answer
        let confirmed = full.index(full.startIndex, offsetBy: min(150, full.count))
        let proposed = full.index(confirmed, offsetBy: min(28, full.distance(from: confirmed, to: full.endIndex)))
        ask.answer = String(full[..<confirmed])
        ask.draft = String(full[confirmed..<proposed])
        ask.phase = .streaming
        ask.checks = [(3, 4), (1, 4), (4, 4), (0, 4), (2, 4), (4, 4), (3, 4), (1, 4), (4, 4), (2, 4)]
            .map { DraftCheck(kept: $0.0, proposed: $0.1) }
        save(AskPanelView(), model, CGSize(width: 720, height: 800), dir, "07-ask-drafting", fitHeight: true)
        ask.answer = full
        ask.draft = ""
        ask.checks = []
        ask.phase = .done
        // A photo waiting to be asked about
        ask.clear()
        ask.input = "Which way does the road go?"
        ask.attachment = .init(name: "big-sur-road.jpg", text: "", url: URL(fileURLWithPath: "/tmp/big-sur-road.jpg"),
                               picture: SampleData.photo)
        save(AskPanelView(), model, CGSize(width: 720, height: 800), dir, "07-ask-photo", fitHeight: true)
        ask.clear()
        // A picture as an answer, three steps into its drawing
        let forming = Drawing.Progress(image: SampleData.photo, step: 3, of: 8,
                                                    firstStep: (1, Date(timeIntervalSinceNow: -40)), latestAt: Date(timeIntervalSinceNow: -5))
        save(PictureAnswer(image: SampleData.photo, progress: forming).padding(28).background(D.bg2),
             model, CGSize(width: 540, height: 600), dir, "07-ask-drawing", fitHeight: true)

        // 5. A week of work, and your profile with this week's note
        await model.backend.beatNow(active: true, adding: 31 * 3_600)
        cloud.issueNote()
        await model.backend.syncNow()
        save(Shell(route: .profile), model, window, dir, "08-profile")

        // 6. The API page, after a few requests went through dots, and a new key
        let baseline = model.settings.savingsBaseline
        for (dest, place, mdl, credits, usd, i, o) in [
            (Destination.thisMac, "This Mac", "qwen3-8b", 0, 0.0, 820, 240),
            (.yourPool, "Studio 9", "MiniMax M2.7", 0, 0.0, 2_400, 610),
            (.publicPool, "Open Science", "Kimi K2.5", 4, 0.0, 6_100, 900),
            (.frontier, "Anthropic", "Claude Opus 5.5", 13, 0.0, 1_500, 600),
            (.frontier, "Anthropic", "Claude Opus 5.5", 0, Pricing.usd(baseline, input: 3_200, output: 800), 3_200, 800),
        ] {
            model.recordAnswer(destination: dest, place: place, model: mdl, source: "My scripts", credits: credits, usd: usd,
                               isPrivate: dest == .thisMac || dest == .yourPool, input: i, output: o, measured: true,
                               fallback: place == "Studio 9" ? "This Mac didn't answer, so it went to Studio 9." : nil, format: .openai)
        }
        model.createKey(name: "My scripts", reach: .all, monthlyLimit: 500, monthlyCapUSD: 20)
        save(Shell(route: .api), model, window, dir, "09-api")
        save(CreateKeySheet(), model, CGSize(width: 440, height: 400), dir, "10-key-sheet", fitHeight: true)

        // 7. Starting a pool, inviting friends to Studio 9, and opening Sam's invite
        save(StartPoolSheet(), model, CGSize(width: 440, height: 420), dir, "11-start-sheet", fitHeight: true)
        _ = model.backend.createInvite(poolID: "studio-9")
        save(InviteSheet(poolID: "studio-9"), model, CGSize(width: 480, height: 520), dir, "12-invite-sheet", fitHeight: true)
        save(Shell(route: .pool("studio-9")), model, window, dir, "13-studio-9")
        // Room for Sam's invite: Student Union gives back what it has
        model.setShare(union, gb: 0)
        await model.backend.beatNow(active: model.lendingStatus.isActive)
        if let invite = model.backend.invites.first {
            model.openInvite = OpenInvite(code: invite.code, preview: invite)
            save(OpenInviteSheet(), model, CGSize(width: 480, height: 420), dir, "15-open-invite", fitHeight: true)
        }

        // 8. Settings, with Anthropic set up for a key that works in more than one workspace
        model.settings.frontierEndpoint = EndpointConfig(enabled: true, label: "Anthropic", baseURL: "https://api.anthropic.com/v1",
                                                        model: "claude-opus-5-5", keychainAccount: "dots-snapshot-none",
                                                        workspaceID: "wrkspc_01JwQvzr7rXLA5AGx3HKfFUJ")
        save(SettingsView(), model, CGSize(width: 560, height: 640), dir, "14-settings", fitHeight: true)
        save(Shell(route: .settings), model, window, dir, "14-settings-page")

        // 9. Sam's invite, taken with 10 GB: Late Night Lab at 2 AM, 6 GB short of Level 2, with GLM Boost coming
        let lateNight = Date().nightStart.addingTimeInterval(14.1 * 3_600)
        if let invite = model.backend.invites.first(where: { $0.code == "LATE7Q" }) {
            model.acceptInvite(invite, lendGB: 10)
            await model.backend.settled()
            try? await Task.sleep(for: .milliseconds(300))
            model.moment = nil
            cloud.forcedWorking = true
            await model.backend.beatNow(active: true, adding: 2 * 3_600)
            await model.backend.syncNow()
            save(Shell(route: .pool("late-night-lab")), model, window, dir, "16-late-night-lab", clock: lateNight)
            save(BoostSheet(poolID: "late-night-lab"), model, CGSize(width: 560, height: 720), dir, "17-boost", fitHeight: true, clock: lateNight)
            save(MenuBarPanel(), model, CGSize(width: 320, height: 600), dir, "21-menubar-lab", fitHeight: true, clock: lateNight)
            save(Shell(route: .profile), model, window, dir, "18-profile-lab", clock: lateNight)
            // Lending 6 GB more takes it to Level 2
            model.setShare(model.state.pool("late-night-lab")!, gb: 16)
            await model.backend.beatNow(active: true)
            if let moment = model.moment {
                save(Shell(route: .pool("late-night-lab")).overlay(MomentView(moment: moment, frozenAt: 3.2)), model, window, dir, "19-level-up", clock: lateNight)
                model.moment = nil
            }
            save(Shell(route: .pool("late-night-lab")), model, window, dir, "20-lab-level-2", clock: lateNight)
            // The morning after
            save(RecapSheet(), model, CGSize(width: 720, height: 800), dir, "22-recap", fitHeight: true,
                 clock: Date().nightStart.addingTimeInterval(19.03 * 3_600))
        }
        cloud.stop()
        print("snapshots written to \(dir.path)")
    }

    private static func save<V: View>(_ view: V, _ model: AppModel, _ size: CGSize, _ dir: URL, _ name: String, fitHeight: Bool = false,
                                      clock: Date? = nil) {
        let content = view
            .environment(model)
            .environment(\.isSnapshot, true)
            .environment(\.artClock, clock ?? Date())
            .environment(\.colorScheme, .dark)
            .frame(width: size.width, height: fitHeight ? nil : size.height)
        let renderer = ImageRenderer(content: content)
        renderer.scale = 2
        guard let cg = renderer.cgImage else { print("couldn't render \(name)"); return }
        let rep = NSBitmapImageRep(cgImage: cg)
        guard let png = rep.representation(using: .png, properties: [:]) else { return }
        try? png.write(to: dir.appendingPathComponent("\(name).png"))
    }

    /// The app window's layout, without the live-window machinery.
    struct Shell: View {
        @Environment(AppModel.self) private var model
        var route: Route

        var body: some View {
            HStack(spacing: 0) {
                Sidebar().frame(width: 232)
                    .overlay(alignment: .topLeading) {
                        HStack(spacing: 8) {
                            ForEach(0..<3) { _ in Circle().fill(Color(hex: 0x3A3A3A)).frame(width: 12, height: 12) }
                        }
                        .padding(.leading, 18).padding(.top, 18)
                    }
                Rectangle().fill(D.line).frame(width: 1)
                Group {
                    switch route {
                    case .explore: ExploreView()
                    case .api: APIView()
                    case .profile: ProfileView()
                    case .ask: AskScreen()
                    case .draw: DrawScreen()
                    case .settings: SettingsView(inline: true)
                    case .pool(let id): PoolView(pool: model.state.pool(id)!)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                .background(D.bg2)
                .clipped()
            }
            .background(D.bg1)
        }
    }
}
