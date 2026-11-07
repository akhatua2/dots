import Foundation

/// One place a question could run.
struct RouteCandidate: Identifiable, Hashable {
    var id: String
    var destination: Destination
    var place: String           // "This Mac", "Studio 9", "Open Science", "Anthropic"
    var model: String           // "MiniMax M2.7"
    var modelID: String
    var credits: Int
    var listCredits: Int
    var tokPerSec: Double
    var isPrivate: Bool
    var poolID: String?
    var minGB: Int              // how big the model is, for comparisons
    var contextTokens = 128_000
    var canUseTools = false
    var canReadImages = false
    /// Every Mac it runs on is yours: this Mac, or a pool of yours whose lending Macs are all your own
    var onlyMine = false
}

/// How far a request may go, and so who could read it there. dots holds to a request's level and never falls
/// back to a wider one; when nothing within it can take the request, you decide. See docs/plans/pools-from-a-link.md.
enum PrivacyLevel: String, Codable, Hashable {
    /// Your own Macs only, and the hub
    case mine
    /// Your pools' Macs, friends' included
    case myPools
    /// Public pools and frontier models too
    case anywhere

    var label: String {
        switch self {
        case .mine: "Only my Macs"
        case .myPools: "My pools"
        case .anywhere: "Anywhere"
        }
    }
}

struct RouteReason: Codable, Hashable {
    var symbol: String
    var text: String
}

struct RouteDecision {
    var chosen: RouteCandidate?
    var candidates: [RouteCandidate]
    var reasons: [RouteReason]
    var isPrivate: Bool
    /// How far it may go: it never runs anywhere wider
    var level: PrivacyLevel = .anywhere
    /// Set when nothing private can take a private question: the user decides.
    var needsConsent: String?
    /// Where it goes next if the chosen place doesn't answer, within the same rules
    var fallbacks: [RouteCandidate] = []
}

/// What the router can see of a request before it runs: its size and what it needs.
struct RequestFacts {
    var inputTokens = 0
    var needsTools = false
    var hasImages = false
    var outputTokens = 600
}

/// Picks where a question runs. In order: privacy, then this Mac and your own pools (free), then the wider
/// network or a frontier model, within your credits and your per-answer cap. It only considers places that can
/// take the request: a long enough context, and tools or images only where they work.
enum Router {
    static func decide(question: String, hasDocument: Bool, state: NetworkState, rules: RouterRules,
                       reach: Reach = .all, forcedID: String? = nil, facts: RequestFacts = RequestFacts(),
                       thisMac: RouteCandidate? = nil) -> RouteDecision {
        let personal = looksPersonal(question)
        // Your documents and photos stay on your own Macs; a question that looks personal stays in your pools
        let level: PrivacyLevel = hasDocument || facts.hasImages ? .mine
            : personal || rules.privateByDefault ? .myPools : .anywhere
        let isPrivate = level != .anywhere
        let hard = looksHard(question) || facts.inputTokens > 24_000

        // Everything that could take it
        let ownPools = state.myPools
            .filter { !$0.isPublic }
            .compactMap { pool -> RouteCandidate? in
                guard let model = pool.model, pool.lendingMacs > 0 else { return nil }
                let mine = pool.macs.filter { $0.state == .lending }
                    .allSatisfy { $0.macID == state.thisMacID || state.device($0.macID)?.ownerID == state.meID }
                return RouteCandidate(id: "pool:\(pool.id)", destination: .yourPool, place: pool.name, model: model.name,
                                      modelID: model.id, credits: 0, listCredits: 0, tokPerSec: Double(pool.tokPerSec),
                                      isPrivate: true, poolID: pool.id, minGB: model.minGB, canReadImages: model.readsImages == true,
                                      onlyMine: mine)
            }
            .sorted { $0.minGB == $1.minGB ? $0.tokPerSec > $1.tokPerSec : $0.minGB > $1.minGB }
        let publicPools = state.publicPools
            .compactMap { pool -> RouteCandidate? in
                guard let model = pool.model, pool.lendingMacs > 0 else { return nil }
                return RouteCandidate(id: "pool:\(pool.id)", destination: .publicPool, place: pool.name, model: model.name,
                                      modelID: model.id, credits: model.credits, listCredits: model.credits,
                                      tokPerSec: Double(pool.tokPerSec), isPrivate: false, poolID: pool.id, minGB: model.minGB,
                                      canReadImages: model.readsImages == true)
            }
            .sorted { lhs, rhs in
                if rules.prefer == .speed { return lhs.tokPerSec > rhs.tokPerSec }
                return lhs.minGB == rhs.minGB ? lhs.tokPerSec > rhs.tokPerSec : lhs.minGB > rhs.minGB
            }
        let frontier = FrontierModel.all.map { f in
            RouteCandidate(id: "frontier:\(f.id)", destination: .frontier, place: f.provider, model: f.name, modelID: f.id,
                           credits: Pricing.credits(f.id, input: facts.inputTokens, output: facts.outputTokens),
                           listCredits: Pricing.listCredits(f.id, input: facts.inputTokens, output: facts.outputTokens),
                           tokPerSec: f.tokPerSec, isPrivate: false, poolID: nil, minGB: 10_000,
                           contextTokens: 1_000_000, canUseTools: true, canReadImages: true)
        }

        // Only places that can take this request at all
        func fits(_ c: RouteCandidate) -> Bool {
            facts.inputTokens + facts.outputTokens <= c.contextTokens && (!facts.needsTools || c.canUseTools)
                && (!facts.hasImages || c.canReadImages)
        }
        let mac = thisMac.flatMap { reach.contains(.yourPools) && fits($0) ? $0 : nil }
        let allowedOwn = reach.contains(.yourPools) ? ownPools.filter(fits) : []
        let allowedPublic = reach.contains(.publicPools) ? publicPools.filter(fits) : []
        let allowedFrontier = reach.contains(.frontier) ? frontier.filter(fits) : []
        let credits = Int(state.credits)
        let cap = rules.maxCreditsPerAnswer
        let affordable = { (c: RouteCandidate) in c.credits <= credits && (c.destination != .frontier || c.credits <= cap) }

        // What the panel shows: this Mac or your best pool, the network's best, and the frontier model
        var shown: [RouteCandidate] = []
        if let c = mac ?? allowedOwn.first { shown.append(c) }
        if let c = allowedPublic.first { shown.append(c) }
        if let c = allowedFrontier.first { shown.append(c) }

        func decision(_ pick: RouteCandidate, _ reasons: [RouteReason], isPrivate: Bool, order: [RouteCandidate]) -> RouteDecision {
            if !shown.contains(pick) { shown.insert(pick, at: 0) }
            var seen = Set<String>([pick.id])
            let fallbacks = order.filter { seen.insert($0.id).inserted }
            return RouteDecision(chosen: pick, candidates: Array(shown.prefix(3)), reasons: reasons, isPrivate: isPrivate,
                                 level: isPrivate ? level : .anywhere, fallbacks: fallbacks)
        }

        // You picked something yourself
        if let forcedID, let forced = ([thisMac].compactMap { $0 } + ownPools + publicPools + frontier).first(where: { $0.id == forcedID }) {
            var reasons = [RouteReason(symbol: "hand.point.up.left", text: "You picked \(forced.model).")]
            if isPrivate && !forced.isPrivate {
                reasons.append(RouteReason(symbol: "arrow.up.right.square", text: "It leaves your pools, because you chose it."))
            }
            if forced.credits > 0 { reasons.append(RouteReason(symbol: "smallcircle.filled.circle", text: "About \(forced.credits) credits.")) }
            return decision(forced, reasons, isPrivate: isPrivate && forced.isPrivate, order: [])
        }

        // 1. Privacy first: a private question only runs on this Mac or in pools you're in with people you know, and
        // one with your own document or photo only on your own Macs
        let within = { (c: RouteCandidate) in level != .mine || c.destination == .thisMac || c.onlyMine }
        let privateOrder = ((hard ? [] : [mac].compactMap { $0 }) + allowedOwn.filter { !hard || $0.minGB >= 136 }
            + (hard ? [mac].compactMap { $0 } + allowedOwn.filter { $0.minGB < 136 } : [])).filter(within)
        if isPrivate {
            let why: RouteReason
            if level == .mine {
                why = RouteReason(symbol: "lock", text: facts.hasImages
                    ? "It includes your picture, so it ran only on your own Macs."
                    : "It includes a personal document, so it ran only on your own Macs.")
            } else if personal {
                why = RouteReason(symbol: "lock", text: "It looks personal, so it stayed in pools you're in.")
            } else {
                why = RouteReason(symbol: "lock", text: "It's kept private, so it stayed on this Mac and in pools you're in.")
            }
            guard let pick = privateOrder.first else {
                let why = facts.needsTools ? "Only a frontier model can take a request with tools, and this one is private."
                    : level == .mine ? "None of your own Macs can take this right now, and it stays on them."
                    : "Nothing on this Mac or in your own pools is lending right now, and this question is private."
                return RouteDecision(chosen: nil, candidates: shown, reasons: [], isPrivate: true, level: level, needsConsent: why)
            }
            var reasons = [why]
            if pick.destination == .thisMac {
                reasons.append(RouteReason(symbol: "desktopcomputer", text: "\(pick.model) on this Mac can answer it, free and without leaving it."))
            } else {
                reasons.append(RouteReason(symbol: "person.2", text: "You're in \(pick.place), so it ran there free."))
                reasons.append(RouteReason(symbol: "checkmark", text: "\(pick.model) is enough for this."))
            }
            return decision(pick, reasons, isPrivate: true, order: privateOrder)
        }

        // 2. Tools: only a frontier model takes them. Images go on, to the places that read them.
        if facts.needsTools {
            guard let f = allowedFrontier.first(where: affordable) ?? allowedFrontier.first else {
                return RouteDecision(chosen: nil, candidates: shown, reasons: [], isPrivate: false,
                                     needsConsent: "This request uses tools, and only a frontier model takes those. This key can't reach one.")
            }
            return decision(f, [
                RouteReason(symbol: "wrench.and.screwdriver", text: "It uses tools, which only a frontier model takes."),
                RouteReason(symbol: "smallcircle.filled.circle", text: "About \(f.credits) credits, or what your key is billed."),
            ], isPrivate: false, order: allowedFrontier)
        }

        let strongEnough = { (c: RouteCandidate) in !hard || c.minGB >= 136 }
        let freeOrder = [mac].compactMap { $0 }.filter { _ in !hard } + allowedOwn.filter(strongEnough)
        let paidOrder = (hard || rules.prefer == .quality)
            ? allowedFrontier.filter(affordable) + allowedPublic.filter(affordable)
            : allowedPublic.filter(affordable) + allowedFrontier.filter(affordable)
        let order = freeOrder + paidOrder

        // 3. This Mac, then your own pools: free, when their model is up to it
        if let pick = freeOrder.first, rules.prefer != .quality || !hard {
            let reasons = pick.destination == .thisMac
                ? [RouteReason(symbol: "desktopcomputer", text: "\(pick.model) on this Mac can answer it, free."),
                   RouteReason(symbol: "checkmark", text: "It fits: about \(facts.inputTokens.grouped) tokens in.")]
                : [RouteReason(symbol: "person.2", text: "You're in \(pick.place), so it ran there free."),
                   RouteReason(symbol: "checkmark", text: "\(pick.model) is enough for this.")]
            return decision(pick, reasons, isPrivate: false, order: order)
        }

        // 4. A frontier model, when the task needs one and your credits and cap allow it
        if hard || rules.prefer == .quality, let f = allowedFrontier.first(where: affordable) {
            return decision(f, [
                RouteReason(symbol: "sparkle", text: hard ? "This needs a frontier model." : "You prefer the best answer."),
                RouteReason(symbol: "smallcircle.filled.circle", text: "About \(f.credits) credits, within your cap of \(cap) an answer."),
                RouteReason(symbol: "arrow.up.right.square", text: "It leaves your pools and goes to \(f.place)."),
            ], isPrivate: false, order: order)
        }

        // 5. The network's best public pool
        if let p = allowedPublic.first(where: affordable) {
            var reasons = [RouteReason(symbol: "globe", text: allowedOwn.isEmpty && mac == nil
                ? "None of your own pools is lending, so it went to \(p.place)."
                : "\(p.place) runs a bigger model than your pools.")]
            reasons.append(RouteReason(symbol: "smallcircle.filled.circle", text: "\(p.credits) credits."))
            return decision(p, reasons, isPrivate: false, order: order)
        }

        // 6. Whatever is left
        if let own = mac ?? allowedOwn.first {
            return decision(own, [RouteReason(symbol: "person.2", text: own.destination == .thisMac
                ? "\(own.model) on this Mac is all that can take it now." : "You're in \(own.place), so it ran there free.")],
                isPrivate: false, order: order)
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
