import Foundation
import Security

/// An OpenAI-compatible endpoint the router can send real questions to. The key lives in the Keychain.
struct EndpointConfig: Codable, Equatable {
    var enabled: Bool
    var label: String
    var baseURL: String
    var model: String
    var keychainAccount: String
    /// The workspace an Anthropic key acts in, for a key made for more than one workspace (`wrkspc_…`).
    /// Not a secret, so it's saved with the settings. Empty for a key made for one workspace.
    var workspaceID: String? = nil
    /// A token handed over in code (the dots cloud's), used instead of a key from the Keychain. Never saved.
    var token: String? = nil
    /// For a dots pool: whether a draft model may guess ahead on this request. Set per request, never saved.
    var draftAhead: Bool? = nil

    enum CodingKeys: String, CodingKey { case enabled, label, baseURL, model, keychainAccount, workspaceID }

    var apiKey: String? { token ?? Keychain.get(keychainAccount) }
    /// Sent as `anthropic-workspace-id` on every request when set.
    var workspace: String? {
        let id = (workspaceID ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return id.isEmpty ? nil : id
    }
    var isAnthropic: Bool { url?.host?.lowercased().hasSuffix("anthropic.com") == true }
    var url: URL? { URL(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)) }
    /// Ready to answer: switched on, with an address and a model, and a key unless it runs on this Mac.
    /// A remote endpoint without its key stays simulated instead of failing every request.
    var isReady: Bool {
        enabled && url != nil && !model.trimmingCharacters(in: .whitespaces).isEmpty && (isLocal || !(apiKey ?? "").isEmpty)
    }

    /// Runs on this Mac (LM Studio, Ollama): nothing sent to it leaves the Mac, and it costs no credits.
    var isLocal: Bool {
        guard let host = url?.host?.lowercased() else { return false }
        return host == "localhost" || host == "127.0.0.1" || host == "::1" || host.hasSuffix(".localhost")
    }

    /// The app serving it, when it's a known one on this Mac.
    var serverName: String? {
        guard isLocal else { return nil }
        switch url?.port { case 1234: return "LM Studio"; case 11434: return "Ollama"; default: return nil }
    }

    /// Where answers come from, named from the address (a label can go stale when the address changes).
    var placeName: String {
        if isLocal { return serverName ?? url.map { "localhost:\($0.port ?? 80)" } ?? "this Mac" }
        guard let host = url?.host else { return label }
        return host.hasSuffix("anthropic.com") ? "Anthropic" : host
    }
}

struct RouterRules: Codable, Equatable {
    enum Prefer: String, Codable, CaseIterable, Identifiable {
        case speed, balanced, quality
        var id: String { rawValue }
        var title: String { rawValue.capitalized }
    }
    var privateByDefault = false
    var maxCreditsPerAnswer = 30
    var prefer: Prefer = .balanced

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        privateByDefault = try c.decodeIfPresent(Bool.self, forKey: .privateByDefault) ?? false
        maxCreditsPerAnswer = try c.decodeIfPresent(Int.self, forKey: .maxCreditsPerAnswer) ?? 30
        prefer = try c.decodeIfPresent(Prefer.self, forKey: .prefer) ?? .balanced
    }
}

/// Where the dots cloud is: the mock on this Mac, or a cloud at an address.
struct CloudSettings: Codable, Equatable {
    var useMock = true
    var mockPort = 8790
    var address = ""

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        useMock = try c.decodeIfPresent(Bool.self, forKey: .useMock) ?? true
        mockPort = try c.decodeIfPresent(Int.self, forKey: .mockPort) ?? 8790
        address = try c.decodeIfPresent(String.self, forKey: .address) ?? ""
    }
}

/// How frontier answers are paid for: with credits through the dots gateway, or billed to your own key.
enum PayWith: String, Codable, CaseIterable, Identifiable {
    case credits, key
    var id: String { rawValue }
    var title: String { self == .credits ? "Credits first" : "My key first" }
}

struct AppSettings: Codable, Equatable {
    /// Where "your pools" answers come from when set: the dots hub, or a model on this Mac (LM Studio, Ollama).
    var poolEndpoint = EndpointConfig(enabled: false, label: "Local", baseURL: "http://localhost:1234/v1",
                                      model: "", keychainAccount: "pool-endpoint")
    /// Where frontier answers come from when set. Anthropic speaks the OpenAI API at this address.
    var frontierEndpoint = EndpointConfig(enabled: false, label: "Anthropic", baseURL: "https://api.anthropic.com/v1",
                                          model: "claude-opus-5-5", keychainAccount: "frontier-endpoint")
    var rules = RouterRules()
    var gatewayEnabled = true
    var gatewayPort = 8787
    /// How fast simulated time runs, for demos: credits, sleeping Macs.
    var demoSpeed: Double = 1
    var cloud = CloudSettings()
    var payFrontierWith: PayWith = .credits
    /// What your savings are measured against: the frontier model you'd otherwise send everything to
    var savingsBaseline = FrontierModel.opus.id
    /// Let a pool's draft model guess ahead, so the pool checks several words at once
    var draftAhead = true
    /// Pictures are shrunk to this many pixels before they're sent: what a pool reads at most (DOTS_IMAGE_MAX_PIXELS)
    var imageMaxPixels = 1_048_576

    init() {}
    /// Settings saved by an older dots load with defaults for anything new.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = AppSettings()
        poolEndpoint = try c.decodeIfPresent(EndpointConfig.self, forKey: .poolEndpoint) ?? d.poolEndpoint
        frontierEndpoint = try c.decodeIfPresent(EndpointConfig.self, forKey: .frontierEndpoint) ?? d.frontierEndpoint
        rules = try c.decodeIfPresent(RouterRules.self, forKey: .rules) ?? d.rules
        gatewayEnabled = try c.decodeIfPresent(Bool.self, forKey: .gatewayEnabled) ?? d.gatewayEnabled
        gatewayPort = try c.decodeIfPresent(Int.self, forKey: .gatewayPort) ?? d.gatewayPort
        demoSpeed = try c.decodeIfPresent(Double.self, forKey: .demoSpeed) ?? d.demoSpeed
        cloud = try c.decodeIfPresent(CloudSettings.self, forKey: .cloud) ?? d.cloud
        payFrontierWith = try c.decodeIfPresent(PayWith.self, forKey: .payFrontierWith) ?? d.payFrontierWith
        savingsBaseline = try c.decodeIfPresent(String.self, forKey: .savingsBaseline) ?? d.savingsBaseline
        draftAhead = try c.decodeIfPresent(Bool.self, forKey: .draftAhead) ?? d.draftAhead
        imageMaxPixels = try c.decodeIfPresent(Int.self, forKey: .imageMaxPixels) ?? d.imageMaxPixels
    }

    private static let defaultsKey = "dots.settings.v1"

    static func load() -> AppSettings {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey),
              let s = try? JSONDecoder().decode(AppSettings.self, from: data) else { return AppSettings() }
        return s
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) { UserDefaults.standard.set(data, forKey: Self.defaultsKey) }
    }
}

/// Endpoint keys, kept in the login Keychain rather than in preferences.
enum Keychain {
    private static let service = "com.sourabhdaroji.dots"

    static func get(_ account: String) -> String? {
        var query: [String: Any] = base(account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func set(_ value: String, for account: String) {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { delete(account); return }
        let data = Data(trimmed.utf8)
        let status = SecItemUpdate(base(account) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var add = base(account)
            add[kSecValueData as String] = data
            SecItemAdd(add as CFDictionary, nil)
        }
    }

    static func delete(_ account: String) {
        SecItemDelete(base(account) as CFDictionary)
    }

    private static func base(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }
}
