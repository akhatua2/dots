import Foundation
import Observation

/// A receipt for one answer: where it ran, on which Macs, why, and what it cost.
struct Receipt: Equatable {
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
    var isSimulated: Bool
    var reasons: [RouteReason]
    var finished = false
}

struct ReceiptMac: Equatable, Identifiable {
    var id: String
    var initial: String
    var name: String
}

/// Turns a route into an answer stream: a real endpoint when one is set for that route, the simulation otherwise.
@MainActor
enum AnswerRunner {
    struct Plan {
        var events: AsyncThrowingStream<AnswerEvent, Error>
        var isSimulated: Bool
        var endpointLabel: String?
    }

    static func plan(for c: RouteCandidate, messages: [ChatMessage], question: String, documentName: String?,
                     second: Bool = false, settings: AppSettings, state: NetworkState, allowSleep: Bool) -> Plan {
        switch c.destination {
        case .yourPool where settings.poolEndpoint.isReady:
            return Plan(events: OpenAIClient.stream(settings.poolEndpoint, messages: messages), isSimulated: false,
                        endpointLabel: settings.poolEndpoint.label)
        case .frontier where settings.frontierEndpoint.isReady:
            return Plan(events: OpenAIClient.stream(settings.frontierEndpoint, messages: messages), isSimulated: false,
                        endpointLabel: settings.frontierEndpoint.label)
        default:
            let text = second
                ? SimulatedAnswers.secondOpinion(for: question, documentName: documentName)
                : SimulatedAnswers.answer(for: question, documentName: documentName)
            var sleeper: (macID: String, name: String)?
            if allowSleep, c.destination == .yourPool, let pool = state.pool(c.poolID) {
                let others = pool.macs.filter { $0.state == .lending && $0.macID != state.thisMacID }
                if others.count >= 3, Double.random(in: 0...1) < 0.6, let mac = others.last,
                   let owner = state.device(mac.macID).flatMap({ state.person($0.ownerID) }) {
                    sleeper = (mac.macID, owner.name)
                }
            }
            return Plan(events: SimulatedAnswers.stream(text, tokPerSec: c.tokPerSec, sleeper: sleeper), isSimulated: true, endpointLabel: nil)
        }
    }

    static func receipt(for c: RouteCandidate, decision: RouteDecision, plan: Plan, state: NetworkState) -> Receipt {
        var macs: [ReceiptMac] = []
        if plan.isSimulated, c.destination == .yourPool, let pool = state.pool(c.poolID) {
            macs = pool.macs.filter { $0.state == .lending }.compactMap { m in
                guard let d = state.device(m.macID), let p = state.person(d.ownerID) else { return nil }
                return ReceiptMac(id: m.macID, initial: d.id == state.thisMacID ? "You" : p.initial, name: p.name)
            }
        }
        var reasons = decision.reasons
        if let label = plan.endpointLabel, c.destination == .yourPool {
            reasons.append(RouteReason(symbol: "network", text: "Your pools answer through \(label)."))
        }
        return Receipt(destination: c.destination, model: c.model, place: plan.endpointLabel ?? c.place, macs: macs,
                       tokPerSec: c.tokPerSec, credits: c.credits, saved: max(0, c.listCredits - c.credits),
                       isPrivate: decision.isPrivate && c.isPrivate, isSimulated: plan.isSimulated, reasons: reasons)
    }

    static func messages(question: String, attachment: AskController.Attachment?) -> [ChatMessage] {
        var system = "You are answering through dots, a pool of Macs. Be direct and concise."
        if let a = attachment {
            system += "\n\nThe user attached \(a.name). Its text:\n\n\(a.text)"
        }
        return [ChatMessage(role: "system", content: system), ChatMessage(role: "user", content: question)]
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
    var receipt: Receipt?
    var second: SecondOpinion?
    var focusToken = 0

    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var secondTask: Task<Void, Never>?

    var isBusy: Bool { phase == .routing || phase == .streaming }

    func attach(_ url: URL) {
        guard let text = Documents.text(from: url), !text.isEmpty else { return }
        attachment = Attachment(name: url.lastPathComponent, text: text, url: url)
    }

    func clear() {
        task?.cancel(); secondTask?.cancel()
        input = ""; asked = ""; attachment = nil; forcedID = nil
        phase = .idle; decision = nil; answer = ""; receipt = nil; second = nil
    }

    func submit(model: AppModel) {
        let question = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return }
        let decision = Router.decide(question: question, hasDocument: attachment != nil, state: model.state,
                                     rules: model.settings.rules, forcedID: forcedID)
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
        task = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(650))
            guard let self, !Task.isCancelled else { return }
            guard let chosen = decision.chosen else {
                self.phase = .needsConsent(decision.needsConsent ?? "Nothing can take this right now.")
                return
            }
            let messages = AnswerRunner.messages(question: question, attachment: attachment)
            let plan = AnswerRunner.plan(for: chosen, messages: messages, question: question, documentName: attachment?.name,
                                         settings: model.settings, state: model.state, allowSleep: true)
            var receipt = AnswerRunner.receipt(for: chosen, decision: decision, plan: plan, state: model.state)
            self.receipt = receipt
            self.phase = .streaming
            let started = Date()
            var chars = 0
            do {
                for try await event in plan.events {
                    if Task.isCancelled { return }
                    switch event {
                    case .text(let t):
                        self.answer += t
                        chars += t.count
                        let elapsed = Date().timeIntervalSince(started)
                        if !plan.isSimulated, elapsed > 0.5 { receipt.tokPerSec = Double(chars) / 4 / elapsed }
                    case let .macAsleep(macID, name):
                        receipt.asleepMacID = macID
                        receipt.handoffNote = "\(name)'s Mac went to sleep. Moving its layers to the others…"
                        if let pool = chosen.poolID { model.backend.setMemberAsleep(macID: macID, poolID: pool, asleep: true) }
                    case .resumed:
                        let others = receipt.macs.filter { $0.id != receipt.asleepMacID }.count
                        let name = receipt.macs.first { $0.id == receipt.asleepMacID }?.name ?? "A Mac"
                        receipt.handoffNote = "\(name)'s Mac went to sleep. The other \(others == 3 ? "three" : "\(others)") took over."
                    case .model(let m):
                        receipt.model = m
                    }
                    self.receipt = receipt
                }
                receipt.finished = true
                self.receipt = receipt
                self.phase = .done
                model.finishAnswer(receipt: receipt, source: "Ask")
                if let f = decision.candidates.first(where: { $0.destination == .frontier }), chosen.destination != .frontier {
                    self.second = SecondOpinion(candidate: f)
                }
            } catch {
                self.phase = .failed(error.localizedDescription)
            }
        }
    }

    func askSecondOpinion(model: AppModel) {
        guard var second, second.phase == .idle else { return }
        let c = second.candidate
        guard Int(model.state.credits) >= c.credits else {
            second.phase = .failed("You need \(c.credits) credits for this, and have \(Int(model.state.credits)). Lend a little longer.")
            self.second = second
            return
        }
        let decision = RouteDecision(chosen: c, candidates: [c], reasons: [
            RouteReason(symbol: "hand.point.up.left", text: "You asked \(c.model) for a second opinion."),
            RouteReason(symbol: "arrow.up.right.square", text: "It left your pools and went to \(c.place)."),
        ], isPrivate: false)
        var messages = AnswerRunner.messages(question: asked, attachment: attachment)
        messages.append(ChatMessage(role: "assistant", content: answer))
        messages.append(ChatMessage(role: "user", content: "Give a short second opinion on that answer. What would you add or change?"))
        let plan = AnswerRunner.plan(for: c, messages: messages, question: asked, documentName: attachment?.name, second: true,
                                     settings: model.settings, state: model.state, allowSleep: false)
        var receipt = AnswerRunner.receipt(for: c, decision: decision, plan: plan, state: model.state)
        second.phase = .streaming
        second.receipt = receipt
        self.second = second
        secondTask = Task { [weak self] in
            do {
                for try await event in plan.events {
                    guard let self else { return }
                    if case .text(let t) = event { self.second?.text += t }
                    if case .model(let m) = event { receipt.model = m; self.second?.receipt = receipt }
                }
                guard let self else { return }
                receipt.finished = true
                self.second?.receipt = receipt
                self.second?.phase = .done
                model.finishAnswer(receipt: receipt, source: "Ask")
            } catch {
                self?.second?.phase = .failed(error.localizedDescription)
            }
        }
    }
}
