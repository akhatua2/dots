import Foundation

/// The two request formats dots speaks: OpenAI's chat completions and Anthropic's Messages API.
enum APIFormat: String, Codable {
    case openai, anthropic
}

/// A request in either format, read for routing: its text, its size, and what it needs.
struct ParsedRequest {
    var format: APIFormat
    var raw: [String: Any]
    var model: String
    var stream: Bool
    var maxTokens: Int?
    /// The conversation as plain text, system first: enough to answer it anywhere
    var messages: [ChatMessage]
    var needsTools: Bool
    var hasImages: Bool

    var lastUserText: String { messages.last(where: { $0.role == "user" })?.content ?? "" }
    var inputTokens: Int { messages.reduce(0) { $0 + Pricing.tokens($1.content) + 4 } }

    static func parse(_ data: Data, format: APIFormat) -> ParsedRequest? {
        guard let raw = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let list = raw["messages"] as? [[String: Any]], !list.isEmpty else { return nil }
        var tools = (raw["tools"] as? [Any])?.isEmpty == false || raw["functions"] != nil
        var images = false
        func text(_ content: Any?) -> String {
            if let s = content as? String { return s }
            guard let blocks = content as? [[String: Any]] else { return "" }
            return blocks.compactMap { block -> String? in
                switch block["type"] as? String {
                case "text", "input_text": return block["text"] as? String
                case "image", "image_url", "input_image", "document", "file": images = true
                case "tool_use", "tool_result", "server_tool_use": tools = true
                default: break
                }
                return nil
            }.joined(separator: "\n")
        }
        var messages: [ChatMessage] = []
        if format == .anthropic, let system = raw["system"] {
            let s = text(system)
            if !s.isEmpty { messages.append(ChatMessage(role: "system", content: s)) }
        }
        for m in list {
            let role = m["role"] as? String ?? "user"
            if role == "tool" || role == "function" || m["tool_calls"] != nil { tools = true }
            let t = text(m["content"])
            if !t.isEmpty { messages.append(ChatMessage(role: role == "developer" ? "system" : role, content: t)) }
        }
        let maxTokens = (raw["max_tokens"] as? Int) ?? (raw["max_completion_tokens"] as? Int)
        return ParsedRequest(format: format, raw: raw, model: raw["model"] as? String ?? "auto", stream: raw["stream"] as? Bool ?? false,
                             maxTokens: maxTokens, messages: messages, needsTools: tools, hasImages: images)
    }

    /// The body to send to a backend that speaks `upstream`, with its model set. A request in the same format
    /// passes through whole (tools, images and all); an Anthropic request sent to an OpenAI-style backend
    /// goes as its text, which is all the router lets through that way.
    func body(for upstream: APIFormat, model: String, usage: Bool = true) -> Data {
        var r: [String: Any]
        if upstream == format {
            r = raw
            r["model"] = model
        } else {
            r = ["model": model, "messages": messages.map { ["role": $0.role, "content": $0.content] }, "stream": stream]
            if let maxTokens { r["max_tokens"] = maxTokens }
            if let t = raw["temperature"] { r["temperature"] = t }
        }
        if upstream == .openai, stream, usage { r["stream_options"] = ["include_usage": true] }
        return (try? JSONSerialization.data(withJSONObject: r)) ?? Data()
    }
}

/// Writes an answer to an Anthropic-format client as Messages API events, whatever produced it.
struct AnthropicStream {
    let res: HTTPResponder
    let id = "msg_dots_" + Simulation.randomCode(16)
    var model: String

    func start(inputTokens: Int, headers: [String: String] = [:]) {
        res.startStream(headers: headers)
        res.namedEvent("message_start", ["type": "message_start", "message": [
            "id": id, "type": "message", "role": "assistant", "model": model, "content": [Any](),
            "stop_reason": NSNull(), "stop_sequence": NSNull(), "usage": ["input_tokens": inputTokens, "output_tokens": 0],
        ] as [String: Any]])
        res.namedEvent("content_block_start", ["type": "content_block_start", "index": 0, "content_block": ["type": "text", "text": ""]])
    }

    func text(_ t: String) {
        res.namedEvent("content_block_delta", ["type": "content_block_delta", "index": 0, "delta": ["type": "text_delta", "text": t]])
    }

    /// `receipt`: dots's own account of the answer, sent last as a `dots` event, which Anthropic's SDKs skip.
    func finish(outputTokens: Int, stopReason: String = "end_turn", receipt: [String: Any]? = nil) {
        res.namedEvent("content_block_stop", ["type": "content_block_stop", "index": 0])
        res.namedEvent("message_delta", ["type": "message_delta", "delta": ["stop_reason": stopReason, "stop_sequence": NSNull()],
                                         "usage": ["output_tokens": outputTokens]])
        res.namedEvent("message_stop", ["type": "message_stop"])
        if let receipt { res.namedEvent("dots", ["type": "dots", "dots": receipt]) }
        res.end()
    }

    /// A whole answer, for a client that didn't ask to stream.
    static func message(model: String, text: String, input: Int, output: Int, extra: [String: Any] = [:]) -> [String: Any] {
        var m: [String: Any] = [
            "id": "msg_dots_" + Simulation.randomCode(16), "type": "message", "role": "assistant", "model": model,
            "content": [["type": "text", "text": text]], "stop_reason": "end_turn", "stop_sequence": NSNull(),
            "usage": ["input_tokens": input, "output_tokens": output],
        ]
        for (k, v) in extra { m[k] = v }
        return m
    }
}

/// OpenAI-format pieces for answers dots writes itself.
enum OpenAIChunks {
    static func chunk(id: String, model: String, text: String? = nil, finish: String? = nil, extra: [String: Any] = [:]) -> [String: Any] {
        var delta: [String: Any] = [:]
        if let text { delta["content"] = text }
        var c: [String: Any] = ["id": id, "object": "chat.completion.chunk", "created": Int(Date().timeIntervalSince1970), "model": model,
                                "choices": [["index": 0, "delta": delta, "finish_reason": finish.map { $0 as Any } ?? NSNull()]]]
        for (k, v) in extra { c[k] = v }
        return c
    }

    static func completion(model: String, text: String, input: Int, output: Int, extra: [String: Any] = [:]) -> [String: Any] {
        var c: [String: Any] = [
            "id": "chatcmpl-dots-" + Simulation.randomCode(12), "object": "chat.completion", "created": Int(Date().timeIntervalSince1970),
            "model": model, "choices": [["index": 0, "message": ["role": "assistant", "content": text], "finish_reason": "stop"]],
            "usage": ["prompt_tokens": input, "completion_tokens": output, "total_tokens": input + output],
        ]
        for (k, v) in extra { c[k] = v }
        return c
    }
}

extension HTTPResponder {
    /// An error in the shape the client's format expects.
    func apiError(_ format: APIFormat, _ status: Int, _ message: String, type: String = "invalid_request_error") {
        switch format {
        case .openai: error(status, message, type: type)
        case .anthropic: json(status, ["type": "error", "error": ["type": type, "message": message]])
        }
    }
}
