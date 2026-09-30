import Foundation
import Network

/// A small HTTP server on localhost that speaks the OpenAI API, so the keys you make in dots work
/// with curl, SDKs and any app that talks to ChatGPT-style services.
final class LocalGateway: @unchecked Sendable {
    typealias Handler = @Sendable (HTTPRequest, HTTPResponder) async -> Void

    let port: UInt16
    var onState: (@Sendable (String) -> Void)?
    private let handler: Handler
    private var listener: NWListener?
    private let queue = DispatchQueue(label: "dots.gateway")

    init(port: UInt16, handler: @escaping Handler) {
        self.port = port
        self.handler = handler
    }

    func start() throws {
        guard let nwPort = NWEndpoint.Port(rawValue: port) else { throw TransportError.badEndpoint }
        let params = NWParameters.tcp
        params.requiredInterfaceType = .loopback       // only this Mac can reach it
        params.allowLocalEndpointReuse = true
        let listener = try NWListener(using: params, on: nwPort)
        listener.stateUpdateHandler = { [weak self, port] state in
            switch state {
            case .ready: self?.onState?("Running on port \(port)")
            case .failed(let e): self?.onState?("Stopped: \(e.localizedDescription)")
            case .cancelled: self?.onState?("Off")
            default: break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
        listener.start(queue: queue)
        self.listener = listener
    }

    func stop() {
        listener?.cancel()
        listener = nil
    }

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        receive(connection, buffer: Data())
    }

    private func receive(_ connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 16) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            var buffer = buffer
            if let data { buffer.append(data) }
            if let request = HTTPRequest.parse(buffer) {
                let responder = HTTPResponder(connection)
                let handler = self.handler
                Task { await handler(request, responder) }
            } else if isComplete || error != nil || buffer.count > 4_000_000 {
                connection.cancel()
            } else {
                self.receive(connection, buffer: buffer)
            }
        }
    }
}

struct HTTPRequest: Sendable {
    var method: String
    var path: String
    var headers: [String: String]
    var body: Data

    static func parse(_ data: Data) -> HTTPRequest? {
        guard let end = data.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        let head = String(decoding: data[data.startIndex..<end.lowerBound], as: UTF8.self)
        var lines = head.components(separatedBy: "\r\n")
        guard !lines.isEmpty else { return nil }
        let first = lines.removeFirst().split(separator: " ")
        guard first.count >= 2 else { return nil }
        var headers: [String: String] = [:]
        for line in lines {
            guard let colon = line.firstIndex(of: ":") else { continue }
            headers[line[..<colon].lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        let length = Int(headers["content-length"] ?? "0") ?? 0
        let bodyStart = end.upperBound
        guard data.distance(from: bodyStart, to: data.endIndex) >= length else { return nil }
        let bodyEnd = data.index(bodyStart, offsetBy: length)
        let path = String(first[1]).components(separatedBy: "?").first ?? String(first[1])
        return HTTPRequest(method: String(first[0]), path: path, headers: headers, body: Data(data[bodyStart..<bodyEnd]))
    }

    var bearer: String? {
        guard let auth = headers["authorization"], auth.lowercased().hasPrefix("bearer ") else { return nil }
        return String(auth.dropFirst(7)).trimmingCharacters(in: .whitespaces)
    }
}

final class HTTPResponder: @unchecked Sendable {
    private let connection: NWConnection
    private let cors = "Access-Control-Allow-Origin: *\r\nAccess-Control-Allow-Headers: Authorization, Content-Type\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\n"

    init(_ connection: NWConnection) { self.connection = connection }

    func json(_ status: Int, _ object: Any) {
        let body = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
        let head = "HTTP/1.1 \(status) \(Self.reason(status))\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\n\(cors)Connection: close\r\n\r\n"
        connection.send(content: Data(head.utf8) + body, completion: .contentProcessed { [connection] _ in connection.cancel() })
    }

    func empty(_ status: Int) {
        let head = "HTTP/1.1 \(status) \(Self.reason(status))\r\nContent-Length: 0\r\n\(cors)Connection: close\r\n\r\n"
        connection.send(content: Data(head.utf8), completion: .contentProcessed { [connection] _ in connection.cancel() })
    }

    func error(_ status: Int, _ message: String, type: String = "invalid_request_error") {
        json(status, ["error": ["message": message, "type": type]])
    }

    func startStream() {
        let head = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n\(cors)Connection: close\r\n\r\n"
        connection.send(content: Data(head.utf8), completion: .idempotent)
    }

    func event(_ object: Any) {
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { return }
        connection.send(content: Data("data: ".utf8) + data + Data("\n\n".utf8), completion: .idempotent)
    }

    func finishStream() {
        connection.send(content: Data("data: [DONE]\n\n".utf8), completion: .contentProcessed { [connection] _ in connection.cancel() })
    }

    private static func reason(_ status: Int) -> String {
        switch status {
        case 200: "OK"
        case 204: "No Content"
        case 400: "Bad Request"
        case 401: "Unauthorized"
        case 402: "Payment Required"
        case 404: "Not Found"
        case 409: "Conflict"
        case 429: "Too Many Requests"
        default: "Error"
        }
    }
}

// MARK: - The API, answered by the same router the ask panel uses

extension AppModel {
    func handleGateway(_ request: HTTPRequest, _ res: HTTPResponder) async {
        if request.method == "OPTIONS" { res.empty(204); return }
        switch (request.method, request.path) {
        case ("GET", "/v1/models"), ("GET", "/models"):
            res.json(200, ["object": "list", "data": gatewayModelIDs.map { ["id": $0, "object": "model", "owned_by": "dots"] }])
        case ("POST", "/v1/chat/completions"), ("POST", "/chat/completions"):
            await chatCompletion(request, res)
        default:
            res.error(404, "dots serves /v1/models and /v1/chat/completions.")
        }
    }

    private var gatewayModelIDs: [String] {
        ["auto"] + Ladder.tiers.map(\.id) + Ladder.special.map(\.id) + FrontierModel.all.map(\.id)
    }

    private struct CompletionBody: Decodable {
        var model: String?
        var messages: [ChatMessage]
        var stream: Bool?
    }

    private func chatCompletion(_ request: HTTPRequest, _ res: HTTPResponder) async {
        guard let secret = request.bearer, let key = backend.key(forSecret: secret) else {
            res.error(401, "Pass a dots API key as a Bearer token. Make one on the API page in dots.", type: "authentication_error")
            return
        }
        guard let body = try? JSONDecoder().decode(CompletionBody.self, from: request.body), !body.messages.isEmpty else {
            res.error(400, "Send a JSON body with a messages array.")
            return
        }
        let question = body.messages.last(where: { $0.role == "user" })?.content ?? ""
        let requested = body.model ?? "auto"
        let forced = forcedRoute(for: requested, reach: key.reach)
        if requested != "auto", forced == nil {
            res.error(400, "This key can't reach \(requested), or nothing is running it right now.")
            return
        }
        let decision = Router.decide(question: question, hasDocument: false, state: state, rules: settings.rules,
                                     reach: key.reach, forcedID: forced)
        guard let chosen = decision.chosen else {
            res.error(409, decision.needsConsent ?? "Nothing this key can reach is running right now.")
            return
        }
        if let limit = key.monthlyLimit, key.creditsThisMonth + chosen.credits > limit {
            res.error(429, "This key has used \(key.creditsThisMonth) of its \(limit) credits this month.")
            return
        }
        if chosen.credits > Int(state.credits) {
            res.error(402, "This needs \(chosen.credits) credits and you have \(Int(state.credits)). Lend your Mac to earn more.")
            return
        }

        let plan = AnswerRunner.plan(for: chosen, messages: body.messages, question: question, documentName: nil,
                                     settings: settings, state: state, allowSleep: false)
        let receipt = AnswerRunner.receipt(for: chosen, decision: decision, plan: plan, state: state)
        let id = "chatcmpl-dots-" + Simulation.randomCode(12)
        let created = Int(Date().timeIntervalSince1970)
        var modelName = chosen.modelID
        let dots: [String: Any] = [
            "ran_on": receipt.macs.isEmpty ? receipt.place : "\(receipt.place) · \(receipt.macs.count) Macs",
            "why": receipt.reasons.map(\.text),
            "credits": receipt.credits,
            "private": receipt.isPrivate,
            "simulated": receipt.isSimulated,
        ]

        if body.stream == true {
            res.startStream()
            do {
                for try await event in plan.events {
                    switch event {
                    case .text(let t):
                        res.event(["id": id, "object": "chat.completion.chunk", "created": created, "model": modelName,
                                   "choices": [["index": 0, "delta": ["content": t], "finish_reason": NSNull()]]])
                    case .model(let m): modelName = m
                    default: break
                    }
                }
                res.event(["id": id, "object": "chat.completion.chunk", "created": created, "model": modelName,
                           "choices": [["index": 0, "delta": [String: String](), "finish_reason": "stop"]], "dots": dots])
            } catch {
                res.event(["error": ["message": error.localizedDescription]])
            }
            res.finishStream()
        } else {
            var text = ""
            do {
                for try await event in plan.events {
                    if case .text(let t) = event { text += t }
                    if case .model(let m) = event { modelName = m }
                }
            } catch {
                res.error(502, error.localizedDescription, type: "upstream_error")
                return
            }
            res.json(200, ["id": id, "object": "chat.completion", "created": created, "model": modelName,
                           "choices": [["index": 0, "message": ["role": "assistant", "content": text], "finish_reason": "stop"]],
                           "dots": dots])
        }
        finishAnswer(receipt: receipt, source: key.name)
        backend.noteKeyUse(id: key.id, credits: receipt.credits)
    }

    /// "auto" lets the router choose; a model id picks the place that runs it, within the key's reach.
    private func forcedRoute(for model: String, reach: Reach) -> String? {
        if model == "auto" { return nil }
        if let f = FrontierModel.all.first(where: { $0.id == model }) {
            return reach.contains(.frontier) ? "frontier:\(f.id)" : nil
        }
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
