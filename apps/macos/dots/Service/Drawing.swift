import Foundation

/// Asking a pool for a picture, and the picture as it forms.
///
/// A picture is drawn whole on one Mac in the pool that offers an image model. It takes a while (a minute or
/// more on a laptop), so the pool sends the picture after every step of the drawing and Ask shows it sharpen.
enum Drawing {
    enum Event {
        /// The picture so far, a small JPEG, after `step` of `of`
        case step(Int, of: Int, preview: Data)
        /// The finished picture, as PNG
        case done(Data)
    }

    /// What a picture's receipt calls the model that drew it. Which image model a pool runs is its own business.
    static let modelLabel = "Image model"

    /// The sizes a picture can be drawn at, as the longer side in pixels. Bigger is sharper and slower.
    static let sides = [512, 1024, 1536]

    /// How far a picture's drawing has come: the picture so far, the step, and when the steps came
    struct Progress: Equatable {
        var image: Data?
        var step = 0
        var of = 0
        var done = false
        /// Which step came first and when, and when the latest came, to tell how long the rest will take
        var firstStep: (step: Int, at: Date)?
        var latestAt: Date?

        static func == (a: Self, b: Self) -> Bool {
            a.image == b.image && a.step == b.step && a.of == b.of && a.done == b.done && a.latestAt == b.latestAt
        }

        /// Seconds left at `now`: the steps still to come at the pace of those so far, less the time since the
        /// latest. Nil until two steps have come.
        func secondsLeft(at now: Date) -> Double? {
            guard !done, let first = firstStep, let latest = latestAt, step > first.step, of > step else { return nil }
            let perStep = latest.timeIntervalSince(first.at) / Double(step - first.step)
            return max(1, perStep * Double(of - step) - now.timeIntervalSince(latest))
        }
    }


    /// Draws `prompt`, or changes `picture` as it says, at `side` pixels (nil: the picture's own size).
    static func stream(_ endpoint: EndpointConfig, prompt: String, picture: Data?, side: Int?) -> AsyncThrowingStream<Event, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    guard let base = endpoint.url else { throw TransportError.badEndpoint }
                    let kind = picture == nil ? "images/generations" : "images/edits"
                    var request = URLRequest(url: base.appendingPathComponent(kind))
                    request.httpMethod = "POST"
                    request.timeoutInterval = 1800
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.authorize(for: endpoint)
                    var body: [String: Any] = ["prompt": prompt, "stream": true]
                    if let side { body["size"] = "\(side)x\(side)" }
                    if let picture { body["image"] = "data:image/jpeg;base64," + picture.base64EncodedString() }
                    request.httpBody = try JSONSerialization.data(withJSONObject: body)
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                        var text = ""
                        for try await line in bytes.lines { text += line; if text.count > 400 { break } }
                        throw TransportError.http(http.statusCode, text)
                    }
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data: "), let data = line.dropFirst(6).data(using: .utf8),
                              let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                              let type = event["type"] as? String else { continue }
                        if type == "error" {
                            let message = (event["error"] as? [String: Any])?["message"] as? String
                            throw TransportError.failed(message ?? "The pool couldn't draw it.")
                        }
                        guard let b64 = event["b64_json"] as? String, let image = Data(base64Encoded: b64) else { continue }
                        if type.hasSuffix(".completed") {
                            continuation.yield(.done(image))
                        } else if let dots = event["dots"] as? [String: Any], let step = dots["step"] as? Int, let of = dots["of"] as? Int {
                            continuation.yield(.step(step, of: of, preview: image))
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
}
