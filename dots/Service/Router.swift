import Foundation

/// One place a question could run.
struct RouteCandidate: Identifiable, Hashable {
    var id: String
    var destination: Destination
    var place: String           // "Studio 9", "Open Science", "Anthropic"
    var model: String           // "Qwen3 235B"
    var modelID: String
    var credits: Int
    var listCredits: Int
    var tokPerSec: Double
    var isPrivate: Bool
    var poolID: String?
    var minGB: Int              // how big the model is, for comparisons
}

struct RouteReason: Hashable {
    var symbol: String
    var text: String
}

struct RouteDecision {
    var chosen: RouteCandidate?
    var candidates: [RouteCandidate]
    var reasons: [RouteReason]
    var isPrivate: Bool
    /// Set when nothing private can take a private question: the user decides.
    var needsConsent: String?
}

/// Picks where a question runs. In order: privacy, then your own pools (free), then the wider
/// network or a frontier model, within your credits and your per-answer cap.
enum Router {
    static func decide(question: String, hasDocument: Bool, state: NetworkState, rules: RouterRules,
                       reach: Reach = .all, forcedID: String? = nil) -> RouteDecision {
        let personal = looksPersonal(question)
        let isPrivate = hasDocument || personal || rules.privateByDefault
        let hard = looksHard(question)

        // Everything that could take it
        let ownPools = state.myPools
            .filter { !$0.isPublic }
            .compactMap { pool -> RouteCandidate? in
                guard let model = pool.model, pool.lendingMacs > 0 else { return nil }
                return RouteCandidate(id: "pool:\(pool.id)", destination: .yourPool, place: pool.name, model: model.name,
                                      modelID: model.id, credits: 0, listCredits: 0, tokPerSec: Double(pool.tokPerSec),
                                      isPrivate: true, poolID: pool.id, minGB: model.minGB)
            }
            .sorted { $0.minGB == $1.minGB ? $0.tokPerSec > $1.tokPerSec : $0.minGB > $1.minGB }
        let publicPools = state.publicPools
            .compactMap { pool -> RouteCandidate? in
                guard let model = pool.model, pool.lendingMacs > 0 else { return nil }
                return RouteCandidate(id: "pool:\(pool.id)", destination: .publicPool, place: pool.name, model: model.name,
                                      modelID: model.id, credits: model.credits, listCredits: model.credits,
                                      tokPerSec: Double(pool.tokPerSec), isPrivate: false, poolID: pool.id, minGB: model.minGB)
            }
            .sorted { lhs, rhs in
                if rules.prefer == .speed { return lhs.tokPerSec > rhs.tokPerSec }
                return lhs.minGB == rhs.minGB ? lhs.tokPerSec > rhs.tokPerSec : lhs.minGB > rhs.minGB
            }
        let frontier = FrontierModel.all.map {
            RouteCandidate(id: "frontier:\($0.id)", destination: .frontier, place: $0.provider, model: $0.name, modelID: $0.id,
                           credits: $0.credits, listCredits: $0.listCredits, tokPerSec: $0.tokPerSec, isPrivate: false,
                           poolID: nil, minGB: 10_000)
        }

        let allowedOwn = reach.contains(.yourPools) ? ownPools : []
        let allowedPublic = reach.contains(.publicPools) ? publicPools : []
        let allowedFrontier = reach.contains(.frontier) ? frontier : []
        let credits = Int(state.credits)

        // The three the panel shows: your best pool, the network's best, and the frontier model
        var shown: [RouteCandidate] = []
        if let c = allowedOwn.first { shown.append(c) }
        if let c = allowedPublic.first { shown.append(c) }
        if let c = allowedFrontier.first { shown.append(c) }

        // You picked something yourself
        if let forcedID, let forced = (ownPools + publicPools + frontier).first(where: { $0.id == forcedID }) {
            var reasons = [RouteReason(symbol: "hand.point.up.left", text: "You picked \(forced.model).")]
            if isPrivate && !forced.isPrivate {
                reasons.append(RouteReason(symbol: "arrow.up.right.square", text: "It leaves your pools, because you chose it."))
            }
            if forced.credits > 0 { reasons.append(RouteReason(symbol: "smallcircle.filled.circle", text: "\(forced.credits) credits.")) }
            if !shown.contains(forced) { shown.insert(forced, at: 0) }
            return RouteDecision(chosen: forced, candidates: shown, reasons: reasons, isPrivate: isPrivate && forced.isPrivate)
        }

        // 1. Privacy first: a private question only runs in pools you're in with people you know
        if isPrivate {
            let why: RouteReason
            if hasDocument {
                why = RouteReason(symbol: "lock", text: "It includes a personal document, so it stayed in pools you're in.")
            } else if personal {
                why = RouteReason(symbol: "lock", text: "It looks personal, so it stayed in pools you're in.")
            } else {
                why = RouteReason(symbol: "lock", text: "You keep questions private by default, so it stayed in pools you're in.")
            }
            guard let pick = allowedOwn.first else {
                return RouteDecision(chosen: nil, candidates: shown, reasons: [why], isPrivate: true,
                                     needsConsent: "None of your own pools is lending right now, and this question is private.")
            }
            var reasons = [why, RouteReason(symbol: "person.2", text: "You're in \(pick.place), so it ran there free.")]
            reasons.append(RouteReason(symbol: "checkmark", text: "\(pick.model) is enough for this."))
            return RouteDecision(chosen: pick, candidates: shown, reasons: reasons, isPrivate: true)
        }

        // 2. Your own pools, free, when their model is up to it
        let strongEnough = { (c: RouteCandidate) in !hard || c.minGB >= 136 }
        if let pick = allowedOwn.first(where: strongEnough), rules.prefer != .quality || !hard {
            return RouteDecision(chosen: pick, candidates: shown, reasons: [
                RouteReason(symbol: "person.2", text: "You're in \(pick.place), so it ran there free."),
                RouteReason(symbol: "checkmark", text: "\(pick.model) is enough for this."),
            ], isPrivate: false)
        }

        // 3. A frontier model, when the task needs one and your credits and cap allow it
        let cap = rules.maxCreditsPerAnswer
        if hard || rules.prefer == .quality, let f = allowedFrontier.first(where: { $0.credits <= cap && $0.credits <= credits }) {
            return RouteDecision(chosen: f, candidates: shown, reasons: [
                RouteReason(symbol: "sparkle", text: hard ? "This needs a frontier model." : "You prefer the best answer."),
                RouteReason(symbol: "smallcircle.filled.circle", text: "\(f.credits) credits, within your cap of \(cap) an answer."),
                RouteReason(symbol: "arrow.up.right.square", text: "It leaves your pools and goes to \(f.place)."),
            ], isPrivate: false)
        }

        // 4. The network's best public pool
        if let p = allowedPublic.first(where: { $0.credits <= credits }) {
            var reasons = [RouteReason(symbol: "globe", text: allowedOwn.isEmpty
                ? "None of your own pools is lending, so it went to \(p.place)."
                : "\(p.place) runs a bigger model than your pools.")]
            reasons.append(RouteReason(symbol: "smallcircle.filled.circle", text: "\(p.credits) credits."))
            return RouteDecision(chosen: p, candidates: shown, reasons: reasons, isPrivate: false)
        }

        // 5. Whatever is left
        if let own = allowedOwn.first {
            return RouteDecision(chosen: own, candidates: shown, reasons: [
                RouteReason(symbol: "person.2", text: "You're in \(own.place), so it ran there free."),
            ], isPrivate: false)
        }
        return RouteDecision(chosen: nil, candidates: shown, reasons: [], isPrivate: false,
                             needsConsent: "Nothing this key can reach is running right now.")
    }

    static func looksPersonal(_ q: String) -> Bool {
        let s = q.lowercased()
        let words = ["my lease", "my contract", "salary", "my bank", "diagnos", "medical", "my doctor", "password",
                     "my tax", "invoice", "my account", "passport", "my therapist", "my health", "my rent"]
        return words.contains { s.contains($0) }
    }

    static func looksHard(_ q: String) -> Bool {
        let s = q.lowercased()
        let words = ["prove", "architecture", "refactor", "step by step", "in depth", "analy", "design a", "debug", "optimi"]
        return q.count > 600 || words.contains { s.contains($0) }
    }
}
