import Foundation
import PDFKit

struct ChatMessage: Codable, Hashable {
    var role: String
    var content: String
}

/// What an answer stream carries: text, and the moments a Mac leaves or the others take over.
enum AnswerEvent {
    case text(String)
    case macAsleep(macID: String, name: String)
    case resumed
    case model(String)      // the model an endpoint says it used
    /// From the hub: the Macs answering, and whether the answer is simulated (the mock cloud)
    case macs([ReceiptMac], simulated: Bool)
    /// Tokens in and out, as the backend counted them
    case usage(input: Int, output: Int)
    /// Credits the hub or gateway charged for this answer
    case charged(Int)
    /// The answer is simulated (the mock cloud has no model behind it)
    case simulated(Bool)
}

enum TransportError: LocalizedError {
    case badEndpoint
    case http(Int, String)
    /// The endpoint started answering, then said it couldn't (a pool whose Macs aren't ready, say)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .badEndpoint: "The endpoint's address isn't a valid URL."
        case .http where needsWorkspace:
            "This key works in more than one Anthropic workspace, so dots has to say which one to use. Pick it in Settings, under the key."
        case let .http(code, body): "The endpoint answered \(code). \(Self.message(in: body) ?? String(body.prefix(240)))"
        case let .failed(message): message.prefix(1).uppercased() + message.dropFirst()
        }
    }

    /// A key made for more than one workspace, sent without saying which one to act in (Anthropic answers 400).
    var needsWorkspace: Bool {
        guard case let .http(code, body) = self, code == 400, let message = Self.message(in: body) else { return false }
        return message.contains("anthropic-workspace-id") && !message.contains("valid workspace ID")
    }

    /// A workspace ID that isn't well formed (400), or that the key can't use (404).
    var badWorkspace: Bool {
        guard case let .http(code, body) = self, let message = Self.message(in: body) else { return false }
        return (code == 400 && message.contains("valid workspace ID")) || (code == 404 && message.hasPrefix("Workspace "))
    }

    /// The readable part of an error body: `error.message` in Anthropic's and OpenAI's shapes, or a top-level `message`.
    static func message(in body: String) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any] else { return nil }
        if let error = object["error"] as? [String: Any], let message = error["message"] as? String { return message }
        if let error = object["error"] as? String { return error }
        return object["message"] as? String
    }
}

extension URLRequest {
    /// The endpoint's key and, for an Anthropic key made for more than one workspace, the workspace it acts in.
    mutating func authorize(for endpoint: EndpointConfig, inWorkspace: Bool = true) {
        if let key = endpoint.apiKey, !key.isEmpty {
            setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
            setValue(key, forHTTPHeaderField: "x-api-key")
        }
        if inWorkspace, let workspace = endpoint.workspace { setValue(workspace, forHTTPHeaderField: "anthropic-workspace-id") }
    }
}

/// Streams chat completions from any OpenAI-compatible endpoint: the dots hub, LM Studio, Ollama, or a
/// provider that speaks the same API.
enum OpenAIClient {
    private struct Body: Encodable {
        struct Options: Encodable { var include_usage = true }
        var model: String
        var messages: [ChatMessage]
        var stream = true
        var stream_options: Options? = Options()
    }

    private struct Chunk: Decodable {
        struct Choice: Decodable {
            struct Delta: Decodable { var content: String? }
            var delta: Delta?
        }
        struct Usage: Decodable { var prompt_tokens: Int?; var completion_tokens: Int? }
        struct Failure: Decodable { var message: String }
        /// What the dots hub and gateway add: the Macs answering, a Mac falling asleep, credits charged
        struct Dots: Decodable {
            struct Mac: Decodable { var id: String; var name: String; var initial: String }
            var event: String?
            var macs: [Mac]?
            var mac_id: String?
            var name: String?
            var credits: Int?
            var simulated: Bool?
        }
        var model: String?
        var choices: [Choice]?
        var usage: Usage?
        var dots: Dots?
        var error: Failure?
    }

    static func stream(_ endpoint: EndpointConfig, messages: [ChatMessage]) -> AsyncThrowingStream<AnswerEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    guard let base = endpoint.url else { throw TransportError.badEndpoint }
                    var request = URLRequest(url: base.appendingPathComponent("chat/completions"))
                    request.httpMethod = "POST"
                    request.timeoutInterval = 120
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.authorize(for: endpoint)
                    request.httpBody = try JSONEncoder().encode(Body(model: endpoint.model, messages: messages))
                    var (bytes, response) = try await URLSession.shared.bytes(for: request)
                    if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                        var body = ""
                        for try await line in bytes.lines { body += line; if body.count > 400 { break } }
                        // A server that doesn't know stream_options gets the plain request instead
                        guard http.statusCode == 400, body.contains("stream_options") else { throw TransportError.http(http.statusCode, body) }
                        request.httpBody = try JSONEncoder().encode(Body(model: endpoint.model, messages: messages, stream_options: nil))
                        (bytes, response) = try await URLSession.shared.bytes(for: request)
                        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                            var body = ""
                            for try await line in bytes.lines { body += line; if body.count > 400 { break } }
                            throw TransportError.http(http.statusCode, body)
                        }
                    }
                    var sentModel = false
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data:") else { continue }
                        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        if payload == "[DONE]" { break }
                        guard let data = payload.data(using: .utf8),
                              let chunk = try? JSONDecoder().decode(Chunk.self, from: data) else { continue }
                        if let failure = chunk.error { throw TransportError.failed(failure.message) }
                        if !sentModel, let m = chunk.model, !m.isEmpty { continuation.yield(.model(m)); sentModel = true }
                        if let text = chunk.choices?.first?.delta?.content, !text.isEmpty { continuation.yield(.text(text)) }
                        if let u = chunk.usage, let i = u.prompt_tokens, let o = u.completion_tokens { continuation.yield(.usage(input: i, output: o)) }
                        if let d = chunk.dots {
                            if let macs = d.macs {
                                continuation.yield(.macs(macs.map { ReceiptMac(id: $0.id, initial: $0.initial, name: $0.name) }, simulated: d.simulated ?? false))
                            }
                            if d.event == "asleep", let id = d.mac_id { continuation.yield(.macAsleep(macID: id, name: d.name ?? "A Mac")) }
                            if d.event == "resumed" { continuation.yield(.resumed) }
                            if let c = d.credits { continuation.yield(.charged(c)) }
                            if let sim = d.simulated { continuation.yield(.simulated(sim)) }
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Lists an endpoint's models, to test a connection from Settings.
    static func models(_ endpoint: EndpointConfig) async throws -> [String] {
        guard let base = endpoint.url else { throw TransportError.badEndpoint }
        var request = URLRequest(url: base.appendingPathComponent("models"))
        request.timeoutInterval = 10
        request.authorize(for: endpoint)
        if endpoint.apiKey?.isEmpty == false { request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version") }
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw TransportError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
        struct List: Decodable { struct M: Decodable { var id: String }; var data: [M] }
        return (try? JSONDecoder().decode(List.self, from: data).data.map(\.id)) ?? []
    }

    struct Workspace: Decodable, Hashable {
        var id: String
        var name: String?
    }

    /// The workspaces a key made for more than one workspace can act in, so Settings can offer them.
    /// Anthropic's List Workspaces takes such a key, and lists the Default Workspace only when asked to.
    static func workspaces(_ endpoint: EndpointConfig) async throws -> [Workspace] {
        guard let base = endpoint.url,
              var parts = URLComponents(url: base.appendingPathComponent("organizations/workspaces"), resolvingAgainstBaseURL: false)
        else { throw TransportError.badEndpoint }
        parts.queryItems = [URLQueryItem(name: "include_default", value: "true"), URLQueryItem(name: "limit", value: "100")]
        guard let url = parts.url else { throw TransportError.badEndpoint }
        var request = URLRequest(url: url)
        request.timeoutInterval = 10
        request.authorize(for: endpoint, inWorkspace: false)
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw TransportError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
        struct List: Decodable { var data: [Workspace] }
        return try JSONDecoder().decode(List.self, from: data).data
    }
}

/// Answers made up on this Mac when no endpoint is set for a route. They say so.
enum SimulatedAnswers {
    static let lease = """
    Three things, in order of how much they could cost you.
    1. Clause 14.2 lets the landlord raise the service charge without a cap. Ask for a cap tied to inflation.
    2. The break clause only works in his favor. Ask for a mutual one after 12 months.
    3. You pay for every repair over €200, wear and tear included. Limit it to damage you cause.
    """
    static let leaseSecond = "I agree with all three. One more: clause 9.1 lets him enter with 24 hours' notice. Ask for 48 hours and a reason in writing."

    static func answer(for question: String, documentName: String?) -> String {
        let q = question.lowercased() + " " + (documentName ?? "").lowercased()
        if q.contains("lease") || q.contains("push back") || q.contains("tenancy") { return lease }
        if q.contains("launch") {
            return "Three risks, most urgent first.\n1. Support isn't staffed for launch day. Put two people on the queue for the first week.\n2. The pricing page still says beta. Fix the copy before the announcement goes out.\n3. There's no rollback plan. Write down who can pull the release, and how."
        }
        return """
        This answer is simulated. No endpoint is set for this route yet, so dots wrote a stand-in to show the flow.

        Add an OpenAI-compatible endpoint in Settings, like your dots hub or a model running in LM Studio or Ollama, and the same question gets a real answer.
        """
    }

    /// A frontier model's answer from the mock cloud's gateway, which has no model behind it.
    static func frontier(for question: String, model: String) -> String {
        let q = question.lowercased()
        if q.contains("second opinion") { return secondOpinion(for: question, documentName: nil) }
        if q.contains("launch") { return answer(for: question, documentName: nil) }
        return "This answer is simulated by the mock cloud. Through the real dots gateway, \(model) answers here, and the same credits come off your balance."
    }

    static func secondOpinion(for question: String, documentName: String?) -> String {
        let q = question.lowercased() + " " + (documentName ?? "").lowercased()
        if q.contains("lease") || q.contains("push back") || q.contains("tenancy") { return leaseSecond }
        return "This second opinion is simulated too. Set a frontier endpoint in Settings and dots will ask the real model, paid in credits."
    }

    /// Streams text at roughly `tokPerSec`, and can let one Mac fall asleep partway through.
    static func stream(_ text: String, tokPerSec: Double, sleeper: (macID: String, name: String)? = nil) -> AsyncThrowingStream<AnswerEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                let charsPerSecond = max(20, tokPerSec * 4)
                let words = text.split(separator: " ", omittingEmptySubsequences: false).map(String.init)
                let sleepAt = sleeper == nil ? -1 : Int(Double(words.count) * 0.45)
                for (i, word) in words.enumerated() {
                    if Task.isCancelled { break }
                    if i == sleepAt, let sleeper {
                        continuation.yield(.macAsleep(macID: sleeper.macID, name: sleeper.name))
                        try? await Task.sleep(for: .milliseconds(900))
                        continuation.yield(.resumed)
                    }
                    let piece = (i == 0 ? "" : " ") + word
                    continuation.yield(.text(piece))
                    try? await Task.sleep(for: .milliseconds(Int(Double(piece.count) / charsPerSecond * 1_000)))
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

/// Text from a document you give the ask panel, sent along with the question.
enum Documents {
    static func text(from url: URL, limit: Int = 24_000) -> String? {
        if url.pathExtension.lowercased() == "pdf" {
            return PDFDocument(url: url)?.string.map { String($0.prefix(limit)) }
        }
        return (try? String(contentsOf: url, encoding: .utf8)).map { String($0.prefix(limit)) }
    }
}
