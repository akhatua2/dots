import Foundation
import Observation

/// This Mac's own part of dots: API keys for the local API, the request log and the lending rules.
/// Saved in Application Support/dots/local.json.
struct LocalStore: Codable {
    var apiKeys: [APIKey]
    var requests: [RequestRecord]
    var lending: LendingSettings
}

/// The dots network as the app sees it: what the cloud last said, plus this Mac's own keys, request log and
/// lending rules. A change shows at once (the app runs the same rules as the cloud), then the cloud's answer
/// replaces it. The cloud is the mock on this Mac today, and the hub, server and gateway later.
@MainActor
@Observable
final class CloudBackend: DotsBackend {
    private let mirror: Simulation
    var state: NetworkState { mirror.state }
    var client: CloudClient
    private(set) var note: WeeklyNote?
    private(set) var worker: WorkerStatus?
    private(set) var invites: [InviteDetails] = []
    private(set) var isMock = true
    private(set) var isConnected = false
    private(set) var connection = "Connecting…"
    /// An unlock the cloud says this Mac caused, for the app to show
    var pendingUnlock: (poolID: String, event: UnlockEvent)?

    @ObservationIgnored private var beating = false
    @ObservationIgnored private var pendingSeconds: Double = 0
    @ObservationIgnored private var lastBeat = Date.distantPast
    @ObservationIgnored private var lastActive: Bool?
    @ObservationIgnored private var serial = 0
    @ObservationIgnored private let localURL: URL?
    @ObservationIgnored private var saveWork: DispatchWorkItem?
    @ObservationIgnored private var inFlight: [Task<Void, Never>] = []

    init(client: CloudClient, persists: Bool = true) {
        self.client = client
        mirror = Simulation(fresh: true, persists: false)
        localURL = persists ? Simulation.supportURL("local.json") : nil
        if let local = Self.loadLocal(localURL) {
            mirror.adoptLocal(keys: local.apiKeys, requests: local.requests, lending: local.lending)
        }
    }

    /// This Mac's part, from local.json, or carried over from the whole network an older dots saved.
    private static func loadLocal(_ url: URL?) -> LocalStore? {
        guard let url else { return nil }
        if let data = try? Data(contentsOf: url), let local = try? JSONDecoder.dots.decode(LocalStore.self, from: data) { return local }
        if let data = try? Data(contentsOf: Simulation.storeURL), let old = try? JSONDecoder.dots.decode(NetworkState.self, from: data) {
            return LocalStore(apiKeys: old.apiKeys, requests: old.requests, lending: old.lending)
        }
        return nil
    }

    // MARK: Talking to the cloud

    /// Asks the cloud for everything now, and waits for the answer.
    func syncNow() async {
        do { adopt(try await client.get("v1/state")) } catch { failed(error) }
    }

    private func adopt(_ sync: CloudSync) {
        mirror.adopt(sync.state)
        Pricing.terms = sync.terms
        note = sync.note
        worker = sync.worker
        invites = sync.invites
        isMock = sync.mock
        isConnected = true
        connection = sync.mock ? "Running on this Mac, port \(client.baseURL.port ?? 0)" : "Connected to \(client.baseURL.host ?? "the cloud")"
    }

    private func failed(_ error: Error) {
        isConnected = false
        connection = "The cloud didn't answer: \(error.localizedDescription)"
    }

    /// Sends a change, and takes the cloud's answer as the truth. A heartbeat sent before this change can't undo it.
    private func send(_ op: @escaping () async throws -> CloudSync) {
        serial += 1
        inFlight.removeAll { $0.isCancelled }
        inFlight.append(Task {
            do { adopt(try await op()) } catch { failed(error) }
        })
    }

    /// Waits until every change sent so far has its answer (the self-test uses this).
    func settled() async {
        while !inFlight.isEmpty {
            let tasks = inFlight
            inFlight = []
            for t in tasks { await t.value }
        }
    }

    /// The worker's report to the hub: lending or not, how much, and how much simulated time has passed.
    func beat(active: Bool) {
        guard !beating else { return }
        beating = true
        Task {
            await beatNow(active: active)
            beating = false
        }
    }

    /// The heartbeat, waited for (the self-test and snapshots use this). `adding`: more simulated time.
    func beatNow(active: Bool, adding extra: Double = 0) async {
        let seconds = pendingSeconds + extra
        pendingSeconds = 0
        lastBeat = .now
        lastActive = active
        let atSerial = serial
        let report = Heartbeat(active: active, lendGB: state.lending.limitGB, seconds: seconds, macKind: ThisMac.kind, memoryGB: ThisMac.memoryGB)
        do {
            let reply: HeartbeatReply = try await client.send("POST", "hub/v1/heartbeat", body: report)
            if atSerial == serial { adopt(reply.sync) } else { worker = reply.sync.worker }
            if let u = reply.unlock, u.byMacID == state.thisMacID, let pool = state.lendingPoolID { pendingUnlock = (pool, u) }
        } catch {
            pendingSeconds += seconds
            failed(error)
        }
    }

    /// An invite, before joining: what it is, and what this Mac would add.
    func preview(code: String) async throws -> InviteDetails {
        try await client.get("v1/invites/\(code)", query: [URLQueryItem(name: "memory_gb", value: "\(ThisMac.memoryGB)")])
    }

    func accept(code: String) async throws -> JoinReply {
        let reply: JoinReply = try await client.send("POST", "v1/invites/\(code)/accept")
        serial += 1
        adopt(reply.sync)
        return reply
    }

    /// The mock cloud's weekly note, now rather than in a week.
    func sendNoteNow() {
        Task {
            do {
                let _: WeeklyNote = try await client.send("POST", "v1/mock/note")
                await syncNow()
            } catch { failed(error) }
        }
    }

    // MARK: The network (the cloud's part)

    func join(poolID: String) -> JoinOutcome {
        let outcome = mirror.join(poolID: poolID)
        saveLocal()
        send { (try await self.client.send("POST", "v1/pools/\(poolID)/join") as JoinReply).sync }
        return outcome
    }

    func leave(poolID: String) {
        mirror.leave(poolID: poolID)
        send { try await self.client.send("POST", "v1/pools/\(poolID)/leave") }
    }

    func endorse(poolID: String) -> Badge? {
        let badge = mirror.endorse(poolID: poolID)
        send { (try await self.client.send("POST", "v1/pools/\(poolID)/endorse") as BadgeReply).sync }
        return badge
    }

    func createPool(name: String, isPublic: Bool) -> Pool {
        let pool = mirror.createPool(name: name, isPublic: isPublic)
        struct Body: Encodable { var id: String; var name: String; var isPublic: Bool }
        send { (try await self.client.send("POST", "v1/pools", body: Body(id: pool.id, name: name, isPublic: isPublic)) as PoolReply).sync }
        return pool
    }

    func createInvite(poolID: String) -> Invite {
        let invite = mirror.createInvite(poolID: poolID)
        struct Body: Encodable { var code: String }
        send { (try await self.client.send("POST", "v1/pools/\(poolID)/invite", body: Body(code: invite.code)) as InviteReply).sync }
        return invite
    }

    func revokeInvite(poolID: String) {
        mirror.revokeInvite(poolID: poolID)
        send { try await self.client.send("DELETE", "v1/pools/\(poolID)/invite") }
    }

    func setOffersIdle(poolID: String, _ on: Bool) {
        mirror.setOffersIdle(poolID: poolID, on)
        struct Body: Encodable { var offersIdleToNetwork: Bool }
        send { try await self.client.send("PATCH", "v1/pools/\(poolID)", body: Body(offersIdleToNetwork: on)) }
    }

    /// Counts you in for a pool's boost with `gb`, or out with nil.
    func pledgeBoost(poolID: String, gb: Int?) {
        mirror.pledgeBoost(poolID: poolID, gb: gb)
        if let gb {
            struct Body: Encodable { var gb: Int }
            send { try await self.client.send("POST", "v1/pools/\(poolID)/boost/pledge", body: Body(gb: gb)) }
        } else {
            send { try await self.client.send("DELETE", "v1/pools/\(poolID)/boost/pledge") }
        }
    }

    func rename(_ name: String) {
        mirror.rename(name)
        saveLocal()
        struct Body: Encodable { var name: String }
        send { try await self.client.send("PATCH", "v1/me", body: Body(name: name)) }
    }

    func markBadgesSeen() {
        guard state.badges.contains(where: \.isNew) else { return }
        mirror.markBadgesSeen()
        send { try await self.client.send("POST", "v1/badges/seen") }
    }

    /// The hub and gateway charge credits as they answer, so the app never spends them itself.
    func spend(_ credits: Int, note: String) {}

    /// The hub says when a Mac falls asleep; this shows it at once.
    func setMemberAsleep(macID: String, poolID: String, asleep: Bool) {
        mirror.setMemberAsleep(macID: macID, poolID: poolID, asleep: asleep)
    }

    // MARK: Lending (the rules are this Mac's; the hub hears about them in each heartbeat)

    func updateLending(_ change: (inout LendingSettings) -> Void) {
        mirror.updateLending(change)
        saveLocal()
    }

    func applyLendingStatus(active: Bool) -> UnlockEvent? {
        let unlock = mirror.applyLendingStatus(active: active)
        if active != lastActive { beat(active: active) }
        return unlock
    }

    func tick(seconds: Double, lendingActive: Bool, working: Bool) {
        pendingSeconds += seconds
        if Date().timeIntervalSince(lastBeat) >= 2 { beat(active: lendingActive) }
    }

    // MARK: This Mac's own part

    func record(_ request: RequestRecord) {
        mirror.record(request)
        saveLocal()
    }

    func createKey(name: String, reach: Reach, monthlyLimit: Int?) -> (key: APIKey, secret: String) {
        let made = mirror.createKey(name: name, reach: reach, monthlyLimit: monthlyLimit)
        saveLocal()
        return made
    }

    func updateKey(id: String, _ change: (inout APIKey) -> Void) {
        mirror.updateKey(id: id, change)
        saveLocal()
    }

    func revokeKey(id: String) {
        mirror.revokeKey(id: id)
        saveLocal()
    }

    func key(forSecret secret: String) -> APIKey? { mirror.key(forSecret: secret) }

    func noteKeyUse(id: String, credits: Int) { noteKeyUse(id: id, credits: credits, usd: 0) }

    func noteKeyUse(id: String, credits: Int, usd: Double) {
        mirror.noteKeyUse(id: id, credits: credits)
        if usd > 0 { mirror.updateKey(id: id) { $0.usdThisMonth = ($0.usdThisMonth ?? 0) + usd } }
        saveLocal()
    }

    func reset() {
        let sample = Simulation(fresh: true, persists: false).state
        mirror.adoptLocal(keys: sample.apiKeys, requests: sample.requests, lending: sample.lending)
        saveLocal()
        if isMock { send { try await self.client.send("POST", "v1/mock/reset") } }
    }

    func saveNow() {
        saveWork?.cancel()
        guard let url = localURL else { return }
        let local = LocalStore(apiKeys: state.apiKeys, requests: Array(state.requests.suffix(5_000)), lending: state.lending)
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder.dots.encode(local).write(to: url, options: .atomic)
        } catch {
            NSLog("dots: couldn't save this Mac's keys and requests: \(error)")
        }
    }

    private func saveLocal() {
        guard localURL != nil else { return }
        saveWork?.cancel()
        let work = DispatchWorkItem { [weak self] in MainActor.assumeIsolated { self?.saveNow() } }
        saveWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8, execute: work)
    }
}
