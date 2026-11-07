import AppKit
import Observation
import SwiftUI
import UserNotifications

enum Route: Hashable {
    case explore, api, profile, ask, draw, settings
    case pool(String)
}

enum SheetKind: Identifiable, Equatable {
    case startPool, invite(String), createKey, openInvite, boost(String), recap
    var id: String {
        switch self {
        case .recap: "recap"
        case .startPool: "start"
        case .invite(let p): "invite-\(p)"
        case .boost(let p): "boost-\(p)"
        case .createKey: "key"
        case .openInvite: "open-invite"
        }
    }
}

/// The two big moments, shown over the app window.
enum Moment: Identifiable, Equatable {
    case joined(poolID: String, macNumber: Int, unlock: UnlockEvent?, waiting: String?)
    case unlocked(poolID: String, event: UnlockEvent)
    var id: String {
        switch self {
        case .joined(let p, _, _, _): "joined-\(p)"
        case .unlocked(let p, let e): "unlocked-\(p)-\(e.modelID)"
        }
    }
}

enum LendingStatus: Equatable {
    case cantLend, off, noPool, paused(until: Date), waitingForPower, inUse
    case lending(pool: String, working: Bool)

    var isActive: Bool { if case .lending = self { true } else { false } }

    var headline: String {
        switch self {
        case .cantLend: "This Mac can't lend"
        case .off: "Not lending"
        case .noPool: "Pick a pool to lend to"
        case .paused: "Paused"
        case .waitingForPower: "Waiting for power"
        case .inUse: "Paused while you use it"
        case let .lending(pool, working): working ? "Working for \(pool)" : "Lending to \(pool)"
        }
    }

    var detail: String {
        switch self {
        case .cantLend: "It has no memory to spare."
        case .off: "Switch it on to earn credits."
        case .noPool: "Join a pool in Explore."
        case let .paused(until): "Back at \(until.formatted(date: .omitted, time: .shortened))"
        case .waitingForPower: "Lends once it's plugged in"
        case .inUse: "Resumes after two idle minutes"
        case let .lending(_, working): working ? "Running prompts for the pool" : "Waiting for work"
        }
    }
}

struct RevealedKey: Equatable {
    var id: String
    var secret: String
}

/// An invite link being looked at before joining.
struct OpenInvite: Equatable {
    var code: String
    var preview: InviteDetails?
    var error: String?
    /// A friend's cloud the invite is for, when it isn't the one this Mac uses
    var cloud: URL?
}

@MainActor
@Observable
final class AppModel {
    static let shared = AppModel()

    let backend: CloudBackend
    let signals = SignalMonitor()
    /// This Mac's real worker, run while it lends to a real cloud.
    @ObservationIgnored let workerProcess = WorkerProcess()
    let ask = AskController()
    /// The mock cloud, when it runs on this Mac
    private(set) var mock: MockCloud?

    var settings: AppSettings {
        didSet {
            guard settings != oldValue else { return }
            if persistsSettings { settings.save() }
            if settings.gatewayEnabled != oldValue.gatewayEnabled || settings.gatewayPort != oldValue.gatewayPort { restartGateway() }
            if settings.cloud != oldValue.cloud { connectCloud() }
        }
    }
    var route: Route = .explore
    var moment: Moment?
    var sheet: SheetKind?
    var revealedKey: RevealedKey?
    var openInvite: OpenInvite?
    var gatewayState = "Off"
    var isSnapshot = false
    /// False for snapshots and the self-test, so they never change your saved settings
    @ObservationIgnored var persistsSettings = true

    @ObservationIgnored var openWindowAction: OpenWindowAction?
    @ObservationIgnored private var timer: Timer?
    @ObservationIgnored private var lastTick = Date()
    @ObservationIgnored private var gateway: LocalGateway?
    /// Unlocks already shown, so the cloud's report of one the app showed itself doesn't show it twice
    @ObservationIgnored private var shownUnlocks = Set<String>()
    /// How fast the model on this Mac answers, measured, and when it last failed to
    @ObservationIgnored var localTokPerSec: Double?
    @ObservationIgnored var localDownUntil: Date?

    init(backend: CloudBackend? = nil, settings: AppSettings? = nil) {
        let settings = settings ?? .load()
        self.settings = settings
        self.backend = backend ?? CloudBackend(client: Self.client(for: settings))
        // The self-test and snapshots pass a backend of their own; they must not touch your saved chats.
        self.chats = ChatStore(persists: backend == nil)
        self.draw = DrawController(persists: backend == nil)
        self.draw.maxPixels = settings.imageMaxPixels
    }

    /// Your Ask conversations, kept on this Mac.
    let chats: ChatStore
    /// Draw: pictures, and changes to them, kept on this Mac
    let draw: DrawController

    var state: NetworkState { backend.state }
    /// Your Mac is working on someone's question right now, as the hub says
    var working: Bool { lendingStatus.isActive && (backend.worker?.working ?? false) }

    // MARK: The cloud

    static let mockToken = "dots_mock_local"

    static func client(for settings: AppSettings, token: String? = Keychain.get(cloudTokenAccount)) -> CloudClient {
        if settings.cloud.useMock || URL(string: settings.cloud.address)?.host == nil {
            return CloudClient(baseURL: URL(string: "http://127.0.0.1:\(settings.cloud.mockPort)")!, token: mockToken)
        }
        return CloudClient(baseURL: URL(string: settings.cloud.address)!, token: token ?? "")
    }

    /// Where the token for this Mac's cloud is kept
    static let cloudTokenAccount = "dots-cloud"
    /// The self-test's token, kept in memory so it never touches the Keychain
    @ObservationIgnored private var testCloudToken: String?
    /// The token this Mac uses with its cloud: in the Keychain, or in memory for the self-test and snapshots
    var cloudToken: String? {
        get { persistsSettings ? Keychain.get(Self.cloudTokenAccount) : testCloudToken }
        set {
            if persistsSettings { Keychain.set(newValue ?? "", for: Self.cloudTokenAccount) } else { testCloudToken = newValue }
        }
    }

    /// Starts the mock cloud on this Mac when it's the one in use, and points the app at the right cloud.
    func connectCloud() {
        backend.client = Self.client(for: settings, token: cloudToken)
        if settings.cloud.useMock {
            if mock?.port != UInt16(settings.cloud.mockPort) {
                mock?.stop()
                let m = MockCloud(port: UInt16(settings.cloud.mockPort))
                m.upstream = { [weak self] in
                    guard let e = self?.settings.poolEndpoint, e.isReady else { return nil }
                    return e
                }
                do { try m.start() } catch { NSLog("dots: the mock cloud couldn't start: \(error)") }
                mock = m
            }
        } else {
            mock?.stop()
            mock = nil
        }
        Task { await backend.syncNow() }
    }

    /// For the self-test and snapshots: a mock cloud of their own, fresh and never saved.
    func useMock(_ m: MockCloud) {
        mock = m
        backend.client = CloudClient(baseURL: URL(string: "http://127.0.0.1:\(m.port)")!, token: Self.mockToken)
    }

    // MARK: Lending

    var lendingStatus: LendingStatus {
        let s = state
        guard s.thisMac.maxLendGB > 0 else { return .cantLend }
        guard s.lending.isOn else { return .off }
        guard !s.lendingPools.isEmpty else { return .noPool }
        if let until = s.lending.pausedUntil, until > .now { return .paused(until: until) }
        // A small Mac lends only while it's plugged in and nobody is using it, whatever the switches say
        let small = s.thisMac.lendsOnlyWhenIdle
        if (s.lending.onlyWhenPluggedIn || small) && !signals.onPower { return .waitingForPower }
        if (s.lending.pauseWhileInUse || small) && signals.inUse { return .inUse }
        return .lending(pool: s.lendingPools.map(\.name).formatted(.list(type: .and)), working: backend.worker?.working ?? false)
    }

    /// The sidebar's earning line: what the ledger says this Mac earned in the last hour, or, from the mock,
    /// its estimate for an hour of lending.
    var earningLine: String {
        if let earning = backend.earning { return "+\(earning.lastHour.grouped) this hour" }
        let s = state
        guard !s.lendingShares.isEmpty else { return "" }
        let perHour = s.lendingShares.reduce(0) { sum, share in sum + (s.pool(share.poolID)?.creditsPerHour(lendGB: share.gb) ?? 0) }
        return "+\(perHour) an hour"
    }

    func setLendingOn(_ on: Bool) {
        backend.updateLending { $0.isOn = on; if on { $0.pausedUntil = nil } }
        refreshLending()
    }
    /// How much this Mac lends a pool it is in, at most what its other pools leave; 0 stops lending there.
    func setShare(_ pool: Pool, gb: Int) {
        backend.setShare(poolID: pool.id, gb: min(max(0, gb), state.mostGB(for: pool.id)))
        refreshLending()
    }
    func setOnlyWhenPluggedIn(_ on: Bool) { backend.updateLending { $0.onlyWhenPluggedIn = on }; refreshLending() }
    func setPauseWhileInUse(_ on: Bool) { backend.updateLending { $0.pauseWhileInUse = on }; refreshLending() }
    func pauseForAnHour() { backend.updateLending { $0.pausedUntil = Date().addingTimeInterval(3_600) }; refreshLending() }

    // MARK: Overnight

    /// Lending overnight keeps this Mac awake until 7 AM, then lets it sleep.
    private(set) var overnightUntil: Date?
    @ObservationIgnored private var overnightActivity: NSObjectProtocol?

    func lendOvernight(_ on: Bool) {
        if let activity = overnightActivity { ProcessInfo.processInfo.endActivity(activity) }
        overnightActivity = nil
        overnightUntil = nil
        guard on else { return }
        var seven = DateComponents()
        seven.hour = 7
        overnightUntil = Calendar.current.nextDate(after: .now, matching: seven, matchingPolicy: .nextTime)
        if !isSnapshot {
            overnightActivity = ProcessInfo.processInfo.beginActivity(options: [.idleSystemSleepDisabled],
                                                                      reason: "dots is lending this Mac overnight, until 7 AM")
        }
        if !state.lending.isOn { setLendingOn(true) }
    }
    func resume() { backend.updateLending { $0.pausedUntil = nil }; refreshLending() }

    /// Moves this Mac in or out of its pool, and shows the unlock if that tipped the pool over a line.
    @discardableResult
    func refreshLending() -> UnlockEvent? {
        let active = lendingStatus.isActive
        // With a real cloud, lending runs the real worker; the mock simulates the work instead.
        if !backend.isMock {
            workerProcess.reconcile(lend: active, shares: state.lendingShares, ticket: backend.hub?.ticket)
        }
        let unlock = backend.applyLendingStatus(active: active)
        if let unlock, let pid = unlock.poolID ?? state.lendingShares.first?.poolID { show(unlock, in: pid) }
        return unlock
    }

    private func show(_ unlock: UnlockEvent, in poolID: String) {
        guard shownUnlocks.insert("\(poolID)-\(unlock.modelID)").inserted, moment == nil else { return }
        moment = .unlocked(poolID: poolID, event: unlock)
    }

    // MARK: Life cycle

    func start() {
        connectCloud()
        signals.start()
        refreshLending()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        restartGateway()
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    private func tick() {
        if let until = overnightUntil, until <= .now { lendOvernight(false) }
        runBoosts()
        checkRecap()
        let now = Date()
        let dt = now.timeIntervalSince(lastTick) * settings.demoSpeed
        lastTick = now
        refreshLending()
        backend.tick(seconds: dt, lendingActive: lendingStatus.isActive, working: working)
        if let u = backend.pendingUnlock {
            backend.pendingUnlock = nil
            show(u.event, in: u.poolID)
        }
        if let note = backend.note { notifyIfNew(note) }
    }

    // MARK: Boosts

    /// The pool this Mac is boosting right now
    private(set) var boosting: String?
    /// How lending to the boosted pool was before the boost, to put back after it
    @ObservationIgnored private var beforeBoost: (poolID: String, gb: Int, wasMember: Bool, isOn: Bool)?
    @ObservationIgnored private var boostActivity: NSObjectProtocol?

    /// Boosts you counted yourself in for. An hour before one, a heads-up. During it, this Mac stays awake and lends
    /// that pool what you pledged, as far as its other pools leave room. After it, lending goes back to how it was.
    func runBoosts(now: Date = .now) {
        let s = state
        let mine = s.pools.compactMap { p -> (pool: Pool, boost: PoolBoost, gb: Int)? in
            guard let b = p.boost, let pledge = b.pledge(of: s.meID) else { return nil }
            return (p, b, pledge.gb)
        }
        if let id = boosting, !mine.contains(where: { $0.pool.id == id && $0.boost.starts <= now && now < $0.boost.ends }) {
            endBoost()
        }
        for (pool, boost, gb) in mine {
            let key = "\(pool.id).\(Int(boost.starts.timeIntervalSince1970))"
            if now >= boost.starts.addingTimeInterval(-3_600), now < boost.starts {
                notifyOnce("dots.boost.soon.\(key)", "Boost in an hour. Your \(s.thisMac.kind) is ready with \(gb) GB.")
            }
            guard boosting == nil, boost.starts <= now, now < boost.ends else { continue }
            let wasMember = pool.isMember(s.meID)
            beforeBoost = (pool.id, s.share(in: pool.id)?.gb ?? 0, wasMember, s.lending.isOn)
            boosting = pool.id
            let lend = min(gb, s.mostGB(for: pool.id))
            if wasMember { backend.setShare(poolID: pool.id, gb: lend) } else { _ = backend.join(poolID: pool.id, gb: lend) }
            backend.updateLending { $0.isOn = true; $0.pausedUntil = nil }
            refreshLending()
            if persistsSettings {
                boostActivity = ProcessInfo.processInfo.beginActivity(options: [.idleSystemSleepDisabled],
                                                                      reason: "dots is boosting \(pool.name) until \(boost.ends.formatted(date: .omitted, time: .shortened))")
            }
            if boost.pledgedGB >= boost.targetGB, let tier = Ladder.tier(boost.modelID) {
                notifyOnce("dots.boost.on.\(key)", "Boosted. \(pool.name) is running \(tier.name) till \(boost.ends.formatted(.dateTime.hour())).")
            }
        }
    }

    private func endBoost() {
        if let activity = boostActivity { ProcessInfo.processInfo.endActivity(activity) }
        boostActivity = nil
        boosting = nil
        guard let before = beforeBoost else { return }
        beforeBoost = nil
        if before.wasMember { backend.setShare(poolID: before.poolID, gb: before.gb) } else { backend.leave(poolID: before.poolID) }
        backend.updateLending { $0.isOn = before.isOn }
        refreshLending()
    }

    /// A notification, once for each key, and never from the self-test or snapshots.
    private func notifyOnce(_ key: String, _ body: String) {
        guard persistsSettings, !UserDefaults.standard.bool(forKey: key) else { return }
        UserDefaults.standard.set(true, forKey: key)
        let content = UNMutableNotificationContent()
        content.body = body
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: key, content: content, trigger: nil))
    }

    // MARK: Mornings

    /// A recap waits for the window: set the morning after a night of lending, shown when dots opens.
    var recapReady = false
    @ObservationIgnored private var lastRecapCheck = Date.distantPast

    /// Between 7 AM and noon, once a night: if this Mac lent for half an hour or more, say so, and keep the recap ready.
    private func checkRecap() {
        guard persistsSettings, Date().timeIntervalSince(lastRecapCheck) > 60 else { return }
        lastRecapCheck = .now
        guard (7..<12).contains(Calendar.current.component(.hour, from: .now)),
              let recap = NightRecap.last(in: state, now: .now), recap.night.minutes >= 30 else { return }
        let key = "dots.recap.\(Int(recap.night.date.timeIntervalSince1970))"
        guard !UserDefaults.standard.bool(forKey: key) else { return }
        UserDefaults.standard.set(true, forKey: key)
        recapReady = true
        let content = UNMutableNotificationContent()
        content.title = "Good morning"
        content.body = recap.headline(macKind: state.thisMac.kind)
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: key, content: content, trigger: nil))
    }

    // MARK: Weekly notes

    private func notifyIfNew(_ note: WeeklyNote) {
        let seen = UserDefaults.standard.string(forKey: "dots.lastNote")
        guard note.id != seen, persistsSettings else { return }
        UserDefaults.standard.set(note.id, forKey: "dots.lastNote")
        let content = UNMutableNotificationContent()
        content.title = "Your week with dots"
        content.body = note.headline
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: note.id, content: content, trigger: nil))
    }

    // MARK: Pools

    func open(_ route: Route) {
        self.route = route
        if case .profile = route { backend.markBadgesSeen() }
    }

    /// Joins a pool, lending it `gb`, or by default all this Mac has left to lend (which may be nothing: then it
    /// joins to ask, and lends nothing until another pool makes room).
    func join(_ pool: Pool, gb: Int? = nil) {
        let wasMember = pool.isMember(state.meID)
        let outcome = backend.join(poolID: pool.id, gb: min(gb ?? .max, state.offerGB(for: pool)))
        let unlock = backend.applyLendingStatus(active: lendingStatus.isActive)
        let waiting: String? = lendingStatus.isActive ? nil : lendingStatus.headline
        route = .pool(pool.id)
        if let unlock { shownUnlocks.insert("\(pool.id)-\(unlock.modelID)") }
        if !wasMember {
            moment = .joined(poolID: pool.id, macNumber: outcome.macNumber, unlock: unlock, waiting: waiting)
        } else if let unlock {
            moment = .unlocked(poolID: pool.id, event: unlock)
        }
    }

    func leave(_ pool: Pool) {
        backend.leave(poolID: pool.id)
        refreshLending()
        route = .explore
    }

    func endorse(_ pool: Pool) {
        _ = backend.endorse(poolID: pool.id)
    }

    func startPool(name: String, isPublic: Bool, model: String?) {
        let pool = backend.createPool(name: name, isPublic: isPublic, model: model)
        route = .pool(pool.id)
    }

    // MARK: Invites

    /// Joins a friend's cloud through their invite: you become a person there, with your name and the account your
    /// Mac's workers carry, and the token it gives you is kept for it. The app uses that cloud from then on.
    private func joinCloud(_ cloud: URL, through preview: InviteDetails, lendGB: Int?) {
        let me = Newcomer(name: state.me.name, account: workerProcess.account,
                          gb: min(lendGB ?? .max, state.thisMac.maxLendGB))
        Task {
            do {
                let reply: NewcomerReply = try await CloudClient(baseURL: cloud, token: "")
                    .send("POST", "v1/invites/\(preview.code)/join", body: me)
                cloudToken = reply.token
                settings.cloud.address = cloud.absoluteString
                settings.cloud.useMock = false
                connectCloud()
                await backend.syncNow()
                backend.updateLending { $0.isOn = true; $0.pausedUntil = nil }
                refreshLending()
                route = .pool(reply.poolID)
                moment = .joined(poolID: reply.poolID, macNumber: reply.macNumber, unlock: reply.unlock,
                                 waiting: lendingStatus.isActive ? nil : lendingStatus.headline)
            } catch {
                openInvite = OpenInvite(code: preview.code, preview: preview, error: error.localizedDescription, cloud: cloud)
                sheet = .openInvite
            }
        }
    }

    /// The code in an invite link, whatever form it was pasted in: dots://invite/CODE, a web link ending in the code,
    /// or CODE.
    static func inviteCode(in text: String) -> String? { invite(in: text)?.code }

    /// An invite link's code, and the cloud it names, if any: dots://invite/CODE?cloud=https://…
    static func invite(in text: String) -> (code: String, cloud: URL?)? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let path = trimmed.split(separator: "?", maxSplits: 1).first.map(String.init) ?? trimmed
        let last = path.split(separator: "/").last.map(String.init) ?? path
        let code = last.components(separatedBy: CharacterSet.alphanumerics.inverted).joined()
        guard code.count >= 4 else { return nil }
        let named = URLComponents(string: trimmed)?.queryItems?.first { $0.name == "cloud" }?.value.flatMap(URL.init(string:))
        let cloud = named.flatMap { ["http", "https"].contains($0.scheme?.lowercased() ?? "") && $0.host != nil ? $0 : nil }
        return (code, cloud)
    }

    func openInviteLink(_ text: String) {
        guard let link = Self.invite(in: text) else {
            openInvite = OpenInvite(code: text, preview: nil, error: "That doesn't look like an invite link.")
            sheet = .openInvite
            return
        }
        let code = link.code
        // An invite for a friend's cloud is read there; one for this Mac's own cloud, here
        let elsewhere = link.cloud.flatMap { $0.host == backend.client.baseURL.host && $0.port == backend.client.baseURL.port ? nil : $0 }
        openInvite = OpenInvite(code: code, cloud: elsewhere)
        sheet = .openInvite
        Task {
            // A link can open dots before its cloud answers, so a connection error gets two more tries
            for attempt in 1...3 {
                do {
                    if let elsewhere {
                        openInvite?.preview = try await CloudClient(baseURL: elsewhere, token: "")
                            .get("v1/invites/\(code)", query: [URLQueryItem(name: "memory_gb", value: "\(ThisMac.memoryGB)")])
                    } else {
                        openInvite?.preview = try await backend.preview(code: code)
                    }
                    return
                } catch let error as TransportError {
                    if case .http(_, _) = error { break }
                } catch {}
                if attempt < 3 { try? await Task.sleep(for: .milliseconds(600)) }
            }
            openInvite?.error = "That invite isn't valid any more."
        }
    }

    func acceptInvite(_ preview: InviteDetails, lendGB: Int? = nil) {
        sheet = nil
        if let cloud = openInvite?.cloud {
            joinCloud(cloud, through: preview, lendGB: lendGB)
            return
        }
        let gb = min(lendGB ?? .max, state.mostGB(for: preview.poolID))
        Task {
            do {
                await backend.beatNow(active: lendingStatus.isActive)
                let reply = try await backend.accept(code: preview.code, gb: gb)
                route = .pool(reply.poolID)
                backend.updateLending { $0.isOn = true; $0.pausedUntil = nil }
                let unlock = backend.applyLendingStatus(active: lendingStatus.isActive)
                if let unlock { shownUnlocks.insert("\(reply.poolID)-\(unlock.modelID)") }
                moment = .joined(poolID: reply.poolID, macNumber: reply.macNumber, unlock: unlock ?? reply.unlock,
                                 waiting: lendingStatus.isActive ? nil : lendingStatus.headline)
            } catch {
                openInvite = OpenInvite(code: preview.code, preview: preview, error: error.localizedDescription)
                sheet = .openInvite
            }
        }
    }

    // MARK: Keys

    func createKey(name: String, reach: Reach, monthlyLimit: Int?, privateOnly: Bool = false, monthlyCapUSD: Double? = nil) {
        let made = backend.createKey(name: name, reach: reach, monthlyLimit: monthlyLimit)
        if privateOnly || monthlyCapUSD != nil {
            backend.updateKey(id: made.key.id) { $0.privateOnly = privateOnly; $0.monthlyCapUSD = monthlyCapUSD }
        }
        revealedKey = RevealedKey(id: made.key.id, secret: made.secret)
    }

    var gatewayBaseURL: String { "http://localhost:\(settings.gatewayPort)/v1" }

    // MARK: Routes

    /// The model on this Mac (LM Studio or Ollama), as a place to answer: free, private, as fast as it measured.
    var thisMacRoute: RouteCandidate? {
        let e = settings.poolEndpoint
        guard e.isReady, e.isLocal, (localDownUntil ?? .distantPast) < .now else { return nil }
        return RouteCandidate(id: "mac", destination: .thisMac, place: "This Mac", model: e.model, modelID: e.model,
                              credits: 0, listCredits: 0, tokPerSec: localTokPerSec ?? 30, isPrivate: true, poolID: nil,
                              minGB: 18, contextTokens: 32_000, canUseTools: false, onlyMine: true)
    }

    func noteLocalSpeed(_ tokPerSec: Double) {
        localTokPerSec = localTokPerSec.map { $0 * 0.7 + tokPerSec * 0.3 } ?? tokPerSec
    }

    func noteLocalDown() { localDownUntil = Date().addingTimeInterval(60) }

    // MARK: Answers

    /// Books an answer: where it ran, the tokens, what it cost and what it saved against the baseline model.
    /// Credits were already charged by the hub or gateway that answered.
    func recordAnswer(destination: Destination, place: String, model: String, source: String, credits: Int, usd: Double,
                      isPrivate: Bool, input: Int, output: Int, measured: Bool, fallback: String?, format: APIFormat) {
        backend.record(RequestRecord(date: .now, source: source, destination: destination, place: place, model: model,
                                     credits: credits, isPrivate: isPrivate, inputTokens: input, outputTokens: output,
                                     measured: measured, usd: usd,
                                     baselineUSD: Pricing.usd(settings.savingsBaseline, input: input, output: output),
                                     fallback: fallback, format: format.rawValue))
    }

    var monthRequests: [RequestRecord] {
        let start = Calendar.current.dateInterval(of: .month, for: .now)?.start ?? .distantPast
        return state.requests.filter { $0.date >= start }
    }

    // MARK: Windows

    func openMainWindow() {
        NSApp.setActivationPolicy(.regular)
        if let window = NSApp.windows.first(where: { $0.identifier?.rawValue.hasPrefix("main") == true }) {
            window.makeKeyAndOrderFront(nil)
        } else {
            openWindowAction?(id: "main")
        }
        NSApp.activate()
    }

    /// Settings is a screen of the main window. dots is a menu bar app, so the window is brought forward first.
    func showSettings() {
        openMainWindow()
        open(.settings)
    }

    /// Ask is a screen of the main window too, with the question box focused and ready.
    func showAsk() {
        openMainWindow()
        open(.ask)
        ask.focusToken += 1
    }

    /// Opens Ask and asks `question`, as if typed in the box and sent. An empty one just opens Ask.
    func send(question: String) {
        showAsk()
        guard !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !ask.isBusy else { return }
        ask.input = question
        ask.submit(model: self)
        ask.input = ""
    }

    /// Opens Draw and draws `picture`, as if typed and sent. Empty, it just opens Draw.
    func draw(picture: String) {
        openMainWindow()
        open(.draw)
        guard !picture.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !draw.isDrawing else { return }
        draw.prompt = picture
        draw.draw(model: self)
    }

    // MARK: Gateway

    func restartGateway() {
        gateway?.stop()
        gateway = nil
        guard settings.gatewayEnabled, !isSnapshot else { gatewayState = "Off"; return }
        let gw = LocalGateway(port: UInt16(settings.gatewayPort)) { [weak self] request, responder in
            await self?.handleGateway(request, responder)
        }
        gw.onState = { [weak self] text in
            Task { @MainActor in self?.gatewayState = text }
        }
        do {
            try gw.start()
            gateway = gw
        } catch {
            gatewayState = "Couldn't start: \(error.localizedDescription)"
        }
    }
}
