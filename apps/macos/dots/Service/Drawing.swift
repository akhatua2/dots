import Foundation

/// Asking a pool for a picture: what counts as asking for one, and the picture as it forms.
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

    /// The sizes a picture can be drawn at, as the longer side in pixels. Bigger is sharper and slower.
    static let sides = [512, 1024, 1536]

    private static let drawWords = ["draw", "paint", "sketch", "illustrate", "generate an image", "generate a picture",
                                    "generate a photo", "make an image", "make a picture", "make a photo", "create an image",
                                    "create a picture", "create a photo", "an image of", "a picture of", "a photo of"]
    private static let editStarts = ["make it", "make the", "make him", "make her", "make them", "remove", "erase", "add",
                                     "change", "replace", "turn", "put", "swap", "recolor", "recolour", "brighten",
                                     "darken", "fix", "edit", "give", "blur", "restore", "colorize", "colourize", "clean up"]
    private static let questionStarts = ["what", "who", "where", "why", "how", "when", "which", "is ", "are ", "can ",
                                         "could ", "does ", "do ", "describe", "explain", "tell", "read", "summarize"]
    /// Ways of asking politely that still ask for the thing ("can you draw…")
    private static let politeStarts = ["please ", "can you ", "could you ", "would you ", "will you "]

    /// Whether the question asks for a picture: to draw one, or, with a picture attached, to change it.
    /// A question about the picture ("what's in this?") is answered in words.
    static func wantsPicture(_ question: String, hasPicture: Bool) -> Bool {
        var q = question.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var polite = false
        while let start = politeStarts.first(where: { q.hasPrefix($0) }) {
            q = String(q.dropFirst(start.count)).trimmingCharacters(in: .whitespaces)
            polite = true
        }
        if polite { q = q.trimmingCharacters(in: CharacterSet(charactersIn: "?")) }
        guard !q.isEmpty, !q.hasSuffix("?"), !questionStarts.contains(where: { q.hasPrefix($0) }) else { return false }
        if drawWords.contains(where: { q.hasPrefix($0) || q.contains(" " + $0 + " ") }) { return true }
        return hasPicture && editStarts.contains { q.hasPrefix($0) }
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
