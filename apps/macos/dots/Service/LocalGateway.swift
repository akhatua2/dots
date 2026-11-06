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
    var query: [String: String] = [:]

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
        let target = String(first[1])
        let path = target.components(separatedBy: "?").first ?? target
        let query = URLComponents(string: target)?.queryItems?.reduce(into: [String: String]()) { $0[$1.name] = $1.value ?? "" } ?? [:]
        return HTTPRequest(method: String(first[0]), path: path, headers: headers, body: Data(data[bodyStart..<bodyEnd]), query: query)
    }

    var bearer: String? {
        guard let auth = headers["authorization"], auth.lowercased().hasPrefix("bearer ") else { return nil }
        return String(auth.dropFirst(7)).trimmingCharacters(in: .whitespaces)
    }
}

final class HTTPResponder: @unchecked Sendable {
    private let connection: NWConnection
    private let cors = "Access-Control-Allow-Origin: *\r\nAccess-Control-Allow-Headers: Authorization, Content-Type, x-api-key, anthropic-version, anthropic-beta\r\nAccess-Control-Allow-Methods: GET, POST, PATCH, DELETE, OPTIONS\r\n"

    init(_ connection: NWConnection) { self.connection = connection }

    func json(_ status: Int, _ object: Any, headers: [String: String] = [:]) {
        let body = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
        raw(status, contentType: "application/json", body, headers: headers)
    }

    /// A whole response, as is: for answers passed through from wherever a request ran.
    func raw(_ status: Int, contentType: String, _ body: Data, headers: [String: String] = [:]) {
        let head = "HTTP/1.1 \(status) \(Self.reason(status))\r\nContent-Type: \(contentType)\r\nContent-Length: \(body.count)\r\n\(Self.lines(headers))\(cors)Connection: close\r\n\r\n"
        connection.send(content: Data(head.utf8) + body, completion: .contentProcessed { [connection] _ in connection.cancel() })
    }

    func empty(_ status: Int) {
        let head = "HTTP/1.1 \(status) \(Self.reason(status))\r\nContent-Length: 0\r\n\(cors)Connection: close\r\n\r\n"
        connection.send(content: Data(head.utf8), completion: .contentProcessed { [connection] _ in connection.cancel() })
    }

    func error(_ status: Int, _ message: String, type: String = "invalid_request_error") {
        json(status, ["error": ["message": message, "type": type]])
    }

    func startStream(headers: [String: String] = [:]) {
        let head = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n\(Self.lines(headers))\(cors)Connection: close\r\n\r\n"
        connection.send(content: Data(head.utf8), completion: .idempotent)
    }

    func event(_ object: Any) {
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { return }
        connection.send(content: Data("data: ".utf8) + data + Data("\n\n".utf8), completion: .idempotent)
    }

    /// A named server-sent event, the way Anthropic's Messages API streams.
    func namedEvent(_ name: String, _ object: Any) {
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { return }
        connection.send(content: Data("event: \(name)\ndata: ".utf8) + data + Data("\n\n".utf8), completion: .idempotent)
    }

    /// Stream text written as is, for events passed through from wherever a request ran.
    func write(_ text: String) {
        connection.send(content: Data(text.utf8), completion: .idempotent)
    }

    func finishStream() {
        connection.send(content: Data("data: [DONE]\n\n".utf8), completion: .contentProcessed { [connection] _ in connection.cancel() })
    }

    /// Closes a stream that has already said it's done.
    func end() {
        connection.send(content: Data(), completion: .contentProcessed { [connection] _ in connection.cancel() })
    }

    private static func lines(_ headers: [String: String]) -> String {
        headers.sorted { $0.key < $1.key }.map { "\($0.key): \($0.value.replacingOccurrences(of: "\r", with: " ").replacingOccurrences(of: "\n", with: " "))\r\n" }.joined()
    }

    private static func reason(_ status: Int) -> String {
        switch status {
        case 200: "OK"
        case 204: "No Content"
        case 400: "Bad Request"
        case 401: "Unauthorized"
        case 402: "Payment Required"
        case 404: "Not Found"
        case 403: "Forbidden"
        case 409: "Conflict"
        case 502: "Bad Gateway"
        case 503: "Service Unavailable"
        case 429: "Too Many Requests"
        default: "Error"
        }
    }
}
