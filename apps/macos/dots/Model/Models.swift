import Foundation

// MARK: - Models the network can run

/// What a pool can run is set by how much memory its Macs lend (4-bit weights plus room for the cache).
/// The cloud sends the real lines in every sync; these are the offline demo's, from the case study.
struct ModelTier: Codable, Hashable, Identifiable {
    var id: String
    var name: String
    var minGB: Int
    var params: String          // "671B"
    var paramsLong: String      // "671 billion parameters"
    var tokPerSec: Double       // on a nearby pool
    var credits: Int            // what an answer costs on a public pool
    var layers: Int             // transformer layers, shared out across the pool's Macs
    var activeB: Double         // parameters used for each token, in billions (fewer than the total for mixture-of-experts models)
    var readsImages: Bool? = nil  // the cloud says so for models whose vision encoder a pool runs
}

enum Ladder {
    /// The cloud sends the ladder in every sync (see `adopt`). This is the starting point until the first one arrives.
    static private(set) var tiers: [ModelTier] = [
        ModelTier(id: "lfm2.5-8b", name: "LFM2.5 8B", minGB: 6, params: "8B", paramsLong: "8 billion parameters", tokPerSec: 60, credits: 1, layers: 24, activeB: 1.5),
        ModelTier(id: "qwen3.8-27b", name: "Qwen3.8 27B", minGB: 18, params: "27B", paramsLong: "27 billion parameters", tokPerSec: 14, credits: 1, layers: 64, activeB: 27),
        ModelTier(id: "qwen3.6-35b", name: "Qwen3.6 35B", minGB: 40, params: "35B", paramsLong: "35 billion parameters", tokPerSec: 35, credits: 1, layers: 40, activeB: 3),
        ModelTier(id: "minimax-m2.7", name: "MiniMax M2.7", minGB: 136, params: "230B", paramsLong: "230 billion parameters", tokPerSec: 18, credits: 2, layers: 62, activeB: 10),
        ModelTier(id: "glm-5.3", name: "GLM 5.3", minGB: 396, params: "744B", paramsLong: "744 billion parameters", tokPerSec: 9, credits: 3, layers: 78, activeB: 40),
        ModelTier(id: "kimi-k2.5", name: "Kimi K2.5", minGB: 620, params: "1T", paramsLong: "1 trillion parameters", tokPerSec: 8, credits: 4, layers: 61, activeB: 32),
    ]

    /// Coding pools can prefer a coding model when they have room for it.
    static private(set) var special: [ModelTier] = [
        ModelTier(id: "qwen3-coder-next", name: "Qwen3 Coder Next", minGB: 270, params: "80B", paramsLong: "80 billion parameters", tokPerSec: 30, credits: 3, layers: 48, activeB: 3),
    ]

    /// Takes the ladder the cloud sent, so a pool's models are the ones its hub can actually serve.
    static func adopt(_ ladder: LadderData) {
        guard !ladder.tiers.isEmpty else { return }
        tiers = ladder.tiers
        special = ladder.special
    }

    static func tier(_ id: String) -> ModelTier? { (tiers + special).first { $0.id == id } }
    static func best(for gb: Int) -> ModelTier? { tiers.last { $0.minGB <= gb } }
    static func next(after gb: Int) -> ModelTier? { tiers.first { $0.minGB > gb } }
}

/// Frontier models reached through the dots gateway, paid in credits at a discount, or on your own key.
struct FrontierModel: Hashable, Identifiable {
    var id: String
    var name: String
    var provider: String
    var tokPerSec: Double

    /// What an everyday answer costs through the gateway, in credits, and what it would at list price.
    var credits: Int { Pricing.typicalCredits(id) }
    var listCredits: Int { Pricing.typicalListCredits(id) }

    static let opus = FrontierModel(id: "claude-opus-5-5", name: "Claude Opus 5.5", provider: "Anthropic", tokPerSec: 55)
    static let sonnet = FrontierModel(id: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", provider: "Anthropic", tokPerSec: 80)
    static let all = [opus, sonnet]
    static func named(_ id: String) -> FrontierModel? { all.first { $0.id == id } }
}

/// What a run is in tokens, on average: a prompt and its answer.
enum Runs {
    /// The mock's stand-in for tokens in a run; a real cloud counts them
    static let tokensPerRun = 420
    /// 160_000 -> "160K", 5_210_520 -> "5.2M"
    static func short(_ n: Int) -> String {
        switch n {
        case 1_000_000...: let m = Double(n) / 1e6; return (m >= 10 ? String(format: "%.0fM", m) : String(format: "%.1fM", m)).replacingOccurrences(of: ".0M", with: "M")
        case 1_000...: let k = Double(n) / 1e3; return (k >= 10 ? String(format: "%.0fK", k) : String(format: "%.1fK", k)).replacingOccurrences(of: ".0K", with: "K")
        default: return "\(n)"
        }
    }
}

// MARK: - People, Macs, pools

struct Person: Codable, Hashable, Identifiable {
    var id: String
    var name: String
    var initial: String { String(name.prefix(1)).uppercased() }
}

struct MacDevice: Codable, Hashable, Identifiable {
    var id: String
    var ownerID: String
    var kind: String            // "MacBook Pro"
    var memoryGB: Int

    /// Every Mac keeps a quarter of its memory for itself, and at least 8 GB.
    var keptGB: Int { max(memoryGB / 4, 8) }
    var maxLendGB: Int { max(0, memoryGB - keptGB) }
}

enum MacState: String, Codable { case lending, online, asleep }

struct PoolMac: Codable, Hashable, Identifiable {
    var id: String { macID }
    var macID: String
    var lendGB: Int
    var state: MacState
    var joinedAt: Date
}

struct UnlockEvent: Codable, Hashable, Identifiable {
    var id = UUID()
    /// The pool it happened in, when the cloud says
    var poolID: String?
    var modelID: String
    var byMacID: String
    var fromGB: Int
    var toGB: Int
    var date: Date
}

struct Invite: Codable, Hashable {
    var code: String
    var expires: Date
    var url: String { "https://dots.link/\(code)" }
}

struct Pool: Codable, Hashable, Identifiable {
    var id: String
    var name: String
    var isPublic: Bool
    var blurb: String
    var tags: [String]
    var memberIDs: [String]
    var macs: [PoolMac]
    /// A public pool has more Macs than we list one by one.
    var crowdMacs = 0
    var crowdLending = 0
    var crowdGB = 0
    var endorsements = 0
    var endorsedByMe = false
    var endorsedAtMacs: Int?
    var hostID: String?
    /// A friends' pool can offer its idle hours to the network, so its Macs earn credits too.
    var offersIdleToNetwork = false
    var invite: Invite?
    var unlocks: [UnlockEvent] = []
    var phase: Double
    var createdAt: Date
    var servesModelID: String?
    /// The image model a Mac in the pool draws pictures with, if one offers it
    var drawsWith: String? = nil
    /// How close the pool's Macs are to you: scales the speed you'd get.
    var speedFactor = 1.0
    /// How busy the pool is: scales what a lending Mac earns there.
    var demand = 0.8
    /// A boost the pool plans: a window when its members commit their Macs, to reach a bigger model for a few hours
    var boost: PoolBoost?
    /// What happened in the pool lately: thanks, people stepping away, milestones
    var activity: [PoolActivity]?
}

/// A boost: a window, at any time of day, when a pool's members commit their Macs at once, so the pool can run
/// a bigger model for a few hours. Friends from outside the pool can boost as guests, just that once.
struct PoolBoost: Codable, Hashable {
    struct Pledge: Codable, Hashable {
        var personID: String
        var gb: Int
        var guest: Bool
    }
    var title: String
    var modelID: String
    var starts: Date
    var hours: Double
    var plannedBy: String
    var pledges: [Pledge]

    var targetGB: Int { Ladder.tier(modelID)?.minGB ?? 0 }
    var pledgedGB: Int { pledges.reduce(0) { $0 + $1.gb } }
    var ends: Date { starts.addingTimeInterval(hours * 3_600) }
    func pledge(of personID: String) -> Pledge? { pledges.first { $0.personID == personID } }
}

/// Something that happened in a pool, for its activity list. `kind` is "thanks" (to your Mac), "away"
/// (a Mac stepped away), "boost" (a boost was planned) or "milestone", which carries its own `text`.
struct PoolActivity: Codable, Hashable, Identifiable {
    var id: String
    var date: Date
    var personID: String?
    var kind: String
    var gb: Int?
    var text: String?
}

/// A night this Mac lent: where, for how long, and the runs it took part in. Nights run from noon to noon.
extension NetworkState {
    /// Everything this Mac has answered, in one pool or in all of them, and the tokens in it.
    func served(in poolID: String? = nil) -> (runs: Int, tokens: Int) {
        let rows = (nights ?? []).filter { poolID == nil || $0.poolID == poolID }
        return (rows.reduce(0) { $0 + $1.runs }, rows.reduce(0) { $0 + $1.tokenCount })
    }
}

struct NightLent: Codable, Hashable {
    var date: Date
    var poolID: String
    var minutes: Double
    var runs: Int
    /// Tokens in those runs, from the hub's ledger; nil from the mock, which only counts runs
    var tokens: Int?

    var tokenCount: Int { tokens ?? runs * Runs.tokensPerRun }
    /// When this Mac began and last lent that night
    var from: Date?
    var to: Date?
}

extension Date {
    /// The noon a night began: nights run from noon to noon, so 2 AM belongs to the evening before.
    var nightStart: Date {
        let shifted = addingTimeInterval(-12 * 3_600)
        return Calendar.current.date(bySettingHour: 12, minute: 0, second: 0, of: shifted) ?? shifted
    }
}

extension Pool {
    var totalMacs: Int { macs.count + crowdMacs }
    var lendingMacs: Int { macs.filter { $0.state == .lending }.count + crowdLending }
    var pooledGB: Int { macs.filter { $0.state == .lending }.reduce(0) { $0 + $1.lendGB } + crowdGB }

    var model: ModelTier? {
        if let id = servesModelID, let special = Ladder.tier(id), special.minGB <= pooledGB { return special }
        return Ladder.best(for: pooledGB)
    }
    var nextModel: ModelTier? { Ladder.next(after: pooledGB) }
    /// Levels climb the model ladder: Level 1 runs the smallest model.
    var level: Int { (Ladder.tiers.lastIndex { $0.minGB <= pooledGB } ?? -1) + 1 }
    var isNew: Bool { createdAt > Date().addingTimeInterval(-7 * 86_400) }
    var tokPerSec: Int { Int(((model?.tokPerSec ?? 0) * speedFactor).rounded()) }

    func creditsPerHour(lendGB: Int) -> Int { max(1, Int((Double(lendGB) / 3 * demand).rounded())) }
    func isMember(_ personID: String) -> Bool { memberIDs.contains(personID) }
}

// MARK: - You

/// This Mac's rules for lending, the same for every pool it lends to. How much each pool gets is its share.
struct LendingSettings: Codable, Hashable {
    var isOn = false
    var onlyWhenPluggedIn = true
    var pauseWhileInUse = false
    var pausedUntil: Date?
}

/// What this Mac lends one pool: some of its memory, held by a worker of its own for that pool.
struct LendingShare: Codable, Hashable {
    var poolID: String
    var gb: Int
    var since: Date?
    /// The pool's name at the hub, which its worker gives: the hub's own pool is its default ("")
    var hubPool: String?
}

struct CreditEvent: Codable, Hashable, Identifiable {
    var id = UUID()
    var date: Date
    var amount: Int
    var note: String
    /// "lend" (work for your pool), "network" (your pool's idle hours, offered to the network) or "spend"
    var kind: String?
    var poolID: String?
    /// For work: how long, and the compute the hub measured, in petaFLOPs
    var minutes: Double?
    var computePF: Double?
}

struct Badge: Codable, Hashable, Identifiable {
    var id: String
    var symbol: String
    var title: String
    var detail: String
    var date: Date
    var isNew = false
}

struct Reach: OptionSet, Codable, Hashable {
    let rawValue: Int
    static let yourPools = Reach(rawValue: 1)
    static let publicPools = Reach(rawValue: 2)
    static let frontier = Reach(rawValue: 4)
    static let all: Reach = [.yourPools, .publicPools, .frontier]

    var summary: String {
        switch self {
        case .all: "Everything"
        case .yourPools: "Your pools only"
        case [.yourPools, .publicPools]: "Your pools and public pools"
        case [.yourPools, .frontier]: "Your pools and frontier models"
        default: "Custom"
        }
    }
}

struct APIKey: Codable, Hashable, Identifiable {
    var id: String
    var name: String
    var last4: String
    var secretHash: String
    var reach: Reach
    var monthlyLimit: Int?
    var createdAt: Date
    var lastUsedAt: Date?
    var creditsThisMonth = 0
    var requestsThisMonth = 0
    /// Everything this key sends stays on this Mac and in your own pools
    var privateOnly: Bool?
    /// The most this key may bill to your own frontier key in a month, in dollars
    var monthlyCapUSD: Double?
    var usdThisMonth: Double?

    var rulesSummary: String {
        var parts = [privateOnly == true ? "Private: this Mac and your pools" : reach.summary]
        if let monthlyLimit { parts.append("\(monthlyLimit.grouped) credits a month") }
        if let cap = monthlyCapUSD { parts.append("\(cap.dollars) a month on your key") }
        return parts.joined(separator: ", ")
    }
}

enum Destination: String, Codable, Hashable { case thisMac, yourPool, publicPool, frontier }

struct RequestRecord: Codable, Hashable, Identifiable {
    var id = UUID()
    var date: Date
    var source: String          // "Ask" or an API key's name
    var destination: Destination
    var place: String
    var model: String
    var credits: Int
    var isPrivate: Bool
    /// Tokens in and out, as the backend reported them (`measured`) or estimated from the text
    var inputTokens: Int?
    var outputTokens: Int?
    var measured: Bool?
    /// Billed to your own frontier key, at list price
    var usd: Double?
    /// What the model you compare against would have cost for the same tokens, at list price
    var baselineUSD: Double?
    var fallback: String?
    var format: String?

    /// What this request cost, in dollars: your key's bill plus the credits, at what a credit is worth.
    var costUSD: Double { (usd ?? 0) + Double(credits) * Pricing.terms.creditUSD }
    var savedUSD: Double { max(0, (baselineUSD ?? 0) - costUSD) }
}

// MARK: - The whole simulated network, as stored on disk

struct NetworkState: Codable {
    var version = 1
    var meID: String
    var thisMacID: String
    var people: [Person]
    var devices: [MacDevice]
    var pools: [Pool]
    var credits: Double
    var earnedAllTime: Double
    var questionsHelped: Int
    var creditLog: [CreditEvent]
    var badges: [Badge]
    var apiKeys: [APIKey]
    var requests: [RequestRecord]
    var lending: LendingSettings
    /// The pools this Mac lends to, an amount to each
    var lendingShares: [LendingShare]
    var networkLendingNow: Int
    /// Pools endorsed before this history starts (the sample profile has a past).
    var endorsedBefore = 0
    /// Hours this Mac has lent, and the compute the hub measured for it, all time
    var hoursLent: Double?
    var computePF: Double?
    /// Invite codes waiting for you (the cloud keeps these)
    var invitesForMe: [String]?
    /// The nights this Mac lent, oldest first (the cloud keeps these)
    var nights: [NightLent]?

    var me: Person { people.first { $0.id == meID }! }
    /// This Mac as the cloud knows it. Before its first heartbeat a cloud may not list it yet, so it falls
    /// back to an unknown Mac rather than failing.
    var thisMac: MacDevice {
        devices.first { $0.id == thisMacID } ?? MacDevice(id: thisMacID, ownerID: meID, kind: "Mac", memoryGB: 0)
    }
    func person(_ id: String) -> Person? { people.first { $0.id == id } }
    func device(_ id: String) -> MacDevice? { devices.first { $0.id == id } }
    func pool(_ id: String?) -> Pool? { guard let id else { return nil }; return pools.first { $0.id == id } }
    var myPools: [Pool] { pools.filter { $0.isMember(meID) } }
    var publicPools: [Pool] { pools.filter(\.isPublic) }
    func share(in poolID: String?) -> LendingShare? { lendingShares.first { $0.poolID == poolID } }
    func isLending(to poolID: String?) -> Bool { share(in: poolID) != nil }
    var lendingPools: [Pool] { lendingShares.compactMap { pool($0.poolID) } }
    var lendingPoolIDs: Set<String> { Set(lendingShares.map(\.poolID)) }
    /// What this Mac lends in all, across its pools
    var lentGB: Int { lendingShares.reduce(0) { $0 + $1.gb } }
    /// The most a pool's share can be: what this Mac can lend, less what its other pools have
    func mostGB(for poolID: String) -> Int {
        max(0, thisMac.maxLendGB - lentGB + (share(in: poolID)?.gb ?? 0))
    }
    /// People you know in a pool, as Explore shows them
    func friends(in pool: Pool) -> [Person] { pool.memberIDs.filter { $0 != meID }.compactMap { person($0) } }
    var poolsEndorsed: Int { endorsedBefore + pools.filter(\.endorsedByMe).count }
}
