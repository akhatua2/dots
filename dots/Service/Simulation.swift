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

/// The dots network, simulated on this Mac: pools, members, lending, unlocks, credits, badges and keys.
/// State lives in Application Support/dots/network.json, so it survives a relaunch.
@MainActor
@Observable
final class Simulation: DotsBackend {
    private(set) var state: NetworkState
    @ObservationIgnored private var saveWork: DispatchWorkItem?
    @ObservationIgnored private var sinceSave: Double = 0
    @ObservationIgnored private var sessionEventID: UUID?
    /// False for snapshots and the self-test, which must never write over your saved network
    private let persists: Bool

    init(fresh: Bool = false, persists: Bool = true) {
        self.persists = persists
        if !fresh, let data = try? Data(contentsOf: Self.storeURL),
           let saved = try? JSONDecoder.dots.decode(NetworkState.self, from: data) {
            state = saved
        } else {
            state = SampleData.initialState()
        }
        // Keep this Mac's real specs current
        if let i = state.devices.firstIndex(where: { $0.id == state.thisMacID }) {
            state.devices[i].memoryGB = ThisMac.memoryGB
            state.devices[i].kind = ThisMac.kind
        }
        state.lending.limitGB = min(state.lending.limitGB, state.thisMac.maxLendGB)
    }

    static var storeURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("dots", isDirectory: true)
            .appendingPathComponent("network.json")
    }

    private func mutate(_ body: (inout NetworkState) -> Void) {
        body(&state)
        scheduleSave()
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

    func createPool(name: String, isPublic: Bool) -> Pool {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let hash = clean.unicodeScalars.reduce(UInt64(5)) { $0 &* 31 &+ UInt64($1.value) }
        let pool = Pool(id: "pool-\(Self.randomCode(6).lowercased())", name: clean, isPublic: isPublic,
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

    func createInvite(poolID: String) -> Invite {
        let invite = Invite(code: Self.randomCode(6), expires: Date().addingTimeInterval(7 * 86_400))
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
            s.creditLog.insert(CreditEvent(date: .now, amount: -credits, note: note), at: 0)
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
            state.credits += earned
            state.earnedAllTime += earned
            if working, Double.random(in: 0...1) < min(1, seconds * 0.05) { state.questionsHelped += 1 }
            // One credit line per lending session, updated as it grows
            if let id = sessionEventID, let i = state.creditLog.firstIndex(where: { $0.id == id }) {
                let total = Int((Double(state.creditLog[i].amount) + earned).rounded(.down))
                if total > state.creditLog[i].amount { state.creditLog[i].amount = total }
            } else if earned > 0 {
                let event = CreditEvent(date: .now, amount: 0, note: "Your Mac lent to \(pool.name)")
                state.creditLog.insert(event, at: 0)
                sessionEventID = event.id
            }
        }
        sinceSave += 1
        if sinceSave > 30 { sinceSave = 0; scheduleSave() }
    }

    func reset() {
        state = Simulation(fresh: true).state
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
        guard persists else { return }
        do {
            let url = Self.storeURL
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder.dots.encode(state).write(to: url, options: .atomic)
        } catch {
            NSLog("dots: couldn't save the network state: \(error)")
        }
    }

    // MARK: Helpers

    static func randomCode(_ n: Int) -> String {
        let chars = Array("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789")
        return String((0..<n).map { _ in chars.randomElement()! })
    }

    static func hash(_ secret: String) -> String {
        SHA256.hash(data: Data(secret.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}
