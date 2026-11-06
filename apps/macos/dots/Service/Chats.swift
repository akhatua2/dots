import Foundation
import Observation

/// One question and its answer, with the receipt that says where it ran.
struct ChatTurn: Codable, Equatable, Identifiable {
    var id = UUID()
    var question: String
    var answer: String
    var receipt: Receipt?
    var attachment: String?
    var date: Date
    /// Your thumbs up (1) or down (-1) on the answer. Kept with the chat on this Mac.
    var rating: Int?
    /// When the answer is a picture: its file, among the chats' pictures on this Mac
    var picture: String?

    var hasAnswer: Bool { picture != nil || !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
}

/// A conversation: its turns, oldest first. Its title is its first question.
struct Chat: Codable, Equatable, Identifiable {
    var id = UUID()
    var turns: [ChatTurn]
    var updated: Date

    var title: String {
        guard let first = turns.first?.question else { return "New chat" }
        return first.count > 60 ? String(first.prefix(57)) + "…" : first
    }
}

/// Your chats, newest first. They can hold private documents, so they stay on this Mac
/// (Application Support/dots/chats.json) and never go to the cloud.
@MainActor
@Observable
final class ChatStore {
    private(set) var chats: [Chat] = []
    /// The chat new questions continue. Nil starts a new one with the next question.
    private(set) var currentID: UUID?
    @ObservationIgnored private let url: URL?
    /// Pictures of a store that isn't saved (the self-test's), kept in memory instead
    @ObservationIgnored private var unsaved: [String: Data] = [:]

    init(persists: Bool) {
        url = persists ? Simulation.supportURL("chats.json") : nil
        if let url, let data = try? Data(contentsOf: url), let saved = try? JSONDecoder.dots.decode([Chat].self, from: data) {
            // An answer that never came (an older dots saved those) isn't worth showing
            chats = saved.map { chat in
                var chat = chat
                chat.turns.removeAll { !$0.hasAnswer }
                return chat
            }.filter { !$0.turns.isEmpty }
        }
    }

    var current: Chat? { chats.first { $0.id == currentID } }

    /// Adds a finished turn to the current chat, or starts a chat with it, and moves that chat to the top.
    func record(_ turn: ChatTurn) {
        guard turn.hasAnswer else { return }
        var chat = current ?? Chat(turns: [], updated: turn.date)
        chat.turns.append(turn)
        chat.updated = turn.date
        chats.removeAll { $0.id == chat.id }
        chats.insert(chat, at: 0)
        currentID = chat.id
        save()
    }

    /// Thumbs up or down on an answer; the same again takes it back.
    func rate(_ turnID: UUID, _ rating: Int) {
        guard let c = chats.firstIndex(where: { $0.turns.contains { $0.id == turnID } }),
              let t = chats[c].turns.firstIndex(where: { $0.id == turnID }) else { return }
        chats[c].turns[t].rating = chats[c].turns[t].rating == rating ? nil : rating
        save()
    }

    /// Keeps a picture an answer drew, beside the chats on this Mac, and says what to call it in the turn.
    func keepPicture(_ png: Data) -> String {
        let name = UUID().uuidString + ".png"
        if let url { try? writePicture(png, to: url.deletingLastPathComponent().appendingPathComponent("pictures/" + name)) }
        else { unsaved[name] = png }
        return name
    }

    func picture(_ name: String) -> Data? {
        if let png = unsaved[name] { return png }
        guard let url else { return nil }
        return try? Data(contentsOf: url.deletingLastPathComponent().appendingPathComponent("pictures/" + name))
    }

    private func writePicture(_ png: Data, to file: URL) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try png.write(to: file, options: .atomic)
    }

    func open(_ id: UUID) { currentID = id }
    func startNew() { currentID = nil }

    func delete(_ id: UUID) {
        chats.removeAll { $0.id == id }
        if currentID == id { currentID = nil }
        save()
    }

    private func save() {
        guard let url else { return }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? JSONEncoder.dots.encode(chats) { try? data.write(to: url, options: .atomic) }
    }
}
