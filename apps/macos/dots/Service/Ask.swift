import Foundation
import Observation
import UniformTypeIdentifiers

/// A receipt for one answer: where it ran, on which Macs, why, and what it cost.
struct Receipt: Codable, Equatable {
    var destination: Destination
    var model: String
    var place: String
    var macs: [ReceiptMac]
    var asleepMacID: String?
    var handoffNote: String?
    var tokPerSec: Double
    var credits: Int
    var saved: Int
    var isPrivate: Bool
    /// The privacy level it ran at, when it was kept private
    var level: PrivacyLevel? = nil
    var isSimulated: Bool
    var reasons: [RouteReason]
    var finished = false
    /// Billed to your own frontier key instead of paid in credits
    var paidWithKey = false
    var usd: Double = 0
    var inputTokens: Int?
    var outputTokens: Int?
    /// Set when the first place didn't answer and it went to the next
    var fallbackNote: String?
    /// With a draft model: how many checks the pool ran, and how many drafted tokens it kept
    var draftChecks: Int?
    var draftKept: Int?
    var draftProposed: Int?
    /// This answer's speed over the same pool without a draft, once the pool has measured that
    var speedup: Double?
}

/// One check of a draft model's proposal by the big model.
struct DraftCheck: Equatable {
    var kept: Int
    var proposed: Int
}

extension Receipt {
    /// One quiet line: where it ran, the model, how fast, what it cost.
    var summary: String {
        var parts = [place, modelName]
        if tokPerSec > 0 { parts.append("\(Int(tokPerSec.rounded())) tok/s") }
        if let checks = draftChecks, checks > 0, let kept = draftKept {
            parts.append(String(format: "%.1f tokens a check", Double(kept + checks) / Double(checks)))
        }
        parts.append(paidWithKey ? usd.dollars : credits == 0 ? "free" : "\(credits) credits")
        if isPrivate { parts.append(level?.label.lowercased() ?? "private") }
        return parts.joined(separator: " · ")
    }

    /// Why it went where it did, one reason a line.
    /// Where it ran and what it cost, as a sentence: "Answered in dots pool by Qwen3.5 0.8B, free."
    var sentence: String {
        let cost = paidWithKey ? "billed \(usd.dollars) to your key" : credits == 0 ? "free" : "for \(credits) credits"
        return "Answered in \(place) by \(modelName), \(cost)."
    }

    var why: String {
        (reasons.map(\.text) + [fallbackNote, handoffNote].compactMap { $0 }).joined(separator: "\n")
    }

    var symbol: String {
        switch destination {
        case .thisMac: "desktopcomputer"
        case .frontier: "sparkle"
        case .publicPool: "globe"
        case .yourPool: "network"
        }
    }

    /// "mlx-community/Qwen3.5-2B-MLX-4bit" -> "Qwen3.5 2B"; names like "GLM 5.3" pass through.
    var modelName: String {
        var name = model.split(separator: "/").last.map(String.init) ?? model
        for suffix in ["-4bit", "-8bit", "-bf16", "-MLX", "-MXFP4", "-Q4", "-Instruct", "-Chat", "-it"] {
            name = name.replacingOccurrences(of: suffix, with: "")
        }
        // A hyphen before a size or a version is a space ("Qwen3.5 2B"); others stay ("gpt-oss")
        return name.replacingOccurrences(of: "-(?=[0-9A-Z])", with: " ", options: .regularExpression)
    }
}

struct ReceiptMac: Codable, Equatable, Identifiable {
    var id: String
    var initial: String
    var name: String
}

/// Turns a route into an answer stream from where it really runs: the model on this Mac, a pool through the
/// dots hub, or a frontier model through the dots gateway (paid in credits) or your own key.
@MainActor
enum AnswerRunner {
    struct Plan {
        var events: AsyncThrowingStream<AnswerEvent, Error>
        var endpoint: EndpointConfig
        var paidWithKey: Bool
        var via: String
    }

    static func target(for c: RouteCandidate, model app: AppModel, level: PrivacyLevel = .anywhere)
        -> (endpoint: EndpointConfig, paidWithKey: Bool, via: String)? {
        let settings = app.settings
        switch c.destination {
        case .thisMac:
            let e = settings.poolEndpoint
            return e.isReady ? (e, false, e.serverName.map { "\($0) on this Mac" } ?? "the model on this Mac") : nil
        case .yourPool, .publicPool:
            guard let pool = c.poolID else { return nil }
            var hub = app.backend.client.hub(poolID: pool, model: c.modelID)
            hub.draftAhead = settings.draftAhead
            hub.privacy = level
            return (hub, false, "the dots hub")
        case .frontier:
            var own = settings.frontierEndpoint
            let ownReady = own.isReady
            if own.isAnthropic { own.model = c.modelID }
            let gateway = (app.backend.client.gateway(model: c.modelID), false, "the dots gateway, in credits")
            let key = (own, true, "your \(own.placeName) key")
            switch settings.payFrontierWith {
            case .credits: return Int(app.state.credits) >= c.credits || !ownReady ? gateway : key
            case .key: return ownReady ? key : gateway
            }
        }
    }

    static func plan(for c: RouteCandidate, messages: [ChatMessage], model app: AppModel, level: PrivacyLevel = .anywhere) -> Plan? {
        guard let t = target(for: c, model: app, level: level) else { return nil }
        return Plan(events: OpenAIClient.stream(t.endpoint, messages: messages), endpoint: t.endpoint, paidWithKey: t.paidWithKey, via: t.via)
    }

    static func receipt(for c: RouteCandidate, decision: RouteDecision, plan: Plan) -> Receipt {
        receipt(for: c, decision: decision, via: plan.via, paidWithKey: plan.paidWithKey)
    }

    static func receipt(for c: RouteCandidate, decision: RouteDecision, via: String, paidWithKey: Bool) -> Receipt {
        var reasons = decision.reasons
        reasons.append(RouteReason(symbol: c.destination == .thisMac ? "desktopcomputer" : "network", text: "Answered through \(via)."))
        return Receipt(destination: c.destination, model: c.model, place: c.place, macs: [], tokPerSec: c.tokPerSec,
                       credits: paidWithKey ? 0 : c.credits, saved: paidWithKey ? 0 : max(0, c.listCredits - c.credits),
                       isPrivate: decision.isPrivate && c.isPrivate, level: decision.isPrivate && c.isPrivate ? decision.level : nil,
                       isSimulated: false, reasons: reasons, paidWithKey: paidWithKey)
    }

    /// The request for a question: the system prompt, the conversation so far, then the question, with its
    /// picture if one is attached. An attached document's text goes in the system prompt.
    static func messages(question: String, attachment: AskController.Attachment?, history: [ChatTurn] = []) -> [ChatMessage] {
        var system = "You are answering through dots, a pool of Macs. Be direct and concise."
        if let a = attachment, a.picture == nil {
            system += "\n\nThe user attached \(a.name). Its text:\n\n\(a.text)"
        }
        let earlier = history.flatMap { [ChatMessage(role: "user", content: $0.question), ChatMessage(role: "assistant", content: $0.answer)] }
        let asked = ChatMessage(role: "user", content: question, images: attachment?.picture.map { [$0] } ?? [])
        return [ChatMessage(role: "system", content: system)] + earlier + [asked]
    }
}

/// The ask panel's state: the question, the route, the streaming answer, its receipt, and a second opinion.
@MainActor
@Observable
final class AskController {
    enum Phase: Equatable { case idle, routing, streaming, done, failed(String), needsConsent(String) }

    struct Attachment: Equatable {
        var name: String
        var text: String
        var url: URL
        /// A picture, shrunk and ready to send; nil for a document
        var picture: Data?
    }

    struct SecondOpinion: Equatable {
        var candidate: RouteCandidate
        var phase: Phase = .idle
        var text = ""
        var receipt: Receipt?
    }

    var input = ""
    var attachment: Attachment?
    var forcedID: String?
    var phase: Phase = .idle
    var asked = ""
    var decision: RouteDecision?
    var answer = ""
    /// The draft model's proposal while the pool checks it, shown greyed out after the answer
    var draft = ""
    /// Each check of the draft so far, oldest first. Empty when the answer isn't speculative.
    var checks: [DraftCheck] = []
    var receipt: Receipt?
    var second: SecondOpinion?
    var focusToken = 0
    /// The chat turn this exchange was saved as, once its answer finished.
    var recordedTurnID: UUID?

    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var secondTask: Task<Void, Never>?

    var isBusy: Bool { phase == .routing || phase == .streaming }

    /// What can be attached to a question: documents, read as text, and pictures
    static let attachable: [UTType] = [.pdf, .plainText, .text, .sourceCode, .json, .commaSeparatedText, .image]

    func attach(_ url: URL, maxPixels: Int) {
        if Pictures.isPicture(url) {
            guard let picture = Pictures.jpeg(from: url, maxPixels: maxPixels) else { return }
            attachment = Attachment(name: url.lastPathComponent, text: "", url: url, picture: picture)
            return
        }
        guard let text = Documents.text(from: url), !text.isEmpty else { return }
        attachment = Attachment(name: url.lastPathComponent, text: text, url: url)
    }

    func clear() {
        task?.cancel(); secondTask?.cancel()
        input = ""; asked = ""; attachment = nil; forcedID = nil
        phase = .idle; decision = nil; answer = ""; receipt = nil; second = nil; recordedTurnID = nil
    }

    func submit(model: AppModel) {
        let question = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return }
        let messages = AnswerRunner.messages(question: question, attachment: attachment, history: model.chats.current?.turns ?? [])
        let facts = RequestFacts(inputTokens: messages.reduce(0) { $0 + Pricing.tokens($1.content) + 4 },
                                 hasImages: attachment?.picture != nil)
        let decision = Router.decide(question: question, hasDocument: attachment != nil, state: model.state,
                                     rules: model.settings.rules, forcedID: forcedID, facts: facts, thisMac: model.thisMacRoute)
        run(question: question, decision: decision, model: model)
    }

    /// For a private question nothing private can take: the user says send it to a frontier model anyway.
    func sendAnyway(model: AppModel) {
        guard let f = decision?.candidates.first(where: { $0.destination == .frontier }) else { return }
        let decision = RouteDecision(chosen: f, candidates: self.decision?.candidates ?? [f],
                                     reasons: [RouteReason(symbol: "hand.point.up.left", text: "You sent it to \(f.model) yourself."),
                                               RouteReason(symbol: "arrow.up.right.square", text: "It left your pools and went to \(f.place).")],
                                     isPrivate: false)
        run(question: asked, decision: decision, model: model)
    }

    private func run(question: String, decision: RouteDecision, model: AppModel) {
        task?.cancel(); secondTask?.cancel()
        asked = question
        answer = ""; receipt = nil; second = nil
        self.decision = decision
        phase = .routing
        let attachment = self.attachment
        let history = model.chats.current?.turns ?? []
        recordedTurnID = nil
        task = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(650))
            guard let self, !Task.isCancelled else { return }
            guard let chosen = decision.chosen else {
                self.phase = .needsConsent(decision.needsConsent ?? "Nothing can take this right now.")
                return
            }
            let messages = AnswerRunner.messages(question: question, attachment: attachment, history: history)
            var missed: [String] = []
            for candidate in [chosen] + decision.fallbacks {
                guard let plan = AnswerRunner.plan(for: candidate, messages: messages, model: model, level: decision.level) else { continue }
                var receipt = AnswerRunner.receipt(for: candidate, decision: decision, plan: plan)
                if !missed.isEmpty {
                    receipt.fallbackNote = "\(missed.joined(separator: " and ")) didn't answer, so it went to \(candidate.place)."
                }
                self.answer = ""
                self.draft = ""
                self.checks = []
                self.receipt = receipt
                self.phase = .streaming
                let started = Date()
                var usage: (input: Int, output: Int)?
                var charged: Int?
                do {
                    for try await event in plan.events {
                        if Task.isCancelled { return }
                        switch event {
                        case .text(let t):
                            self.answer += t
                            self.draft = ""
                            let elapsed = Date().timeIntervalSince(started)
                            if elapsed > 0.5 { receipt.tokPerSec = Double(Pricing.tokens(self.answer)) / elapsed }
                        case .draft(let d):
                            self.draft = d
                        case .speedup(let s):
                            receipt.speedup = s
                        case let .verified(kept, proposed):
                            self.checks.append(DraftCheck(kept: kept, proposed: proposed))
                            receipt.draftChecks = self.checks.count
                            receipt.draftKept = self.checks.reduce(0) { $0 + $1.kept }
                            receipt.draftProposed = self.checks.reduce(0) { $0 + $1.proposed }
                        case let .macAsleep(macID, name):
                            receipt.asleepMacID = macID
                            receipt.handoffNote = "\(name)'s Mac went to sleep. Moving its layers to the others…"
                            if let pool = candidate.poolID { model.backend.setMemberAsleep(macID: macID, poolID: pool, asleep: true) }
                        case .resumed:
                            let others = receipt.macs.filter { $0.id != receipt.asleepMacID }.count
                            let name = receipt.macs.first { $0.id == receipt.asleepMacID }?.name ?? "A Mac"
                            receipt.handoffNote = "\(name)'s Mac went to sleep. The other \(others == 3 ? "three" : "\(others)") took over."
                        case .model(let m):
                            if candidate.destination == .thisMac { receipt.model = m }
                        case let .macs(macs, simulated):
                            receipt.macs = macs
                            receipt.isSimulated = simulated
                        case let .usage(input, output):
                            usage = (input, output)
                        case .charged(let c):
                            charged = c
                            receipt.credits = c
                        case .simulated(let s):
                            receipt.isSimulated = s
                        }
                        self.receipt = receipt
                    }
                } catch {
                    self.draft = ""
                    // Nothing came back yet: try the next place, within the same rules
                    if self.answer.isEmpty {
                        if candidate.destination == .thisMac { model.noteLocalDown() }
                        missed.append(candidate.place)
                        continue
                    }
                    self.phase = .failed(error.localizedDescription)
                    return
                }
                self.draft = ""
                // An answer that ended without a word didn't answer either
                if self.answer.isEmpty {
                    missed.append(candidate.place)
                    continue
                }
                let input = usage?.input ?? messages.reduce(0) { $0 + Pricing.tokens($1.content) + 4 }
                let output = usage?.output ?? Pricing.tokens(self.answer)
                receipt.inputTokens = input
                receipt.outputTokens = output
                receipt.usd = plan.paidWithKey ? Pricing.usd(candidate.modelID, input: input, output: output) : 0
                // Credits are what the hub or gateway actually charged; your key's bill is in dollars instead
                receipt.credits = plan.paidWithKey ? 0 : (charged ?? 0)
                receipt.finished = true
                self.receipt = receipt
                self.phase = .done
                let turn = ChatTurn(question: question, answer: self.answer, receipt: receipt, attachment: attachment?.name, date: .now)
                model.chats.record(turn)
                self.recordedTurnID = turn.id
                if candidate.destination == .thisMac, receipt.tokPerSec > 0 { model.noteLocalSpeed(receipt.tokPerSec) }
                model.recordAnswer(destination: candidate.destination, place: candidate.place, model: candidate.model, source: "Ask",
                                   credits: receipt.credits, usd: receipt.usd, isPrivate: receipt.isPrivate, input: input, output: output,
                                   measured: usage != nil, fallback: receipt.fallbackNote, format: .openai)
                if let f = decision.candidates.first(where: { $0.destination == .frontier }), candidate.destination != .frontier {
                    self.second = SecondOpinion(candidate: f)
                }
                return
            }
            let places = missed.reduce(into: [String]()) { if !$0.contains($1) { $0.append($1) } }
            self.phase = .failed(places.isEmpty ? "Nothing that can take this is set up." : "\(places.joined(separator: " and ")) didn't answer.")
        }
    }

    func askSecondOpinion(model: AppModel) {
        guard var second, second.phase == .idle else { return }
        let c = second.candidate
        let decision = RouteDecision(chosen: c, candidates: [c], reasons: [
            RouteReason(symbol: "hand.point.up.left", text: "You asked \(c.model) for a second opinion."),
            RouteReason(symbol: "arrow.up.right.square", text: "It left your pools and went to \(c.place)."),
        ], isPrivate: false)
        var messages = AnswerRunner.messages(question: asked, attachment: attachment)
        messages.append(ChatMessage(role: "assistant", content: answer))
        messages.append(ChatMessage(role: "user", content: "Give a short second opinion on that answer. What would you add or change?"))
        guard let plan = AnswerRunner.plan(for: c, messages: messages, model: model) else { return }
        if !plan.paidWithKey, Int(model.state.credits) < c.credits {
            second.phase = .failed("You need about \(c.credits) credits for this, and have \(Int(model.state.credits)). Lend a little longer.")
            self.second = second
            return
        }
        var receipt = AnswerRunner.receipt(for: c, decision: decision, plan: plan)
        second.phase = .streaming
        second.receipt = receipt
        self.second = second
        secondTask = Task { [weak self] in
            var usage: (input: Int, output: Int)?
            do {
                for try await event in plan.events {
                    guard let self else { return }
                    switch event {
                    case .text(let t): self.second?.text += t
                    case let .usage(i, o): usage = (i, o)
                    case .charged(let n): receipt.credits = n
                    case .simulated(let s): receipt.isSimulated = s
                    default: break
                    }
                    self.second?.receipt = receipt
                }
                guard let self else { return }
                let input = usage?.input ?? messages.reduce(0) { $0 + Pricing.tokens($1.content) + 4 }
                let output = usage?.output ?? Pricing.tokens(self.second?.text ?? "")
                receipt.usd = plan.paidWithKey ? Pricing.usd(c.modelID, input: input, output: output) : 0
                receipt.finished = true
                self.second?.receipt = receipt
                self.second?.phase = .done
                model.recordAnswer(destination: c.destination, place: c.place, model: c.model, source: "Ask", credits: receipt.credits,
                                   usd: receipt.usd, isPrivate: false, input: input, output: output, measured: usage != nil,
                                   fallback: nil, format: .openai)
            } catch {
                self?.second?.phase = .failed(error.localizedDescription)
            }
        }
    }
}
