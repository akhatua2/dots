import Foundation

/// `dots --selftest` starts the local API on a spare port against a fresh, in-memory simulation,
/// calls it the way another app would, and prints what passed. Nothing is saved.
@MainActor
enum SelfTest {
    private static var failures = 0

    static func run() async -> Bool {
        var settings = AppSettings()
        settings.gatewayPort = Int.random(in: 18_700..<18_900)
        settings.poolEndpoint.enabled = false
        settings.frontierEndpoint.enabled = false
        let model = AppModel(backend: Simulation(fresh: true, persists: false), settings: settings)
        model.persistsSettings = false
        model.restartGateway()
        let base = URL(string: "http://127.0.0.1:\(settings.gatewayPort)/v1")!
        try? await Task.sleep(for: .milliseconds(400))
        print("dots self-test on \(base.absoluteString)\n")

        // Models are listed without a key
        let (models, modelsCode) = await call(base, "GET", "/models")
        let ids = ((models?["data"] as? [[String: Any]]) ?? []).compactMap { $0["id"] as? String }
        check("GET /models lists auto, the ladder and the frontier models",
              modelsCode == 200 && ids.contains("auto") && ids.contains("claude-opus-5-5") && ids.contains("deepseek-v3.1"))

        // Answers need a key
        let (_, noKey) = await call(base, "POST", "/chat/completions", body: ["messages": [["role": "user", "content": "hi"]]])
        check("POST without a key is refused (401)", noKey == 401)

        let (all, secret) = model.backend.createKey(name: "Self-test", reach: .all, monthlyLimit: nil)
        check("A new key starts with dots_sk_live_", secret.hasPrefix("dots_sk_live_") && all.last4 == String(secret.suffix(4)))

        let (_, badBody) = await call(base, "POST", "/chat/completions", key: secret, raw: Data("{nope".utf8))
        check("A body that isn't JSON is refused (400)", badBody == 400)

        // Auto: an everyday question runs free in your own pool
        let credits0 = Int(model.state.credits)
        let (plain, plainCode) = await call(base, "POST", "/chat/completions", key: secret,
                                            body: ["model": "auto", "messages": [["role": "user", "content": "What's a good name for a cat?"]]])
        let plainDots = plain?["dots"] as? [String: Any]
        let plainText = (((plain?["choices"] as? [[String: Any]])?.first?["message"] as? [String: Any])?["content"] as? String) ?? ""
        check("auto answers an everyday question in your own pool, free",
              plainCode == 200 && !plainText.isEmpty && (plainDots?["credits"] as? Int) == 0 && (plainDots?["ran_on"] as? String)?.contains("Studio 9") == true)
        print("      ran on: \(plainDots?["ran_on"] ?? "?") · model \(plain?["model"] ?? "?")")

        // A personal question stays private
        let (lease, _) = await call(base, "POST", "/chat/completions", key: secret,
                                    body: ["messages": [["role": "user", "content": "Is my lease fair on repairs?"]]])
        let leaseDots = lease?["dots"] as? [String: Any]
        check("A personal question is marked private and stays in your pools",
              leaseDots?["private"] as? Bool == true && ((leaseDots?["why"] as? [String])?.first?.contains("personal") == true))

        // Asking for a frontier model by name spends discounted credits
        let (opus, opusCode) = await call(base, "POST", "/chat/completions", key: secret,
                                          body: ["model": "claude-opus-5-5", "messages": [["role": "user", "content": "Design a caching layer."]]])
        let opusCredits = (opus?["dots"] as? [String: Any])?["credits"] as? Int ?? -1
        check("Naming claude-opus-5-5 routes to Anthropic for 22 credits",
              opusCode == 200 && opusCredits == 22 && opus?["model"] as? String == "claude-opus-5-5")
        check("Those credits come off your balance", Int(model.state.credits) == credits0 - 22)
        check("The request shows up under the key", model.state.apiKeys.first { $0.id == all.id }?.creditsThisMonth == 22)

        // Streaming speaks server-sent events and ends with [DONE]
        let (stream, streamCode) = await raw(base, "POST", "/chat/completions", key: secret,
                                             body: ["stream": true, "messages": [["role": "user", "content": "Say hello"]]])
        let chunks = stream.components(separatedBy: "\n").filter { $0.hasPrefix("data: ") }
        check("stream: true sends chunks and ends with data: [DONE]",
              streamCode == 200 && chunks.count > 2 && chunks.last == "data: [DONE]" && !streamText(stream).isEmpty
              && stream.contains("\"finish_reason\":\"stop\""))

        // A key limited to your pools can't reach a frontier model
        let (_, poolsOnly) = model.backend.createKey(name: "Pools only", reach: .yourPools, monthlyLimit: nil)
        let (_, reachCode) = await call(base, "POST", "/chat/completions", key: poolsOnly,
                                        body: ["model": "claude-opus-5-5", "messages": [["role": "user", "content": "hi"]]])
        check("A key without frontier reach can't name Claude (400)", reachCode == 400)

        // A key's monthly limit holds
        let (_, capped) = model.backend.createKey(name: "Capped", reach: .all, monthlyLimit: 10)
        let (_, capCode) = await call(base, "POST", "/chat/completions", key: capped,
                                      body: ["model": "claude-opus-5-5", "messages": [["role": "user", "content": "hi"]]])
        check("A key over its monthly limit is refused (429)", capCode == 429)

        let (_, unknown) = await call(base, "GET", "/embeddings")
        check("Anything else is a 404", unknown == 404)

        // With an endpoint set, your pools' answers are real (DOTS_SELFTEST_ENDPOINT points at any OpenAI-compatible server)
        if let url = ProcessInfo.processInfo.environment["DOTS_SELFTEST_ENDPOINT"] {
            print("\nReal answers from \(url)")
            let endpoint = EndpointConfig(enabled: true, label: "Test", baseURL: url, model: "", keychainAccount: "dots-selftest-none")
            let ids = (try? await OpenAIClient.models(endpoint)) ?? []
            check("Test lists the endpoint's models", !ids.isEmpty)
            model.settings.poolEndpoint = EndpointConfig(enabled: true, label: "Test", baseURL: url, model: ids.first ?? "",
                                                         keychainAccount: "dots-selftest-none")
            let (real, realCode) = await call(base, "POST", "/chat/completions", key: secret,
                                              body: ["messages": [["role": "user", "content": "What's a good name for a dog?"]]])
            let realText = (((real?["choices"] as? [[String: Any]])?.first?["message"] as? [String: Any])?["content"] as? String) ?? ""
            let realDots = real?["dots"] as? [String: Any]
            check("A pool question gets the endpoint's own answer, not a simulated one",
                  realCode == 200 && realText == "Real answer from the endpoint." && realDots?["simulated"] as? Bool == false)
            print("      answer: \(realText)")
            let (stream, _) = await raw(base, "POST", "/chat/completions", key: secret,
                                        body: ["stream": true, "messages": [["role": "user", "content": "And a cat?"]]])
            check("Streaming passes the endpoint's words through", streamText(stream) == "Real answer from the endpoint.")
        }

        print(failures == 0 ? "\nAll passed." : "\n\(failures) failed.")
        return failures == 0
    }

    /// The text of a server-sent-events answer, put back together from its deltas
    private static func streamText(_ stream: String) -> String {
        stream.components(separatedBy: "\n")
            .filter { $0.hasPrefix("data: {") }
            .compactMap { line -> String? in
                guard let obj = try? JSONSerialization.jsonObject(with: Data(line.dropFirst(6).utf8)) as? [String: Any],
                      let delta = (obj["choices"] as? [[String: Any]])?.first?["delta"] as? [String: Any] else { return nil }
                return delta["content"] as? String
            }
            .joined()
    }

    private static func check(_ name: String, _ ok: Bool) {
        if !ok { failures += 1 }
        print("\(ok ? "PASS" : "FAIL")  \(name)")
    }

    private static func request(_ base: URL, _ method: String, _ path: String, key: String?, body: [String: Any]?, raw: Data?) -> URLRequest {
        var r = URLRequest(url: base.appendingPathComponent(String(path.dropFirst())), timeoutInterval: 60)
        r.httpMethod = method
        if let key { r.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization") }
        if let body { r.httpBody = try? JSONSerialization.data(withJSONObject: body) }
        if let raw { r.httpBody = raw }
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return r
    }

    private static func call(_ base: URL, _ method: String, _ path: String, key: String? = nil,
                             body: [String: Any]? = nil, raw: Data? = nil) async -> ([String: Any]?, Int) {
        do {
            let (data, response) = try await URLSession.shared.data(for: request(base, method, path, key: key, body: body, raw: raw))
            return (try? JSONSerialization.jsonObject(with: data) as? [String: Any], (response as? HTTPURLResponse)?.statusCode ?? 0)
        } catch {
            print("      \(method) \(path): \(error.localizedDescription)")
            return (nil, 0)
        }
    }

    private static func raw(_ base: URL, _ method: String, _ path: String, key: String?, body: [String: Any]) async -> (String, Int) {
        do {
            let (data, response) = try await URLSession.shared.data(for: request(base, method, path, key: key, body: body, raw: nil))
            return (String(decoding: data, as: UTF8.self), (response as? HTTPURLResponse)?.statusCode ?? 0)
        } catch {
            print("      \(method) \(path): \(error.localizedDescription)")
            return ("", 0)
        }
    }
}
