import Foundation

// The API dots serves on this Mac: one address and one key for every app, in OpenAI's format or Anthropic's.
// Each request goes through the same router as the ask panel, to the cheapest place that can take it, and on to
// the next place if that one doesn't answer. Every response says where it ran, why, and what it cost.

private enum RelayError: Error {
    /// The place couldn't be reached, or failed on its side, before anything was sent back
    case unreachable(String)
}

/// Where a request is sent: the endpoint, the format it speaks, and how it's paid for.
private struct Target {
    var endpoint: EndpointConfig
    var speaks: APIFormat
    var paidWithKey: Bool
    var via: String
}

/// What came back, read as it passes through.
private struct Reading {
    var text = ""
    var input: Int?
    var output: Int?
    var charged: Int?
    var simulated = false
    var macs = 0

    mutating func scan(_ o: [String: Any]) {
        // OpenAI: chunks and whole completions
        if let choice = (o["choices"] as? [[String: Any]])?.first {
            if let t = (choice["delta"] as? [String: Any])?["content"] as? String { text += t }
            if let t = (choice["message"] as? [String: Any])?["content"] as? String { text += t }
        }
        if let u = o["usage"] as? [String: Any] {
            if let i = u["prompt_tokens"] as? Int { input = i }
            if let c = u["completion_tokens"] as? Int { output = c }
            if let i = u["input_tokens"] as? Int { input = i }
            if let c = u["output_tokens"] as? Int { output = c }
        }
        // Anthropic: events and whole messages
        switch o["type"] as? String {
        case "message_start":
            if let i = ((o["message"] as? [String: Any])?["usage"] as? [String: Any])?["input_tokens"] as? Int { input = i }
        case "content_block_delta":
            if let t = (o["delta"] as? [String: Any])?["text"] as? String { text += t }
        case "message":
            for block in (o["content"] as? [[String: Any]]) ?? [] { if let t = block["text"] as? String { text += t } }
        default: break
        }
        // What the dots hub and gateway add
        if let d = o["dots"] as? [String: Any] {
            if let c = d["credits"] as? Int { charged = c }
            if let s = d["simulated"] as? Bool { simulated = s }
            if let m = d["macs"] as? [Any] { macs = m.count }
        }
    }
}

extension AppModel {
    func handleGateway(_ request: HTTPRequest, _ res: HTTPResponder) async {
        if request.method == "OPTIONS" { res.empty(204); return }
        switch (request.method, request.path) {
        case ("GET", "/v1/models"), ("GET", "/models"):
            res.json(200, ["object": "list", "has_more": false, "data": gatewayModels.map { id, name in
                ["id": id, "object": "model", "type": "model", "display_name": name, "owned_by": "dots",
                 "created": 1_767_225_600, "created_at": "2026-01-01T00:00:00Z"] as [String: Any]
            }])
        case ("POST", "/v1/chat/completions"), ("POST", "/chat/completions"):
            await serve(request, res, format: .openai)
        case ("POST", "/v1/messages"), ("POST", "/messages"):
            await serve(request, res, format: .anthropic)
        case ("POST", "/v1/messages/count_tokens"):
            guard let body = ParsedRequest.parse(request.body, format: .anthropic) else {
                res.apiError(.anthropic, 400, "Send a JSON body with a messages array."); return
            }
            res.json(200, ["input_tokens": body.inputTokens])
        default:
            res.error(404, "dots serves /v1/models, /v1/chat/completions (OpenAI's format) and /v1/messages (Anthropic's).", type: "not_found_error")
        }
    }

    private var gatewayModels: [(String, String)] {
        let auto: [(String, String)] = [("auto", "dots picks where each request runs")]
        var onThisMac: [(String, String)] = []
        if let route = thisMacRoute {
            onThisMac = [(route.modelID, "\(route.modelID) on this Mac")]
        }
        let pools: [(String, String)] = (Ladder.tiers + Ladder.special).map { ($0.id, $0.name) }
        let frontier: [(String, String)] = FrontierModel.all.map { ($0.id, $0.name) }
        return auto + onThisMac + pools + frontier
    }

    private func serve(_ request: HTTPRequest, _ res: HTTPResponder, format: APIFormat) async {
        guard let secret = request.bearer ?? request.headers["x-api-key"], let key = backend.key(forSecret: secret) else {
            res.apiError(format, 401, "Pass a dots API key, as a Bearer token or in x-api-key. Make one on the API page in dots.",
                         type: "authentication_error")
            return
        }
        guard let body = ParsedRequest.parse(request.body, format: format) else {
            res.apiError(format, 400, "Send a JSON body with a messages array."); return
        }
        // The key's rules: what it may reach, and whether everything it sends stays private
        let privateOnly = key.privateOnly == true
        let reach: Reach = privateOnly ? .yourPools : key.reach
        var rules = settings.rules
        if privateOnly { rules.privateByDefault = true }
        let forced = forcedRoute(for: body.model, reach: reach)
        if body.model != "auto", forced == nil {
            res.apiError(format, 400, "This key can't reach \(body.model), or nothing is running it right now."); return
        }
        let facts = RequestFacts(inputTokens: body.inputTokens, needsTools: body.needsTools, hasImages: body.hasImages,
                                 outputTokens: min(body.maxTokens ?? 600, 4_000))
        let decision = Router.decide(question: body.lastUserText, hasDocument: false, state: state, rules: rules, reach: reach,
                                     forcedID: forced, facts: facts, thisMac: thisMacRoute)
        guard let chosen = decision.chosen else {
            res.apiError(format, 409, decision.needsConsent ?? "Nothing this key can reach is running right now."); return
        }
        if let limit = key.monthlyLimit, key.creditsThisMonth + chosen.credits > limit {
            res.apiError(format, 429, "This key has used \(key.creditsThisMonth) of its \(limit) credits this month.", type: "rate_limit_error")
            return
        }
        var missed: [String] = []
        for candidate in [chosen] + decision.fallbacks {
            guard let target = target(for: candidate, body: body, key: key) else { continue }
            let fallback = missed.isEmpty ? nil : "\(missed.joined(separator: " and ")) didn't answer, so it went to \(candidate.place)."
            do {
                try await relay(body, headers: request.headers, to: target, candidate: candidate, decision: decision, key: key,
                                res: res, fallback: fallback)
                return
            } catch RelayError.unreachable(let why) {
                if candidate.destination == .thisMac { noteLocalDown() }
                missed.append(candidate.place)
                NSLog("dots: \(candidate.place) didn't answer: \(why)")
            } catch {
                res.apiError(format, 502, error.localizedDescription, type: "api_error"); return
            }
        }
        res.apiError(format, 503, missed.isEmpty ? "Nothing this key can reach can take this request."
                     : "\(missed.joined(separator: " and ")) didn't answer.", type: "overloaded_error")
    }

    /// Where a route really runs, and how it's paid for. Frontier models go through the gateway in credits or
    /// to your own key, as Settings says, within the key's monthly cap on your own key.
    private func target(for c: RouteCandidate, body: ParsedRequest, key: APIKey) -> Target? {
        switch c.destination {
        case .thisMac:
            let e = settings.poolEndpoint
            return e.isReady ? Target(endpoint: e, speaks: .openai, paidWithKey: false,
                                      via: e.serverName.map { "\($0) on this Mac" } ?? "the model on this Mac") : nil
        case .yourPool, .publicPool:
            guard let pool = c.poolID else { return nil }
            return Target(endpoint: backend.client.hub(poolID: pool, model: c.modelID), speaks: .openai, paidWithKey: false, via: "the dots hub")
        case .frontier:
            let gateway = Target(endpoint: backend.client.gateway(model: c.modelID), speaks: body.format, paidWithKey: false,
                                 via: "the dots gateway, in credits")
            var own = settings.frontierEndpoint
            if own.isAnthropic { own.model = c.modelID }
            let speaks: APIFormat = own.isAnthropic && body.format == .anthropic ? .anthropic : .openai
            let capLeft = key.monthlyCapUSD.map { $0 - (key.usdThisMonth ?? 0) } ?? .infinity
            // An Anthropic-format request with tools needs Anthropic itself; any other provider would get only its text
            let ownOK = own.isReady && capLeft > 0 && !(body.format == .anthropic && speaks == .openai && (body.needsTools || body.hasImages))
            let mine = Target(endpoint: own, speaks: speaks, paidWithKey: true, via: "your \(own.placeName) key")
            switch settings.payFrontierWith {
            case .credits: return Int(state.credits) >= c.credits || !ownOK ? gateway : mine
            case .key: return ownOK ? mine : gateway
            }
        }
    }

    private func relay(_ body: ParsedRequest, headers clientHeaders: [String: String], to t: Target, candidate c: RouteCandidate,
                       decision: RouteDecision, key: APIKey, res: HTTPResponder, fallback: String?) async throws {
        guard let base = t.endpoint.url else { throw RelayError.unreachable("it has no address") }
        let model = t.endpoint.model
        var request = URLRequest(url: base.appendingPathComponent(t.speaks == .anthropic ? "messages" : "chat/completions"), timeoutInterval: 600)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.authorize(for: t.endpoint)
        if t.speaks == .anthropic {
            request.setValue(clientHeaders["anthropic-version"] ?? "2023-06-01", forHTTPHeaderField: "anthropic-version")
            if let beta = clientHeaders["anthropic-beta"] { request.setValue(beta, forHTTPHeaderField: "anthropic-beta") }
        }

        // Open it. A server that doesn't know stream_options gets the plain request instead.
        var bytes: URLSession.AsyncBytes
        var response: URLResponse
        var status = 0
        var errorText = ""
        for usage in [true, false] {
            request.httpBody = body.body(for: t.speaks, model: model, usage: usage)
            do { (bytes, response) = try await URLSession.shared.bytes(for: request) } catch {
                throw RelayError.unreachable(error.localizedDescription)
            }
            status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if status == 0 || status >= 500 { throw RelayError.unreachable("it answered \(status)") }
            if (200..<300).contains(status) { return try await pass(bytes, response, body, t, c, decision, key, res, fallback) }
            errorText = ""
            for try await line in bytes.lines { errorText += line + "\n"; if errorText.count > 8_000 { break } }
            if !(status == 400 && errorText.contains("stream_options")) { break }
        }
        // The place said no: pass its reason on, in the client's format
        let reason = TransportError.message(in: errorText) ?? "It answered \(status)."
        res.apiError(body.format, status, "\(c.place) said: \(reason)",
                     type: status == 401 || status == 403 ? "authentication_error" : status == 402 ? "insufficient_credits" : "invalid_request_error")
    }

    /// Streams or returns the answer in the client's format, reading the tokens, credits and Macs as it goes,
    /// then books it.
    private func pass(_ bytes: URLSession.AsyncBytes, _ response: URLResponse, _ body: ParsedRequest, _ t: Target, _ c: RouteCandidate,
                      _ decision: RouteDecision, _ key: APIKey, _ res: HTTPResponder, _ fallback: String?) async throws {
        var r = Reading()
        if let header = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "x-dots-credits") { r.charged = Int(header) }
        let isPrivate = c.destination == .thisMac || (decision.isPrivate && c.isPrivate)
        var headers = ["x-dots-route": c.place, "x-dots-private": isPrivate ? "true" : "false"]
        if let fallback { headers["x-dots-fallback"] = fallback }
        func object(_ s: String) -> [String: Any]? { (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] }

        // Books it, and says what it was, for the response
        func book() -> [String: Any] {
            let input = r.input ?? body.inputTokens
            let output = r.output ?? Pricing.tokens(r.text)
            let usd = t.paidWithKey ? Pricing.usd(t.endpoint.model, input: input, output: output) : 0
            let credits = t.paidWithKey ? 0 : (r.charged ?? 0)
            let baseline = Pricing.usd(settings.savingsBaseline, input: input, output: output)
            let saved = max(0, baseline - usd - Double(credits) * Pricing.terms.creditUSD)
            recordAnswer(destination: c.destination, place: c.place, model: c.model, source: key.name, credits: credits, usd: usd,
                         isPrivate: isPrivate, input: input, output: output, measured: r.input != nil && r.output != nil,
                         fallback: fallback, format: body.format)
            backend.noteKeyUse(id: key.id, credits: credits, usd: usd)
            var d: [String: Any] = [
                "ran_on": r.macs > 0 ? "\(c.place) · \(r.macs) Macs" : c.destination == .thisMac ? "This Mac" : c.place,
                "model": c.model, "why": decision.reasons.map(\.text) + ["Answered through \(t.via)."],
                "credits": credits, "usd": usd, "saved_usd": saved, "compared_with": settings.savingsBaseline,
                "private": isPrivate, "simulated": r.simulated, "input_tokens": input, "output_tokens": output,
            ]
            if let fallback { d["fallback"] = fallback }
            return d
        }

        if body.stream {
            if body.format == t.speaks {
                // The same format: pass every event through as it comes
                res.startStream(headers: headers)
                var event: String?
                for try await line in bytes.lines {
                    if line.hasPrefix("event:") { event = line; continue }
                    guard line.hasPrefix("data:") else { continue }
                    let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                    if payload == "[DONE]" { break }
                    if let o = object(payload) { r.scan(o) }
                    res.write((event.map { $0 + "\n" } ?? "") + "data: " + payload + "\n\n")
                    event = nil
                }
                let dots = book()
                if body.format == .openai {
                    res.event(["id": "chatcmpl-dots-receipt", "object": "chat.completion.chunk", "created": Int(Date().timeIntervalSince1970),
                               "model": t.endpoint.model, "choices": [Any](), "dots": dots])
                    res.finishStream()
                } else {
                    // Anthropic's SDKs skip event names they don't know, so the receipt can follow message_stop
                    res.namedEvent("dots", ["type": "dots", "dots": dots])
                    res.end()
                }
            } else {
                // An Anthropic-format client, answered by an OpenAI-style place: write its text as Messages events
                let out = AnthropicStream(res: res, model: c.modelID)
                out.start(inputTokens: body.inputTokens, headers: headers)
                for try await line in bytes.lines {
                    guard line.hasPrefix("data:") else { continue }
                    let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                    if payload == "[DONE]" { break }
                    guard let o = object(payload) else { continue }
                    let before = r.text.count
                    r.scan(o)
                    if r.text.count > before { out.text(String(r.text.dropFirst(before))) }
                }
                let dots = book()
                out.finish(outputTokens: dots["output_tokens"] as? Int ?? 0, receipt: dots)
            }
        } else {
            var whole = ""
            for try await line in bytes.lines { whole += line + "\n" }
            guard var o = object(whole) else { throw RelayError.unreachable("its answer wasn't JSON") }
            r.scan(o)
            let dots = book()
            if body.format == t.speaks {
                o["dots"] = dots
                res.json(200, o, headers: headers)
            } else {
                res.json(200, AnthropicStream.message(model: c.modelID, text: r.text, input: dots["input_tokens"] as? Int ?? 0,
                                                      output: dots["output_tokens"] as? Int ?? 0, extra: ["dots": dots]), headers: headers)
            }
        }
    }

    /// "auto" lets the router choose; a model id picks the place that runs it, within the key's reach.
    private func forcedRoute(for model: String, reach: Reach) -> String? {
        if model == "auto" { return nil }
        if let mac = thisMacRoute, model == mac.modelID || model == "local", reach.contains(.yourPools) { return mac.id }
        if let f = FrontierModel.named(model) {
            return reach.contains(.frontier) ? "frontier:\(f.id)" : nil
        }
        // Anthropic SDK defaults and other Claude names go to the nearest frontier model
        if model.hasPrefix("claude") { return reach.contains(.frontier) ? "frontier:\(FrontierModel.opus.id)" : nil }
        let pools = state.pools.filter { $0.model?.id == model && $0.lendingMacs > 0 }
        if reach.contains(.yourPools), let own = pools.first(where: { !$0.isPublic && $0.isMember(state.meID) }) {
            return "pool:\(own.id)"
        }
        if reach.contains(.publicPools), let pub = pools.filter(\.isPublic).max(by: { $0.tokPerSec < $1.tokPerSec }) {
            return "pool:\(pub.id)"
        }
        return nil
    }
}
