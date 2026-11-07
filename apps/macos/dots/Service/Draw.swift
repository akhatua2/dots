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
}

/// Draw: pictures from a few words, and changes to a picture, one after another. A Mac in one of your pools
/// draws each picture whole; you watch it form. Pictures stay on this Mac; a photo of yours is changed only in
/// your own pools.
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
    /// The picture being drawn, as far as it has come
    var progress: Drawing.Progress?
    /// The picture shown large when nothing is being drawn
    var shownID: UUID?
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
    var shown: DrawnPicture? { pictures.first { $0.id == shownID } }

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

    func show(_ picture: DrawnPicture) {
        guard !isDrawing else { return }
        shownID = picture.id
        phase = .idle
    }

    func delete(_ picture: DrawnPicture) {
        pictures.removeAll { $0.id == picture.id }
        if shownID == picture.id { shownID = pictures.first?.id }
        if start?.drawn == picture.id { start = nil }
        if let url = fileURL(picture) { try? FileManager.default.removeItem(at: url) }
        save()
    }

    /// Draws the prompt, or changes the start picture as it says, in a pool with a Mac that draws: your own
    /// pools first, and only those for a photo of yours.
    func draw(model: AppModel) {
        let prompt = self.prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, !isDrawing else { return }
        task?.cancel()
        let start = self.start
        let personal = start != nil && start?.drawn == nil
        let side = start == nil ? model.settings.pictureSide : nil
        let places = model.state.myPools.filter { !$0.isPublic && $0.drawsWith != nil }
            + (personal ? [] : model.state.publicPools.filter { $0.drawsWith != nil })
        phase = .drawing
        progress = Drawing.Progress()
        task = Task { [weak self] in
            guard let self else { return }
            for pool in places {
                let candidate = RouteCandidate(id: "pool:\(pool.id)", destination: pool.isPublic ? .publicPool : .yourPool,
                                               place: pool.name, model: Drawing.modelLabel, modelID: pool.drawsWith ?? "",
                                               credits: 0, listCredits: 0, tokPerSec: 0, isPrivate: !pool.isPublic,
                                               poolID: pool.id, minGB: 0)
                guard let target = AnswerRunner.target(for: candidate, model: model) else { continue }
                do {
                    var png: Data?
                    for try await event in Drawing.stream(target.endpoint, prompt: prompt, picture: start?.sent, side: side) {
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
                                                                                              reasons: [], isPrivate: !pool.isPublic),
                                                       via: target.via, paidWithKey: false)
                    self.keep(picture, prompt: prompt, changed: start?.drawn, receipt: receipt)
                    return
                } catch {
                    if self.progress?.image == nil { continue }  // nothing came back: the next pool
                    self.finish(.failed(error.localizedDescription))
                    return
                }
            }
            self.finish(.failed(personal ? "No pool of yours draws pictures right now, and this photo stays in your pools."
                                         : "No pool draws pictures right now."))
        }
    }

    func cancel() {
        task?.cancel()
        finish(.idle)
    }

    private func keep(_ png: Data, prompt: String, changed: UUID?, receipt: Receipt) {
        let file = UUID().uuidString + ".png"
        if let folder {
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try? png.write(to: folder.appendingPathComponent(file), options: .atomic)
        } else {
            unsaved[file] = png
        }
        let picture = DrawnPicture(prompt: prompt, file: file, date: .now, changed: changed, receipt: receipt)
        pictures.insert(picture, at: 0)
        save()
        self.prompt = ""
        // The next prompt changes what was just drawn, until you start over
        start = nil
        shownID = picture.id
        finish(.idle)
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
