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
}

enum TransportError: LocalizedError {
    case badEndpoint
    case http(Int, String)

    var errorDescription: String? {
        switch self {
        case .badEndpoint: "The endpoint's address isn't a valid URL."
        case let .http(code, body): "The endpoint answered \(code). \(body.prefix(240))"
        }
    }
}

/// Streams chat completions from any OpenAI-compatible endpoint: the dots hub, LM Studio, Ollama, or a
/// provider that speaks the same API.
enum OpenAIClient {
    private struct Body: Encodable {
        var model: String
        var messages: [ChatMessage]
        var stream = true
    }

    private struct Chunk: Decodable {
        struct Choice: Decodable {
            struct Delta: Decodable { var content: String? }
            var delta: Delta?
        }
        var model: String?
        var choices: [Choice]
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
                    if let key = endpoint.apiKey, !key.isEmpty {
                        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
                        request.setValue(key, forHTTPHeaderField: "x-api-key")
                    }
                    request.httpBody = try JSONEncoder().encode(Body(model: endpoint.model, messages: messages))
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                        var body = ""
                        for try await line in bytes.lines { body += line; if body.count > 400 { break } }
                        throw TransportError.http(http.statusCode, body)
                    }
                    var sentModel = false
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data:") else { continue }
                        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        if payload == "[DONE]" { break }
                        guard let data = payload.data(using: .utf8),
                              let chunk = try? JSONDecoder().decode(Chunk.self, from: data) else { continue }
                        if !sentModel, let m = chunk.model, !m.isEmpty { continuation.yield(.model(m)); sentModel = true }
                        if let text = chunk.choices.first?.delta?.content, !text.isEmpty { continuation.yield(.text(text)) }
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
        if let key = endpoint.apiKey, !key.isEmpty {
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
            request.setValue(key, forHTTPHeaderField: "x-api-key")
            request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw TransportError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
        struct List: Decodable { struct M: Decodable { var id: String }; var data: [M] }
        return (try? JSONDecoder().decode(List.self, from: data).data.map(\.id)) ?? []
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
