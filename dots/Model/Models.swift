import Foundation

// MARK: - Models the network can run

/// What a pool can run is set by how much memory its Macs lend (4-bit weights plus room for the cache).
/// The lines follow the case study: Qwen3 235B at 136 GB, DeepSeek V3.1 at 396, Kimi K2 at 620.
struct ModelTier: Codable, Hashable, Identifiable {
    var id: String
    var name: String
    var minGB: Int
    var params: String          // "671B"
    var paramsLong: String      // "671 billion parameters"
    var tokPerSec: Double       // on a nearby pool
    var credits: Int            // what an answer costs on a public pool
}

enum Ladder {
    static let tiers: [ModelTier] = [
        ModelTier(id: "llama-3.1-8b", name: "Llama 3.1 8B", minGB: 6, params: "8B", paramsLong: "8 billion parameters", tokPerSec: 62, credits: 1),
        ModelTier(id: "gemma-3-27b", name: "Gemma 3 27B", minGB: 18, params: "27B", paramsLong: "27 billion parameters", tokPerSec: 38, credits: 1),
        ModelTier(id: "llama-3.3-70b", name: "Llama 3.3 70B", minGB: 40, params: "70B", paramsLong: "70 billion parameters", tokPerSec: 19, credits: 1),
        ModelTier(id: "qwen3-235b", name: "Qwen3 235B", minGB: 136, params: "235B", paramsLong: "235 billion parameters", tokPerSec: 24, credits: 2),
        ModelTier(id: "deepseek-v3.1", name: "DeepSeek V3.1", minGB: 396, params: "671B", paramsLong: "671 billion parameters", tokPerSec: 11, credits: 3),
        ModelTier(id: "kimi-k2", name: "Kimi K2", minGB: 620, params: "1T", paramsLong: "1 trillion parameters", tokPerSec: 9, credits: 4),
    ]

    /// Coding pools can prefer a coding model when they have room for it.
    static let special: [ModelTier] = [
        ModelTier(id: "qwen3-coder-480b", name: "Qwen3 Coder 480B", minGB: 270, params: "480B", paramsLong: "480 billion parameters", tokPerSec: 13, credits: 3),
    ]

    static func tier(_ id: String) -> ModelTier? { (tiers + special).first { $0.id == id } }
    static func best(for gb: Int) -> ModelTier? { tiers.last { $0.minGB <= gb } }
    static func next(after gb: Int) -> ModelTier? { tiers.first { $0.minGB > gb } }
}

/// Frontier models reached through the dots gateway, paid in credits at a discount.
struct FrontierModel: Codable, Hashable, Identifiable {
    var id: String
    var name: String
    var provider: String
    var listCredits: Int
    var credits: Int
    var tokPerSec: Double

    static let opus = FrontierModel(id: "claude-opus-5-5", name: "Claude Opus 5.5", provider: "Anthropic", listCredits: 31, credits: 22, tokPerSec: 55)
    static let sonnet = FrontierModel(id: "claude-sonnet-5", name: "Claude Sonnet 5", provider: "Anthropic", listCredits: 11, credits: 8, tokPerSec: 80)
    static let all = [opus, sonnet]
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
    /// How close the pool's Macs are to you: scales the speed you'd get.
    var speedFactor = 1.0
    /// How busy the pool is: scales what a lending Mac earns there.
    var demand = 0.8
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
    var isNew: Bool { createdAt > Date().addingTimeInterval(-7 * 86_400) }
    var tokPerSec: Int { Int(((model?.tokPerSec ?? 0) * speedFactor).rounded()) }

    func creditsPerHour(lendGB: Int) -> Int { max(1, Int((Double(lendGB) / 3 * demand).rounded())) }
    func isMember(_ personID: String) -> Bool { memberIDs.contains(personID) }
}

// MARK: - You

struct LendingSettings: Codable, Hashable {
    var isOn = false
    var limitGB = 0
    var onlyWhenPluggedIn = true
    var pauseWhileInUse = false
    var pausedUntil: Date?
}

struct CreditEvent: Codable, Hashable, Identifiable {
    var id = UUID()
    var date: Date
    var amount: Int
    var note: String
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
}

enum Destination: String, Codable, Hashable { case yourPool, publicPool, frontier }

struct RequestRecord: Codable, Hashable, Identifiable {
    var id = UUID()
    var date: Date
    var source: String          // "Ask" or an API key's name
    var destination: Destination
    var place: String
    var model: String
    var credits: Int
    var isPrivate: Bool
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
    var lendingPoolID: String?
    var networkLendingNow: Int
    var lendingSince: Date?
    /// Pools endorsed before this history starts (the sample profile has a past).
    var endorsedBefore = 0

    var me: Person { people.first { $0.id == meID }! }
    var thisMac: MacDevice { devices.first { $0.id == thisMacID }! }
    func person(_ id: String) -> Person? { people.first { $0.id == id } }
    func device(_ id: String) -> MacDevice? { devices.first { $0.id == id } }
    func pool(_ id: String?) -> Pool? { guard let id else { return nil }; return pools.first { $0.id == id } }
    var myPools: [Pool] { pools.filter { $0.isMember(meID) } }
    var publicPools: [Pool] { pools.filter(\.isPublic) }
    var lendingPool: Pool? { pool(lendingPoolID) }
    var poolsEndorsed: Int { endorsedBefore + pools.filter(\.endorsedByMe).count }
}
