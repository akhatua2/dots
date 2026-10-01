import Foundation
import Security

/// An OpenAI-compatible endpoint the router can send real questions to. The key lives in the Keychain.
struct EndpointConfig: Codable, Equatable {
    var enabled: Bool
    var label: String
    var baseURL: String
    var model: String
    var keychainAccount: String

    var apiKey: String? { Keychain.get(keychainAccount) }
    var url: URL? { URL(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)) }
    var isReady: Bool { enabled && url != nil && !model.trimmingCharacters(in: .whitespaces).isEmpty }

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
        if isLocal { return "this Mac" }
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
}

struct AppSettings: Codable, Equatable {
    /// Where "your pools" answers come from when set: the dots hub, or a model on this Mac (LM Studio, Ollama).
    var poolEndpoint = EndpointConfig(enabled: false, label: "Your pools", baseURL: "http://localhost:1234/v1",
                                      model: "", keychainAccount: "pool-endpoint")
    /// Where frontier answers come from when set. Anthropic speaks the OpenAI API at this address.
    var frontierEndpoint = EndpointConfig(enabled: false, label: "Anthropic", baseURL: "https://api.anthropic.com/v1",
                                          model: "claude-opus-5-5", keychainAccount: "frontier-endpoint")
    var rules = RouterRules()
    var gatewayEnabled = true
    var gatewayPort = 8787
    /// How fast simulated time runs, for demos: credits, sleeping Macs.
    var demoSpeed: Double = 1

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
