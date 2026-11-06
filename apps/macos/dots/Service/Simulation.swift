import CryptoKit
import Foundation
import Observation

extension JSONEncoder {
    static let dots: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.sortedKeys]
        return e
    }()
}

extension JSONDecoder {
    static let dots: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()
}

/// The dots network, simulated: pools, members, lending, unlocks, credits, badges and keys.
/// It is the mock cloud's world (saved in Application Support/dots/mock-cloud.json), and the app's
/// mirror of what the cloud last said.
@MainActor
@Observable
final class Simulation: DotsBackend {
    private(set) var state: NetworkState
    @ObservationIgnored private var saveWork: DispatchWorkItem?
    @ObservationIgnored private var sinceSave: Double = 0
    @ObservationIgnored private var sessionEventID: UUID?
    @ObservationIgnored private var sessionEarned: Double = 0
    /// Runs part-way done, carried to the next tick
    @ObservationIgnored private var runCarry: Double = 0
    /// Where it's saved. Nil for snapshots, the self-test and the app's mirror, which never write over a saved network
    private let store: URL?
    private var persists: Bool { store != nil }

    /// `seed` is read when nothing is saved at `store` yet, so a network carries over from an older dots.
    init(fresh: Bool = false, persists: Bool = true, store: URL? = nil, seed: URL? = nil) {
        self.store = persists ? (store ?? Self.storeURL) : nil
        let saved = [self.store, seed].compactMap { $0 }.lazy
            .compactMap { try? Data(contentsOf: $0) }
            .compactMap { try? JSONDecoder.dots.decode(NetworkState.self, from: $0) }
            .first
        if !fresh, let saved {
            state = saved
        } else {
            state = SampleData.initialState()
        }
        // Keep this Mac's real specs current
        if let i = state.devices.firstIndex(where: { $0.id == state.thisMacID }) {
            state.devices[i].memoryGB = ThisMac.memoryGB
            state.devices[i].kind = ThisMac.kind
        }
        // Only clamp to what this Mac can lend once the cloud knows its memory.
        if state.thisMac.memoryGB > 0 {
            state.lending.limitGB = min(state.lending.limitGB, state.thisMac.maxLendGB)
        }
    }

    /// Where an older dots saved its whole simulated network
    static var storeURL: URL { supportURL("network.json") }

    static func supportURL(_ name: String) -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("dots", isDirectory: true)
            .appendingPathComponent(name)
    }

    private func mutate(_ body: (inout NetworkState) -> Void) {
        body(&state)
        scheduleSave()
    }

    /// Changes made by the cloud's code itself (seeding, the hub's bookkeeping)
    func update(_ body: (inout NetworkState) -> Void) { mutate(body) }

    // MARK: The cloud's part

    var cloudState: CloudState {
        CloudState(meID: state.meID, thisMacID: state.thisMacID, people: state.people, devices: state.devices, pools: state.pools,
                   credits: state.credits, earnedAllTime: state.earnedAllTime, questionsHelped: state.questionsHelped,
                   creditLog: Array(state.creditLog.prefix(200)), badges: state.badges, networkLendingNow: state.networkLendingNow,
                   lendingPoolID: state.lendingPoolID, lendingSince: state.lendingSince, endorsedBefore: state.endorsedBefore,
                   hoursLent: state.hoursLent, computePF: state.computePF, nights: state.nights)
    }

    /// Takes what the cloud says as the truth, keeping this Mac's own keys, request log and lending rules.
    func adopt(_ c: CloudState) {
        state.meID = c.meID
        state.thisMacID = c.thisMacID
        state.people = c.people
        state.devices = c.devices
        state.pools = c.pools
        state.credits = c.credits
        state.earnedAllTime = c.earnedAllTime
        state.questionsHelped = c.questionsHelped
        state.creditLog = c.creditLog
        state.badges = c.badges
        state.networkLendingNow = c.networkLendingNow
        state.lendingPoolID = c.lendingPoolID
        state.lendingSince = c.lendingSince
        state.endorsedBefore = c.endorsedBefore
        state.hoursLent = c.hoursLent
        state.computePF = c.computePF
        state.nights = c.nights
        state.lending.limitGB = min(state.lending.limitGB, state.thisMac.maxLendGB)
    }

    /// This Mac's own part: keys, request log and lending rules, kept on this Mac.
    func adoptLocal(keys: [APIKey], requests: [RequestRecord], lending: LendingSettings) {
        state.apiKeys = keys
        state.requests = requests
        state.lending = lending
        state.lending.limitGB = min(state.lending.limitGB, state.thisMac.maxLendGB)
    }

    func updateKey(id: String, _ change: (inout APIKey) -> Void) {
        mutate { s in if let i = s.apiKeys.firstIndex(where: { $0.id == id }) { change(&s.apiKeys[i]) } }
    }

    // MARK: Pools

    func join(poolID: String) -> JoinOutcome {
        let macID = state.thisMacID
        mutate { s in
            guard let i = s.pools.firstIndex(where: { $0.id == poolID }) else { return }
            // A Mac lends to one pool at a time: leave the old pool's lending set
            if let old = s.lendingPoolID, old != poolID, let o = s.pools.firstIndex(where: { $0.id == old }) {
                s.pools[o].macs.removeAll { $0.macID == macID }
            }
            if !s.pools[i].memberIDs.contains(s.meID) { s.pools[i].memberIDs.append(s.meID) }
            if !s.pools[i].macs.contains(where: { $0.macID == macID }) {
                s.pools[i].macs.append(PoolMac(macID: macID, lendGB: s.lending.limitGB, state: .online, joinedAt: .now))
            }
            s.lendingPoolID = poolID
            s.lending.isOn = true
            s.lending.pausedUntil = nil
        }
        let pool = state.pool(poolID)!
        return JoinOutcome(macNumber: pool.totalMacs, pool: pool)
    }

    func leave(poolID: String) {
        mutate { s in
            guard let i = s.pools.firstIndex(where: { $0.id == poolID }) else { return }
            s.pools[i].memberIDs.removeAll { $0 == s.meID }
            s.pools[i].macs.removeAll { $0.macID == s.thisMacID }
            if s.lendingPoolID == poolID { s.lendingPoolID = nil; s.lendingSince = nil }
        }
    }

    func endorse(poolID: String) -> Badge? {
        var badge: Badge?
        mutate { s in
            guard let i = s.pools.firstIndex(where: { $0.id == poolID }), !s.pools[i].endorsedByMe else { return }
            s.pools[i].endorsedByMe = true
            s.pools[i].endorsements += 1
            s.pools[i].endorsedAtMacs = s.pools[i].totalMacs
            // Backing a pool while it's small is what makes you an early backer
            if s.pools[i].totalMacs < 20, !s.badges.contains(where: { $0.id == "backer-\(poolID)" }) {
                let b = Badge(id: "backer-\(poolID)", symbol: "checkmark.seal.fill", title: "Early backer",
                              detail: "Endorsed \(s.pools[i].name) at \(s.pools[i].totalMacs) Macs", date: .now, isNew: true)
                s.badges.insert(b, at: 0)
                badge = b
            }
        }
        return badge
    }

    func createPool(name: String, isPublic: Bool) -> Pool { createPool(name: name, isPublic: isPublic, id: nil) }

    /// `id`: the one the app already gave it, so the app's optimistic pool and the cloud's are the same.
    func createPool(name: String, isPublic: Bool, id: String?) -> Pool {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let hash = clean.unicodeScalars.reduce(UInt64(5)) { $0 &* 31 &+ UInt64($1.value) }
        let pool = Pool(id: id ?? "pool-\(Self.randomCode(6).lowercased())", name: clean, isPublic: isPublic,
                        blurb: isPublic ? "A new public pool." : "A pool for friends.",
                        tags: [], memberIDs: [state.meID], macs: [], hostID: state.meID,
                        phase: Double(hash % 628) / 100, createdAt: .now, speedFactor: 1.0, demand: 0.5)
        mutate { s in
            s.pools.append(pool)
            if !s.badges.contains(where: { $0.id == "starter" }) {
                s.badges.insert(Badge(id: "starter", symbol: "plus.circle.fill", title: "Pool starter",
                                      detail: "Started \(clean)", date: .now, isNew: true), at: 0)
            }
        }
        return pool
    }

    func createInvite(poolID: String) -> Invite { createInvite(poolID: poolID, code: nil) }

    func createInvite(poolID: String, code: String?) -> Invite {
        let invite = Invite(code: code ?? Self.randomCode(6), expires: Date().addingTimeInterval(7 * 86_400))
        mutate { s in
            if let i = s.pools.firstIndex(where: { $0.id == poolID }) { s.pools[i].invite = invite }
        }
        return invite
    }

    func revokeInvite(poolID: String) {
        mutate { s in
            if let i = s.pools.firstIndex(where: { $0.id == poolID }) { s.pools[i].invite = nil }
        }
    }

    func setOffersIdle(poolID: String, _ on: Bool) {
        mutate { s in
            if let i = s.pools.firstIndex(where: { $0.id == poolID }) { s.pools[i].offersIdleToNetwork = on }
        }
    }

    /// Counts you in for a pool's boost with `gb`, or out with nil. You boost as a guest in a pool you're not in.
    func pledgeBoost(poolID: String, gb: Int?) {
        mutate { s in
            guard let i = s.pools.firstIndex(where: { $0.id == poolID }), var boost = s.pools[i].boost else { return }
            let me = s.meID
            let guest = !s.pools[i].memberIDs.contains(me)
            boost.pledges.removeAll { $0.personID == me }
            if let gb, gb > 0 { boost.pledges.insert(.init(personID: me, gb: gb, guest: guest), at: 0) }
            s.pools[i].boost = boost
        }
    }

    // MARK: Lending

    func updateLending(_ change: (inout LendingSettings) -> Void) {
        mutate { s in
            change(&s.lending)
            s.lending.limitGB = max(0, min(s.lending.limitGB, s.thisMac.maxLendGB))
        }
    }

    func applyLendingStatus(active: Bool) -> UnlockEvent? {
        guard let pid = state.lendingPoolID,
              let pi = state.pools.firstIndex(where: { $0.id == pid }),
              let mi = state.pools[pi].macs.firstIndex(where: { $0.macID == state.thisMacID }) else { return nil }
        let target: MacState = active ? .lending : .online
        let lend = state.lending.limitGB
        let current = state.pools[pi].macs[mi]
        guard current.state != target || current.lendGB != lend else { return nil }

        let beforeGB = state.pools[pi].pooledGB
        let beforeModel = state.pools[pi].model
        var unlock: UnlockEvent?
        mutate { s in
            s.pools[pi].macs[mi].state = target
            s.pools[pi].macs[mi].lendGB = lend
            let afterGB = s.pools[pi].pooledGB
            if active, let model = s.pools[pi].model, model.minGB > (beforeModel?.minGB ?? -1),
               !s.pools[pi].unlocks.contains(where: { $0.modelID == model.id }) {
                let event = UnlockEvent(modelID: model.id, byMacID: s.thisMacID, fromGB: beforeGB, toGB: afterGB, date: .now)
                s.pools[pi].unlocks.append(event)
                unlock = event
                let badgeID = "unlock-\(pid)-\(model.id)"
                if !s.badges.contains(where: { $0.id == badgeID }) {
                    s.badges.insert(Badge(id: badgeID, symbol: "bolt.fill", title: "Unlocker",
                                          detail: "Took \(s.pools[pi].name) to \(model.name)", date: .now, isNew: true), at: 0)
                }
            }
            if active {
                if s.lendingSince == nil { s.lendingSince = .now }
            } else {
                s.lendingSince = nil
            }
        }
        if active { sessionEventID = nil }
        return unlock
    }

    // MARK: Credits and usage

    func spend(_ credits: Int, note: String) {
        guard credits > 0 else { return }
        mutate { s in
            s.credits -= Double(credits)
            s.creditLog.insert(CreditEvent(date: .now, amount: -credits, note: note, kind: "spend"), at: 0)
        }
    }

    func record(_ request: RequestRecord) {
        mutate { s in s.requests.append(request) }
    }

    func setMemberAsleep(macID: String, poolID: String, asleep: Bool) {
        mutate { s in
            guard let pi = s.pools.firstIndex(where: { $0.id == poolID }),
                  let mi = s.pools[pi].macs.firstIndex(where: { $0.macID == macID }) else { return }
            s.pools[pi].macs[mi].state = asleep ? .asleep : .lending
            if asleep, !s.pools[pi].isPublic, let owner = s.device(macID)?.ownerID {
                let away = PoolActivity(id: "away-\(macID)-\(Int(Date().timeIntervalSince1970))", date: .now, personID: owner,
                                        kind: "away", gb: s.pools[pi].macs[mi].lendGB)
                s.pools[pi].activity = Array(([away] + (s.pools[pi].activity ?? [])).prefix(20))
            }
        }
    }

    // MARK: Keys

    func createKey(name: String, reach: Reach, monthlyLimit: Int?) -> (key: APIKey, secret: String) {
        let secret = "dots_sk_live_" + Self.randomCode(16)
        let key = APIKey(id: "key-\(Self.randomCode(8).lowercased())", name: name, last4: String(secret.suffix(4)),
                         secretHash: Self.hash(secret), reach: reach, monthlyLimit: monthlyLimit, createdAt: .now)
        mutate { s in s.apiKeys.insert(key, at: 0) }
        return (key, secret)
    }

    func revokeKey(id: String) {
        mutate { s in s.apiKeys.removeAll { $0.id == id } }
    }

    func key(forSecret secret: String) -> APIKey? {
        let h = Self.hash(secret)
        return state.apiKeys.first { !$0.secretHash.isEmpty && $0.secretHash == h }
    }

    func noteKeyUse(id: String, credits: Int) {
        mutate { s in
            guard let i = s.apiKeys.firstIndex(where: { $0.id == id }) else { return }
            s.apiKeys[i].lastUsedAt = .now
            s.apiKeys[i].requestsThisMonth += 1
            s.apiKeys[i].creditsThisMonth += credits
        }
    }

    func rename(_ name: String) {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != state.me.name else { return }
        let old = "\(state.me.name)'s laptop", new = "\(name)'s laptop"
        mutate { s in
            if let i = s.people.firstIndex(where: { $0.id == s.meID }) { s.people[i].name = name }
            // The sample key and its requests are named after you
            for i in s.apiKeys.indices where s.apiKeys[i].name == old { s.apiKeys[i].name = new }
            for i in s.requests.indices where s.requests[i].source == old { s.requests[i].source = new }
        }
    }

    func markBadgesSeen() {
        guard state.badges.contains(where: \.isNew) else { return }
        mutate { s in for i in s.badges.indices { s.badges[i].isNew = false } }
    }

    // MARK: Time

    func tick(seconds: Double, lendingActive: Bool, working: Bool) {
        tick(seconds: seconds, lendingActive: lendingActive, working: working, network: false)
    }

    /// The compute this Mac does for a pool in `seconds` of work, in petaFLOPs: its share of every token's
    /// two FLOPs per active parameter, at the pool's speed.
    static func computePF(pool: Pool, lendGB: Int, seconds: Double) -> Double {
        guard let model = pool.model, pool.pooledGB > 0 else { return 0 }
        let share = min(1, Double(lendGB) / Double(pool.pooledGB))
        return Double(pool.tokPerSec) * seconds * 2 * model.activeB * 1e9 * share / 1e15
    }

    /// `network`: the work came from the wider network, through a friends' pool that offers its idle hours.
    func tick(seconds: Double, lendingActive: Bool, working: Bool, network: Bool) {
        // The network's count of lending Macs breathes a little
        if Double.random(in: 0...1) < 0.1 {
            state.networkLendingNow = max(2_800, min(3_050, state.networkLendingNow + Int.random(in: -3...3)))
        }
        // Friends who fell asleep wake up after a while
        for pi in state.pools.indices where !state.pools[pi].isPublic {
            for mi in state.pools[pi].macs.indices where state.pools[pi].macs[mi].state == .asleep {
                if Double.random(in: 0...1) < 0.004 * max(1, seconds) { state.pools[pi].macs[mi].state = .lending }
            }
        }
        // Your Mac earns while it lends: more while it's working on someone's question
        if lendingActive, let pool = state.lendingPool {
            let perHour = Double(pool.creditsPerHour(lendGB: state.lending.limitGB))
            let earned = perHour * seconds / 3_600 * (working ? 1.0 : 0.6)
            let compute = working ? Self.computePF(pool: pool, lendGB: state.lending.limitGB, seconds: seconds) : 0
            let hoursBefore = state.hoursLent ?? 0
            state.credits += earned
            state.earnedAllTime += earned
            state.hoursLent = hoursBefore + seconds / 3_600
            state.computePF = (state.computePF ?? 0) + compute
            noteNight(pool: pool, seconds: seconds, working: working)
            // One credit line per lending session, updated as it grows, with the time and the measured compute
            let kind = network ? "network" : "lend"
            if let id = sessionEventID, let i = state.creditLog.firstIndex(where: { $0.id == id }), state.creditLog[i].kind ?? "lend" == kind {
                sessionEarned += earned
                state.creditLog[i].amount = Int(sessionEarned.rounded(.down))
                state.creditLog[i].minutes = (state.creditLog[i].minutes ?? 0) + seconds / 60
                state.creditLog[i].computePF = (state.creditLog[i].computePF ?? 0) + compute
            } else if earned > 0 {
                sessionEarned = earned
                let note = network ? "Network work through \(pool.name)" : "Your Mac lent to \(pool.name)"
                let event = CreditEvent(date: .now, amount: Int(earned.rounded(.down)), note: note, kind: kind, poolID: pool.id,
                                        minutes: seconds / 60, computePF: compute)
                state.creditLog.insert(event, at: 0)
                sessionEventID = event.id
            }
            // A badge for showing up, whatever the Mac
            if hoursBefore < 100, state.hoursLent ?? 0 >= 100, !state.badges.contains(where: { $0.id == "hours-100" }) {
                state.badges.insert(Badge(id: "hours-100", symbol: "clock.fill", title: "100 hours",
                                          detail: "Lent 100 hours to your pools", date: .now, isNew: true), at: 0)
            }
        }
        sinceSave += 1
        if sinceSave > 30 { sinceSave = 0; scheduleSave() }
    }

    /// Tonight in this Mac's history: the time it lent, and the runs it took part in at the pool's speed.
    /// A friend in the pool thanks your Mac once it has done some work there.
    private func noteNight(pool: Pool, seconds: Double, working: Bool) {
        let lent = min(seconds, 8 * 3_600)
        runCarry += working ? lent * Double(max(1, pool.tokPerSec)) / Double(Runs.tokensPerRun) : 0
        let runs = Int(runCarry)
        runCarry -= Double(runs)
        state.questionsHelped += runs
        let night = Date().nightStart
        var nights = state.nights ?? []
        if let i = nights.indices.last, nights[i].date == night, nights[i].poolID == pool.id {
            nights[i].minutes += lent / 60
            nights[i].runs += runs
            nights[i].from = nights[i].from ?? Date().addingTimeInterval(-lent)
            nights[i].to = .now
        } else {
            nights.append(NightLent(date: night, poolID: pool.id, minutes: lent / 60, runs: runs,
                                    from: Date().addingTimeInterval(-lent), to: .now))
        }
        state.nights = nights
        let tonight = nights.last?.runs ?? 0
        guard !pool.isPublic, tonight >= 20, let pi = state.pools.firstIndex(where: { $0.id == pool.id }) else { return }
        let thanked = (state.pools[pi].activity ?? []).contains { $0.kind == "thanks" && $0.date >= night }
        let friend = pool.macs.first { $0.macID != state.thisMacID && $0.state == .lending }
            .flatMap { state.device($0.macID)?.ownerID }
        if !thanked, let friend {
            state.pools[pi].activity = [PoolActivity(id: "thanks-\(Int(Date().timeIntervalSince1970))", date: .now,
                                                     personID: friend, kind: "thanks")] + (state.pools[pi].activity ?? [])
        }
    }

    func reset() {
        state = Simulation(fresh: true, persists: false).state
        sessionEventID = nil
        saveNow()
    }

    // MARK: Persistence

    private func scheduleSave() {
        guard persists else { return }
        saveWork?.cancel()
        let work = DispatchWorkItem { [weak self] in MainActor.assumeIsolated { self?.saveNow() } }
        saveWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8, execute: work)
    }

    func saveNow() {
        guard let url = store else { return }
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder.dots.encode(state).write(to: url, options: .atomic)
        } catch {
            NSLog("dots: couldn't save the network state: \(error)")
        }
    }

    // MARK: Helpers

    nonisolated static func randomCode(_ n: Int) -> String {
        let chars = Array("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789")
        return String((0..<n).map { _ in chars.randomElement()! })
    }

    nonisolated static func hash(_ secret: String) -> String {
        SHA256.hash(data: Data(secret.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}
