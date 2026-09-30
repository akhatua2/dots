import AppKit
import Observation
import SwiftUI

enum Route: Hashable {
    case explore, api, profile
    case pool(String)
}

enum SheetKind: Identifiable, Equatable {
    case startPool, invite(String), createKey
    var id: String {
        switch self {
        case .startPool: "start"
        case .invite(let p): "invite-\(p)"
        case .createKey: "key"
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
        case .cantLend: "It needs more than 8 GB of memory."
        case .off: "Switch it on to earn credits."
        case .noPool: "Join a pool in Explore."
        case let .paused(until): "Back at \(until.formatted(date: .omitted, time: .shortened))"
        case .waitingForPower: "Lends once it's plugged in"
        case .inUse: "Resumes after two idle minutes"
        case let .lending(_, working): working ? "Helping answer questions" : "Waiting for work"
        }
    }
}

struct RevealedKey: Equatable {
    var id: String
    var secret: String
}

@MainActor
@Observable
final class AppModel {
    static let shared = AppModel()

    let backend: Simulation
    let signals = SignalMonitor()
    let ask = AskController()

    var settings: AppSettings {
        didSet {
            guard settings != oldValue else { return }
            if persistsSettings { settings.save() }
            if settings.gatewayEnabled != oldValue.gatewayEnabled || settings.gatewayPort != oldValue.gatewayPort { restartGateway() }
        }
    }
    var route: Route = .explore
    var moment: Moment?
    var sheet: SheetKind?
    var working = false
    var revealedKey: RevealedKey?
    var gatewayState = "Off"
    var isSnapshot = false
    /// False for snapshots and the self-test, so they never change your saved settings
    @ObservationIgnored var persistsSettings = true

    @ObservationIgnored var openWindowAction: OpenWindowAction?
    @ObservationIgnored private var timer: Timer?
    @ObservationIgnored private var lastTick = Date()
    @ObservationIgnored private var gateway: LocalGateway?

    init(backend: Simulation? = nil, settings: AppSettings? = nil) {
        self.backend = backend ?? Simulation()
        self.settings = settings ?? .load()
    }

    var state: NetworkState { backend.state }

    // MARK: Lending

    var lendingStatus: LendingStatus {
        let s = state
        guard s.thisMac.maxLendGB > 0 else { return .cantLend }
        guard s.lending.isOn else { return .off }
        guard let pool = s.lendingPool else { return .noPool }
        if let until = s.lending.pausedUntil, until > .now { return .paused(until: until) }
        if s.lending.onlyWhenPluggedIn && !signals.onPower { return .waitingForPower }
        if s.lending.pauseWhileInUse && signals.inUse { return .inUse }
        return .lending(pool: pool.name, working: working)
    }

    var creditsPerHour: Int {
        guard let pool = state.lendingPool else { return 0 }
        return pool.creditsPerHour(lendGB: state.lending.limitGB)
    }

    func setLendingOn(_ on: Bool) {
        backend.updateLending { $0.isOn = on; if on { $0.pausedUntil = nil } }
        refreshLending()
    }
    func setLimit(_ gb: Int) { backend.updateLending { $0.limitGB = gb }; refreshLending() }
    func setOnlyWhenPluggedIn(_ on: Bool) { backend.updateLending { $0.onlyWhenPluggedIn = on }; refreshLending() }
    func setPauseWhileInUse(_ on: Bool) { backend.updateLending { $0.pauseWhileInUse = on }; refreshLending() }
    func pauseForAnHour() { backend.updateLending { $0.pausedUntil = Date().addingTimeInterval(3_600) }; refreshLending() }
    func resume() { backend.updateLending { $0.pausedUntil = nil }; refreshLending() }

    /// Moves this Mac in or out of its pool, and shows the unlock if that tipped the pool over a line.
    @discardableResult
    func refreshLending() -> UnlockEvent? {
        let unlock = backend.applyLendingStatus(active: lendingStatus.isActive)
        if let unlock, let pid = state.lendingPoolID, moment == nil { moment = .unlocked(poolID: pid, event: unlock) }
        return unlock
    }

    // MARK: Life cycle

    func start() {
        signals.start()
        refreshLending()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        restartGateway()
    }

    private func tick() {
        let now = Date()
        let dt = now.timeIntervalSince(lastTick) * settings.demoSpeed
        lastTick = now
        // Work comes in bursts while the Mac lends
        if lendingStatus.isActive {
            if Double.random(in: 0...1) < 0.07 { working.toggle() }
        } else if working {
            working = false
        }
        refreshLending()
        backend.tick(seconds: dt, lendingActive: lendingStatus.isActive, working: working)
    }

    // MARK: Pools

    func open(_ route: Route) {
        self.route = route
        if case .profile = route { backend.markBadgesSeen() }
    }

    func join(_ pool: Pool) {
        let wasMember = pool.isMember(state.meID)
        let outcome = backend.join(poolID: pool.id)
        let unlock = backend.applyLendingStatus(active: lendingStatus.isActive)
        let waiting: String? = lendingStatus.isActive ? nil : lendingStatus.headline
        route = .pool(pool.id)
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

    func startPool(name: String, isPublic: Bool) {
        let pool = backend.createPool(name: name, isPublic: isPublic)
        route = .pool(pool.id)
    }

    // MARK: Keys

    func createKey(name: String, reach: Reach, monthlyLimit: Int?) {
        let made = backend.createKey(name: name, reach: reach, monthlyLimit: monthlyLimit)
        revealedKey = RevealedKey(id: made.key.id, secret: made.secret)
    }

    var gatewayBaseURL: String { "http://localhost:\(settings.gatewayPort)/v1" }

    // MARK: Answers

    /// Books an answer: spends its credits and records where it ran.
    func finishAnswer(receipt: Receipt, source: String) {
        if receipt.credits > 0 { backend.spend(receipt.credits, note: "\(receipt.model) in \(receipt.place)") }
        backend.record(RequestRecord(date: .now, source: source, destination: receipt.destination, place: receipt.place,
                                     model: receipt.model, credits: receipt.credits, isPrivate: receipt.isPrivate))
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
