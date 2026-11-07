import Foundation
import Observation
import UniformTypeIdentifiers

/// A picture drawn in Draw, kept on this Mac with the words that made it.
struct DrawnPicture: Codable, Equatable, Identifiable {
    var id = UUID()
    var prompt: String
    /// Its file, among the pictures in Application Support/dots/pictures
    var file: String
    var date: Date
    /// The picture this one changed, when it was a change
    var changed: UUID?
    var receipt: Receipt?
    /// The drawing it belongs to: a conversation of a picture and the changes to it
    var thread: UUID?
    /// The photo of yours it changed, by name, when it started from one
    var photo: String?
    /// What the image model was asked to draw, when the pool's chat model put the words into a description
    var description: String?
}

/// A drawing in the sidebar: its first words, and when it last changed
struct DrawingThread: Identifiable, Equatable {
    var id: UUID
    var title: String
    var updated: Date
}

/// Draw: a conversation in pictures. You describe a picture; then each thing you say changes the last one,
/// until you start over. A Mac in one of your pools draws each picture whole; you watch it form. Pictures stay
/// on this Mac; a photo of yours is changed only in your own pools.
@MainActor
@Observable
final class DrawController {
    enum Phase: Equatable { case idle, drawing, failed(String) }

    /// What the next picture changes: a photo from this Mac, or a picture drawn here
    struct Start: Equatable {
        var name: String
        /// The picture as it's sent: shrunk to what a pool reads
        var sent: Data
        /// The full-size picture on this Mac, which the change is put back into
        var original: URL
        /// Set when it's a picture drawn here rather than a photo of yours
        var drawn: UUID?
    }

    var prompt = ""
    var start: Start?
    var phase: Phase = .idle
    /// The picture being drawn, as far as it has come, and what was asked for it
    var progress: Drawing.Progress?
    private(set) var asked = ""
    private(set) var askedPhoto: String?
    /// The drawing open now; nil starts a new one with the next picture
    private(set) var threadID: UUID?
    private(set) var pictures: [DrawnPicture] = []

    @ObservationIgnored private let folder: URL?
    @ObservationIgnored private var unsaved: [String: Data] = [:]
    @ObservationIgnored private var task: Task<Void, Never>?

    init(persists: Bool) {
        folder = persists ? Simulation.supportURL("pictures") : nil
        if let folder, let data = try? Data(contentsOf: folder.appendingPathComponent("pictures.json")),
           let saved = try? JSONDecoder.dots.decode([DrawnPicture].self, from: data) {
            pictures = saved
        }
    }

    var isDrawing: Bool { phase == .drawing }

    /// The open drawing's pictures, oldest first
    var thread: [DrawnPicture] {
        guard let threadID else { return [] }
        return pictures.filter { ($0.thread ?? $0.id) == threadID }.reversed()
    }

    /// Your drawings, most recently changed first
    var threads: [DrawingThread] {
        var seen: [UUID: DrawingThread] = [:]
        var order: [UUID] = []
        for p in pictures {  // newest first
            let id = p.thread ?? p.id
            if seen[id] == nil { order.append(id); seen[id] = DrawingThread(id: id, title: p.prompt, updated: p.date) }
            else { seen[id]?.title = p.prompt }  // ends on the oldest: the words that started it
        }
        return order.compactMap { seen[$0] }
    }

    func open(_ thread: UUID) {
        guard !isDrawing else { return }
        threadID = thread
        phase = .idle
        if let last = self.thread.last { change(last, maxPixels: maxPixels) } else { start = nil }
    }

    func startNew() {
        guard !isDrawing else { return }
        threadID = nil
        start = nil
        phase = .idle
    }

    /// How large a picture is sent to be changed; set from the app's settings
    @ObservationIgnored var maxPixels = 1_048_576

    func image(_ picture: DrawnPicture) -> Data? {
        if let png = unsaved[picture.file] { return png }
        guard let folder else { return nil }
        return try? Data(contentsOf: folder.appendingPathComponent(picture.file))
    }

    /// A photo from this Mac to change
    func attach(_ url: URL, maxPixels: Int) {
        guard Pictures.isPicture(url), let sent = Pictures.jpeg(from: url, maxPixels: maxPixels) else { return }
        start = Start(name: url.lastPathComponent, sent: sent, original: url, drawn: nil)
    }

    /// The next prompt changes this picture
    func change(_ picture: DrawnPicture, maxPixels: Int) {
        guard let url = fileURL(picture) ?? temporaryFile(picture), let sent = Pictures.jpeg(from: url, maxPixels: maxPixels) else { return }
        start = Start(name: "This picture", sent: sent, original: url, drawn: picture.id)
    }

    /// Draws `picture` again from the same words, with a new seed: for when it came out wrong. A change is made
    /// again to the same picture. A change to a photo of yours can't be: the photo was never kept.
    func drawAgain(_ picture: DrawnPicture, model: AppModel) {
        guard !isDrawing, picture.photo == nil else { return }
        if let changed = picture.changed, let original = pictures.first(where: { $0.id == changed }) {
            change(original, maxPixels: maxPixels)
        } else {
            start = nil
        }
        prompt = picture.prompt
        draw(model: model)
    }

    /// Deletes a whole drawing and its pictures from this Mac
    func delete(thread: UUID) {
        let gone = pictures.filter { ($0.thread ?? $0.id) == thread }
        pictures.removeAll { ($0.thread ?? $0.id) == thread }
        for picture in gone { if let url = fileURL(picture) { try? FileManager.default.removeItem(at: url) } }
        if threadID == thread { startNew() }
        save()
    }

    /// Draws the prompt, or changes the start picture as it says, in a pool with a Mac that draws: your own
    /// pools first, and only those for a photo of yours.
    func draw(model: AppModel) {
        let prompt = self.prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, !isDrawing else { return }
        task?.cancel()
        let start = self.start
        asked = prompt
        askedPhoto = start?.drawn == nil ? start?.name : nil
        self.prompt = ""
        // A photo of yours is changed only on your own Macs
        let personal = start != nil && start?.drawn == nil
        let level: PrivacyLevel = personal ? .mine : .anywhere
        let side = start == nil ? model.settings.pictureSide : nil
        // A photo of yours goes only to pools your own Mac draws in, public ones too: the hub draws it on your Mac
        // or not at all (Only my Macs)
        let s = model.state
        let places = personal
            ? s.pools.filter { $0.drawsWith != nil && s.isLending(to: $0.id) }
            : s.myPools.filter { !$0.isPublic && $0.drawsWith != nil } + s.publicPools.filter { $0.drawsWith != nil }
        phase = .drawing
        progress = Drawing.Progress()
        task = Task { [weak self] in
            guard let self else { return }
            for pool in places {
                let candidate = RouteCandidate(id: "pool:\(pool.id)", destination: pool.isPublic ? .publicPool : .yourPool,
                                               place: pool.name, model: Drawing.modelLabel, modelID: pool.drawsWith ?? "",
                                               credits: 0, listCredits: 0, tokPerSec: 0, isPrivate: !pool.isPublic,
                                               poolID: pool.id, minGB: 0)
                guard let target = AnswerRunner.target(for: candidate, model: model, level: level) else { continue }
                // A new picture is drawn from a clear description of it; a change goes as said
                var description: String?
                if start == nil, let chatModel = pool.model?.id {
                    var chat = candidate
                    chat.modelID = chatModel
                    if let chatTarget = AnswerRunner.target(for: chat, model: model) {
                        description = await Drawing.describe(prompt, endpoint: chatTarget.endpoint)
                    }
                    if Task.isCancelled { return }
                }
                do {
                    var png: Data?
                    for try await event in Drawing.stream(target.endpoint, prompt: description ?? prompt, picture: start?.sent, side: side) {
                        if Task.isCancelled { return }
                        switch event {
                        case let .step(step, of, preview):
                            let now = Date()
                            self.progress = Drawing.Progress(image: preview, step: step, of: of,
                                                             firstStep: self.progress?.firstStep ?? (step, now), latestAt: now)
                        case .done(let drawn):
                            png = drawn
                        }
                    }
                    guard var picture = png else { continue }
                    // A change goes back into the picture it changed, at its size, wherever it changed it
                    if let start {
                        let edited = picture
                        picture = await Task.detached { Compositing.putBack(edited: edited, sent: start.sent, into: start.original) }.value ?? picture
                    }
                    let receipt = AnswerRunner.receipt(for: candidate, decision: RouteDecision(chosen: candidate, candidates: [candidate],
                                                                                              reasons: [], isPrivate: !pool.isPublic,
                                                                                              level: personal ? .mine : .myPools),
                                                       via: target.via, paidWithKey: false)
                    self.keep(picture, prompt: prompt, changed: start?.drawn, photo: start?.drawn == nil ? start?.name : nil,
                              description: description, receipt: receipt)
                    return
                } catch {
                    if self.progress?.image == nil { continue }  // nothing came back: the next pool
                    self.restoreAsked()
                    self.finish(.failed(error.localizedDescription))
                    return
                }
            }
            self.restoreAsked()
            self.finish(.failed(personal ? "Your Mac doesn't lend to a pool that draws, and this photo stays on your own Macs. Lend to one to change it here."
                                         : "No pool draws pictures right now."))
        }
    }

    func cancel() {
        task?.cancel()
        finish(.idle)
    }

    /// A drawing that failed: its words go back in the box to try again
    private func restoreAsked() {
        if prompt.isEmpty { prompt = asked }
    }

    private func keep(_ png: Data, prompt: String, changed: UUID?, photo: String?, description: String?, receipt: Receipt) {
        let file = UUID().uuidString + ".png"
        if let folder {
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try? png.write(to: folder.appendingPathComponent(file), options: .atomic)
        } else {
            unsaved[file] = png
        }
        let thread = threadID ?? UUID()
        threadID = thread
        let picture = DrawnPicture(prompt: prompt, file: file, date: .now, changed: changed, receipt: receipt,
                                   thread: thread, photo: photo, description: description)
        pictures.insert(picture, at: 0)
        save()
        finish(.idle)
        // What you say next changes what was just drawn, until you take it off or start over
        change(picture, maxPixels: maxPixels)
    }

    private func finish(_ phase: Phase) {
        self.phase = phase
        progress = nil
    }

    private func fileURL(_ picture: DrawnPicture) -> URL? {
        guard let folder else { return nil }
        let url = folder.appendingPathComponent(picture.file)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    /// For a store that isn't saved (the self-test's): the picture written where the photo tools can read it
    private func temporaryFile(_ picture: DrawnPicture) -> URL? {
        guard let png = unsaved[picture.file] else { return nil }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(picture.file)
        return (try? png.write(to: url)) != nil ? url : nil
    }

    private func save() {
        guard let folder else { return }
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        if let data = try? JSONEncoder.dots.encode(pictures) {
            try? data.write(to: folder.appendingPathComponent("pictures.json"), options: .atomic)
        }
    }
}
