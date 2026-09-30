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
            ScrollView { content }
        }
    }
}

/// `dots --snapshot <folder>` walks a fresh simulation through the main moments and saves each screen as a PNG.
@MainActor
enum Snapshots {
    static func render(to dir: URL) {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let model = AppModel(backend: Simulation(fresh: true, persists: false), settings: AppSettings())
        model.isSnapshot = true
        model.persistsSettings = false
        model.signals.onPower = true
        model.signals.idleSeconds = 999
        let window = CGSize(width: 1_180, height: 780)

        // 1. Explore, before your Mac lends anywhere
        save(Shell(route: .explore), model, window, dir, "01-explore")

        // 2. Join Student Union: your Mac tips it over DeepSeek V3.1's line
        let union = model.state.pools.first { $0.id == "student-union" }!
        save(Shell(route: .pool(union.id)), model, window, dir, "02-pool-before")
        model.join(union)
        if let moment = model.moment {
            save(Shell(route: .pool(union.id)).overlay(MomentView(moment: moment, frozenAt: 1.7)), model, window, dir, "03-joined")
            save(Shell(route: .pool(union.id)).overlay(MomentView(moment: moment, frozenAt: 4.2)), model, window, dir, "04-unlocked")
        }
        model.moment = nil
        model.endorse(model.state.pool(union.id)!)
        save(Shell(route: .pool(union.id)), model, window, dir, "05-pool-after")

        // 3. The menu bar
        model.working = true
        save(MenuBarPanel(), model, CGSize(width: 320, height: 520), dir, "06-menubar", fitHeight: true)

        // 4. Ask: a lease, private, answered in Studio 9 while Priya's Mac sleeps
        let ask = model.ask
        ask.input = "Anything in here I should push back on?"
        ask.attachment = .init(name: "lease-flat-4b.pdf", text: "Tenancy agreement", url: URL(fileURLWithPath: "/tmp/lease.pdf"))
        let decision = Router.decide(question: ask.input, hasDocument: true, state: model.state, rules: model.settings.rules)
        ask.decision = decision
        ask.asked = ask.input
        ask.answer = SimulatedAnswers.lease
        if let chosen = decision.chosen {
            let plan = AnswerRunner.Plan(events: AsyncThrowingStream { $0.finish() }, isSimulated: true, endpointLabel: nil)
            var receipt = AnswerRunner.receipt(for: chosen, decision: decision, plan: plan, state: model.state)
            if let priya = receipt.macs.first(where: { $0.name == "Priya" }) {
                receipt.asleepMacID = priya.id
                receipt.handoffNote = "Priya's Mac went to sleep. The other three took over."
            }
            receipt.finished = true
            ask.receipt = receipt
            ask.phase = .done
            ask.second = .init(candidate: decision.candidates.first { $0.destination == .frontier }!)
        }
        save(AskPanelView(), model, CGSize(width: 720, height: 800), dir, "07-ask", fitHeight: true)

        // 5. Your profile, and the API page with a new key
        save(Shell(route: .profile), model, window, dir, "08-profile")
        model.createKey(name: "My scripts", reach: .all, monthlyLimit: 500)
        save(Shell(route: .api), model, window, dir, "09-api")
        save(CreateKeySheet(), model, CGSize(width: 440, height: 400), dir, "10-key-sheet", fitHeight: true)

        // 6. Starting a pool, and inviting friends to Studio 9
        save(StartPoolSheet(), model, CGSize(width: 440, height: 420), dir, "11-start-sheet", fitHeight: true)
        _ = model.backend.createInvite(poolID: "studio-9")
        save(InviteSheet(poolID: "studio-9"), model, CGSize(width: 480, height: 520), dir, "12-invite-sheet", fitHeight: true)
        save(Shell(route: .pool("studio-9")), model, window, dir, "13-studio-9")
        print("snapshots written to \(dir.path)")
    }

    private static func save<V: View>(_ view: V, _ model: AppModel, _ size: CGSize, _ dir: URL, _ name: String, fitHeight: Bool = false) {
        let content = view
            .environment(model)
            .environment(\.isSnapshot, true)
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
