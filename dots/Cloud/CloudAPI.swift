import Foundation

// The dots cloud, as the app sees it: the hub (pools, workers, measured compute, pool answers), the
// dots server (people, invites, the credit ledger, badges, weekly notes) and the dots gateway (frontier
// models paid in credits). API.md describes every call. MockCloud answers all of it on this Mac today.

// MARK: - What the cloud sends

/// The part of the network the cloud owns. Your API keys, your request log and your lending rules stay on this Mac.
struct CloudState: Codable {
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
    var networkLendingNow: Int
    var lendingPoolID: String?
    var lendingSince: Date?
    var endorsedBefore: Int
    var hoursLent: Double?
    var computePF: Double?
    var nights: [NightLent]?
}

/// What your Mac is doing for its pool, as the hub sees it: the layers it holds and whether it's working now.
struct WorkerStatus: Codable, Equatable {
    var poolID: String
    var poolName: String
    var model: String
    var firstLayer: Int
    var lastLayer: Int
    var layers: Int
    var working: Bool
    var network: Bool = false        // working for the network through a friends' pool

    var line: String { "Layers \(firstLayer + 1) to \(lastLayer + 1) of \(layers) in \(model)" }
}

/// A week of your Mac's work and your pools' life, sent once a week.
struct WeeklyNote: Codable, Equatable, Identifiable {
    struct PoolLine: Codable, Equatable, Hashable {
        var name: String
        var answered: Int
        var sharePercent: Int
    }
    var id: String
    var issued: Date
    var hoursLent: Double
    var computePF: Double
    var creditsEarned: Int
    var creditsSpent: Int
    var questionsHelped: Int
    var pools: [PoolLine]
    var badges: [String]
    var headline: String
}

/// An invite link, opened before joining: the pool, and what your Mac would add to it.
struct InviteDetails: Codable, Equatable, Identifiable {
    var id: String { code }
    var code: String
    var poolID: String
    var poolName: String
    var blurb: String
    var fromName: String?
    var macs: Int
    var pooledGB: Int
    var modelNow: String?
    var addGB: Int
    /// The model your Mac would take the pool to, if it crosses a line
    var unlocks: String?
    /// Otherwise, how far the pool would still be from its next model
    var nextModel: String?
    var shortGB: Int?
    var isMember: Bool
}

/// Everything the app shows from the cloud, in one answer.
struct CloudSync: Codable {
    var state: CloudState
    var terms: Terms
    var note: WeeklyNote?
    var worker: WorkerStatus?
    var invites: [InviteDetails]
    /// Answers and pools are simulated by the mock cloud
    var mock: Bool
}

/// This Mac's report to the hub, every couple of seconds.
struct Heartbeat: Codable {
    var active: Bool
    var lendGB: Int
    /// Seconds of simulated time since the last report (the demo speed is already applied)
    var seconds: Double
    var macKind: String
    var memoryGB: Int
}

struct HeartbeatReply: Codable { var sync: CloudSync; var unlock: UnlockEvent? }
struct JoinReply: Codable { var macNumber: Int; var poolID: String; var unlock: UnlockEvent?; var sync: CloudSync }
struct BadgeReply: Codable { var badge: Badge?; var sync: CloudSync }
struct PoolReply: Codable { var pool: Pool; var sync: CloudSync }
struct InviteReply: Codable { var invite: Invite; var sync: CloudSync }

// MARK: - Calling it

/// Calls the dots cloud: one base URL and one token for the hub, the server and the gateway.
struct CloudClient {
    var baseURL: URL
    var token: String

    func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        try await call("GET", path, query: query, body: nil)
    }

    func send<T: Decodable>(_ method: String, _ path: String, body: (any Encodable)? = nil) async throws -> T {
        try await call(method, path, query: [], body: body)
    }

    private func call<T: Decodable>(_ method: String, _ path: String, query: [URLQueryItem], body: (any Encodable)?) async throws -> T {
        guard var parts = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false) else {
            throw TransportError.badEndpoint
        }
        if !query.isEmpty { parts.queryItems = query }
        guard let url = parts.url else { throw TransportError.badEndpoint }
        var request = URLRequest(url: url, timeoutInterval: 20)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder.dots.encode(AnyEncodable(body))
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw TransportError.http(http.statusCode, String(decoding: data, as: UTF8.self))
        }
        return try JSONDecoder.dots.decode(T.self, from: data)
    }

    /// A pool's answers, from the hub. It speaks the OpenAI API.
    func hub(poolID: String, model: String) -> EndpointConfig {
        var e = EndpointConfig(enabled: true, label: "dots hub", baseURL: baseURL.appendingPathComponent("hub/v1/pools/\(poolID)").absoluteString,
                               model: model, keychainAccount: "")
        e.token = token
        return e
    }

    /// Frontier models paid in credits, from the gateway. It speaks both the OpenAI API and Anthropic's Messages API.
    func gateway(model: String) -> EndpointConfig {
        var e = EndpointConfig(enabled: true, label: "dots gateway", baseURL: baseURL.appendingPathComponent("gateway/v1").absoluteString,
                               model: model, keychainAccount: "")
        e.token = token
        return e
    }
}

/// Lets `send` take any Encodable body.
private struct AnyEncodable: Encodable {
    let value: any Encodable
    init(_ value: any Encodable) { self.value = value }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}
