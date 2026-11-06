import Foundation

/// A stand-in for the dots cloud, on this Mac. It plays the hub (pools, workers, measured compute, pool
/// answers), the dots server (people, invites, the credit ledger, badges, weekly notes) and the gateway
/// (frontier models paid in credits), and speaks the API in API.md. The app talks to it over HTTP the way it
/// will talk to the real services, so each part can be replaced by pointing the app at the real one.
///
/// Its world is the simulation, saved in Application Support/dots/mock-cloud.json. Answers are simulated,
/// unless pool answers are given a model to come from (your model in LM Studio or Ollama).
@MainActor
final class MockCloud {
    nonisolated static let defaultPort: UInt16 = 8790
    static var storeURL: URL { Simulation.supportURL("mock-cloud.json") }

    let port: UInt16
    let world: Simulation
    /// Where pool answers come from when set; otherwise they're simulated
    var upstream: () -> EndpointConfig? = { nil }
    /// For snapshots and the self-test: whether the hub has work for this Mac, and work from the wider network
    /// for a friends' pool that offers its idle hours, instead of chance
    var forcedWorking: Bool?
    var forcedNetwork: Bool?
    /// For the self-test: how much faster simulated answers stream, and how often a friend's Mac falls asleep mid-answer
    var answerSpeed = 1.0
    var sleepChance = 0.5
    private(set) var status = "Off"
    private var server: LocalGateway?
    private var working = false
    private var network = false
    private var lastActive = false
    private var notes: [WeeklyNote] = []
    private var noteSeconds: Double = 0
    private var since: Counters

    /// The totals at the last weekly note, so the next note can say what changed
    private struct Counters {
        var date: Date
        var hours: Double
        var computePF: Double
        var earned: Double
        var helped: Int
        var badgeIDs: Set<String>
    }

    init(port: UInt16 = MockCloud.defaultPort, fresh: Bool = false, persists: Bool = true) {
        self.port = port
        world = Simulation(fresh: fresh, persists: persists, store: Self.storeURL, seed: fresh ? nil : Simulation.storeURL)
        since = Counters(date: .now, hours: 0, computePF: 0, earned: 0, helped: 0, badgeIDs: [])
        seed()
        since = counters()
    }

    func start() throws {
        let server = LocalGateway(port: port) { [weak self] request, res in
            await self?.handle(request, res)
        }
        server.onState = { [weak self, port] text in
            Task { @MainActor in self?.status = text.hasPrefix("Running") ? "Running on port \(port)" : text }
        }
        try server.start()
        self.server = server
    }

    func stop() {
        server?.stop()
        server = nil
        status = "Off"
    }

    // MARK: The mock's own additions to the sample world

    /// A friends' pool you've been invited to, one Mac short of Qwen3 235B, with a boost coming;
    /// the totals behind your badges; and the nights your Mac lent before.
    private func seed() {
        world.update { s in
            if s.hoursLent == nil { s.hoursLent = 96.5 }
            if s.computePF == nil { s.computePF = 31.4 }
            if s.nights == nil { s.nights = Self.pastNights(hours: 96.5) }
            defer { Self.seedLateNightLab(&s) }
            guard !s.pools.contains(where: { $0.id == "late-night-lab" }) else { return }
            let myLend = s.thisMac.maxLendGB
            let others = min(135, max(41, 136 - myLend))
            s.people += [Person(id: "sam", name: "Sam"), Person(id: "ana", name: "Ana")]
            s.devices += [MacDevice(id: "mac-sam", ownerID: "sam", kind: "Mac Studio", memoryGB: 192),
                          MacDevice(id: "mac-ana", ownerID: "ana", kind: "MacBook Pro", memoryGB: 36)]
            let now = Date()
            let invite = Invite(code: "LATE7Q", expires: now.addingTimeInterval(7 * 86_400))
            s.pools.append(Pool(id: "late-night-lab", name: "Late Night Lab", isPublic: false,
                                blurb: "Sam and Ana's Macs, for code at night.", tags: [], memberIDs: ["sam", "ana"],
                                macs: [PoolMac(macID: "mac-sam", lendGB: others - 24, state: .lending, joinedAt: now.addingTimeInterval(-12 * 86_400)),
                                       PoolMac(macID: "mac-ana", lendGB: 24, state: .lending, joinedAt: now.addingTimeInterval(-9 * 86_400))],
                                hostID: "sam", invite: invite, phase: 0.7, createdAt: now.addingTimeInterval(-12 * 86_400),
                                speedFactor: 1.0, demand: 0.5))
            s.invitesForMe = (s.invitesForMe ?? []) + [invite.code]
        }
    }

    /// Late Night Lab's DeepSeek Boost, on a Saturday afternoon (a boost can be any time), and what happened there lately.
    private static func seedLateNightLab(_ s: inout NetworkState) {
        guard let i = s.pools.firstIndex(where: { $0.id == "late-night-lab" }) else { return }
        let now = Date()
        if s.pools[i].boost == nil {
            var c = DateComponents()
            c.weekday = 7
            c.hour = 14
            let saturday = Calendar.current.nextDate(after: now, matching: c, matchingPolicy: .nextTime) ?? now.addingTimeInterval(5 * 86_400)
            s.pools[i].boost = PoolBoost(title: "DeepSeek Boost", modelID: "deepseek-v3.1", starts: saturday, hours: 4, plannedBy: "sam",
                                         pledges: [.init(personID: "sam", gb: 96, guest: false), .init(personID: "ana", gb: 24, guest: false),
                                                   .init(personID: "nia", gb: 18, guest: true), .init(personID: "theo", gb: 96, guest: true),
                                                   .init(personID: "maya", gb: 48, guest: true), .init(personID: "priya", gb: 14, guest: true)])
        }
        if s.pools[i].activity == nil {
            s.pools[i].activity = [
                PoolActivity(id: "lnl-million", date: now.addingTimeInterval(-26 * 3_600), kind: "milestone", text: "Passed a million tokens"),
                PoolActivity(id: "lnl-boost", date: now.addingTimeInterval(-50 * 3_600), personID: "sam", kind: "boost"),
            ]
        }
    }

    /// Forty-five nights of this Mac's past, Home's first and then Studio 9's, adding up to `hours`.
    private static func pastNights(hours: Double) -> [NightLent] {
        var seed: UInt64 = 17
        func rnd() -> Double {
            seed = seed &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
            return Double(seed >> 11) / Double(UInt64(1) << 53)
        }
        let tonight = Date().nightStart
        let raw = (0..<45).map { _ in 0.6 + rnd() * 0.8 }
        let scale = hours * 60 / raw.reduce(0, +)
        return (0..<45).map { k in
            let minutes = raw[k] * scale
            return NightLent(date: Calendar.current.date(byAdding: .day, value: k - 45, to: tonight) ?? tonight,
                             poolID: k < 20 ? "home" : "studio-9", minutes: minutes, runs: Int((minutes * (1.9 + rnd() * 0.5)).rounded()))
        }
    }

    private func counters() -> Counters {
        let s = world.state
        return Counters(date: .now, hours: s.hoursLent ?? 0, computePF: s.computePF ?? 0, earned: s.earnedAllTime,
                        helped: s.questionsHelped, badgeIDs: Set(s.badges.map(\.id)))
    }

    // MARK: What the app asks for

    func sync() -> CloudSync {
        CloudSync(state: world.cloudState, terms: Pricing.terms, note: notes.last, worker: workerStatus(),
                  invites: (world.state.invitesForMe ?? []).compactMap { preview(code: $0, memoryGB: nil) }, mock: true)
    }

    /// The layers this Mac holds in its pool: a share in proportion to the memory it lends, in the order Macs joined.
    private func workerStatus() -> WorkerStatus? {
        let s = world.state
        guard let pool = s.lendingPool, let model = pool.model,
              let me = pool.macs.first(where: { $0.macID == s.thisMacID && $0.state == .lending }) else { return nil }
        var start = 0.0
        for m in pool.macs.filter({ $0.state == .lending }).sorted(by: { $0.joinedAt < $1.joinedAt }) {
            let count = Double(model.layers) * Double(m.lendGB) / Double(max(1, pool.pooledGB))
            if m.macID == me.macID {
                let first = min(model.layers - 1, Int(start.rounded()))
                let last = min(model.layers - 1, max(first, Int((start + count).rounded()) - 1))
                return WorkerStatus(poolID: pool.id, poolName: pool.name, model: model.name, firstLayer: first, lastLayer: last,
                                    layers: model.layers, working: working || network, network: network && !working)
            }
            start += count
        }
        return nil
    }

    /// An invite, as whoever opens it sees it: the pool, and what a Mac with `memoryGB` would add.
    func preview(code: String, memoryGB: Int?) -> InviteDetails? {
        let s = world.state
        guard let pool = s.pools.first(where: { $0.invite?.code.caseInsensitiveCompare(code) == .orderedSame }),
              let invite = pool.invite else { return nil }
        let add = MacDevice(id: "", ownerID: "", kind: "", memoryGB: memoryGB ?? s.thisMac.memoryGB).maxLendGB
        let after = pool.pooledGB + add
        let now = pool.model
        let then = pool.servesModelID.flatMap(Ladder.tier).flatMap { $0.minGB <= after ? $0 : nil } ?? Ladder.best(for: after)
        let unlocks = (then?.minGB ?? 0) > (now?.minGB ?? -1) ? then?.name : nil
        let next = Ladder.next(after: after)
        return InviteDetails(code: invite.code, poolID: pool.id, poolName: pool.name, blurb: pool.blurb,
                             fromName: pool.hostID.flatMap { s.person($0)?.name }, macs: pool.totalMacs, pooledGB: pool.pooledGB,
                             modelNow: now?.name, addGB: add, unlocks: unlocks, nextModel: unlocks == nil ? next?.name : nil,
                             shortGB: unlocks == nil ? next.map { $0.minGB - after } : nil, isMember: pool.isMember(s.meID))
    }

    @discardableResult
    func issueNote() -> WeeklyNote {
        let s = world.state
        let hours = max(0, (s.hoursLent ?? 0) - since.hours)
        let earned = max(0, Int((s.earnedAllTime - since.earned).rounded()))
        let spent = s.creditLog.filter { $0.date >= since.date && $0.amount < 0 }.reduce(0) { $0 - $1.amount }
        let pools = s.myPools.filter { !$0.isPublic && $0.lendingMacs > 0 }.map { pool -> WeeklyNote.PoolLine in
            let mine = pool.macs.first { $0.macID == s.thisMacID && $0.state == .lending }?.lendGB ?? 0
            let share = pool.pooledGB > 0 ? Int((Double(mine) / Double(pool.pooledGB) * 100).rounded()) : 0
            return .init(name: pool.name, answered: Int(Double(pool.lendingMacs) * pool.demand * 280), sharePercent: share)
        }
        func count(_ n: Int, _ one: String) -> String { "\(n.grouped) \(one)\(n == 1 ? "" : "s")" }
        let headline = hours >= 1
            ? "Your Mac lent \(count(Int(hours.rounded()), "hour")) this week and earned \(count(earned, "credit"))."
            : "A quiet week: your Mac lent \(count(Int((hours * 60).rounded()), "minute")) and earned \(count(earned, "credit"))."
        let note = WeeklyNote(id: "note-\(Int(Date().timeIntervalSince1970 * 1_000))", issued: .now, hoursLent: hours,
                              computePF: max(0, (s.computePF ?? 0) - since.computePF), creditsEarned: earned, creditsSpent: spent,
                              questionsHelped: max(0, s.questionsHelped - since.helped), pools: pools,
                              badges: s.badges.filter { !since.badgeIDs.contains($0.id) }.map(\.title), headline: headline)
        notes.append(note)
        since = counters()
        return note
    }

    // MARK: Requests

    func handle(_ r: HTTPRequest, _ res: HTTPResponder) async {
        if r.method == "OPTIONS" { res.empty(204); return }
        // Every call carries the app's token, as it will for the real cloud
        guard (r.bearer ?? r.headers["x-api-key"]) == AppModel.mockToken else {
            res.error(401, "Pass the cloud's token as a Bearer token.", type: "authentication_error"); return
        }
        if match(r, "GET", "/v1/state") != nil { res.encoded(200, sync()); return }
        if match(r, "POST", "/hub/v1/heartbeat") != nil { heartbeat(r, res); return }
        if match(r, "POST", "/v1/pools") != nil {
            let b = r.object
            let pool = world.createPool(name: b["name"] as? String ?? "New pool", isPublic: b["isPublic"] as? Bool ?? false, id: b["id"] as? String)
            res.encoded(200, PoolReply(pool: pool, sync: sync())); return
        }
        if let c = match(r, "POST", "/v1/pools/:id/join") {
            guard world.state.pool(c[0]) != nil else { res.error(404, "No pool \(c[0]).", type: "not_found_error"); return }
            res.encoded(200, join(c[0])); return
        }
        if let c = match(r, "POST", "/v1/pools/:id/leave") { world.leave(poolID: c[0]); res.encoded(200, sync()); return }
        if let c = match(r, "POST", "/v1/pools/:id/endorse") {
            let badge = world.endorse(poolID: c[0])
            res.encoded(200, BadgeReply(badge: badge, sync: sync())); return
        }
        if let c = match(r, "POST", "/v1/pools/:id/invite") {
            let invite = world.createInvite(poolID: c[0], code: r.object["code"] as? String)
            res.encoded(200, InviteReply(invite: invite, sync: sync())); return
        }
        if let c = match(r, "DELETE", "/v1/pools/:id/invite") { world.revokeInvite(poolID: c[0]); res.encoded(200, sync()); return }
        if let c = match(r, "POST", "/v1/pools/:id/boost/pledge") {
            guard world.state.pool(c[0])?.boost != nil else { res.error(404, "No boost planned in \(c[0]).", type: "not_found_error"); return }
            world.pledgeBoost(poolID: c[0], gb: (r.object["gb"] as? Int) ?? world.state.thisMac.maxLendGB)
            res.encoded(200, sync()); return
        }
        if let c = match(r, "DELETE", "/v1/pools/:id/boost/pledge") { world.pledgeBoost(poolID: c[0], gb: nil); res.encoded(200, sync()); return }
        if let c = match(r, "PATCH", "/v1/pools/:id") {
            if let on = r.object["offersIdleToNetwork"] as? Bool { world.setOffersIdle(poolID: c[0], on) }
            res.encoded(200, sync()); return
        }
        if let c = match(r, "GET", "/v1/invites/:code") {
            guard let p = preview(code: c[0], memoryGB: r.query["memory_gb"].flatMap { Int($0) }) else {
                res.error(404, "That invite isn't valid any more.", type: "not_found_error"); return
            }
            res.encoded(200, p); return
        }
        if let c = match(r, "POST", "/v1/invites/:code/accept") {
            guard let p = preview(code: c[0], memoryGB: nil) else { res.error(404, "That invite isn't valid any more.", type: "not_found_error"); return }
            world.update { s in s.invitesForMe?.removeAll { $0.caseInsensitiveCompare(p.code) == .orderedSame } }
            res.encoded(200, join(p.poolID)); return
        }
        if match(r, "PATCH", "/v1/me") != nil {
            if let name = r.object["name"] as? String { world.rename(name) }
            res.encoded(200, sync()); return
        }
        if match(r, "POST", "/v1/badges/seen") != nil { world.markBadgesSeen(); res.encoded(200, sync()); return }
        if match(r, "GET", "/v1/pricing") != nil { res.encoded(200, Pricing.terms); return }
        if match(r, "GET", "/v1/notes/latest") != nil {
            guard let note = notes.last else { res.error(404, "No weekly note yet.", type: "not_found_error"); return }
            res.encoded(200, note); return
        }
        if match(r, "POST", "/v1/mock/note") != nil { res.encoded(200, issueNote()); return }
        if match(r, "POST", "/v1/mock/reset") != nil {
            world.reset()
            seed()
            notes = []
            since = counters()
            res.encoded(200, sync()); return
        }
        if let c = match(r, "POST", "/hub/v1/pools/:id/chat/completions") { await poolAnswer(c[0], r, res); return }
        if match(r, "POST", "/gateway/v1/chat/completions") != nil { await gateway(r, res, format: .openai); return }
        if match(r, "POST", "/gateway/v1/messages") != nil { await gateway(r, res, format: .anthropic); return }
        res.error(404, "The mock cloud doesn't serve \(r.method) \(r.path).", type: "not_found_error")
    }

    /// `/v1/pools/:id/join` against a path: the values in place of each `:name`, or nil.
    private func match(_ r: HTTPRequest, _ method: String, _ pattern: String) -> [String]? {
        guard r.method == method else { return nil }
        let have = r.path.split(separator: "/"), want = pattern.split(separator: "/")
        guard have.count == want.count else { return nil }
        var values: [String] = []
        for (h, w) in zip(have, want) {
            if w.hasPrefix(":") { values.append(String(h).removingPercentEncoding ?? String(h)) } else if h != w { return nil }
        }
        return values
    }

    private func join(_ poolID: String) -> JoinReply {
        let outcome = world.join(poolID: poolID)
        let unlock = world.applyLendingStatus(active: lastActive)
        return JoinReply(macNumber: outcome.macNumber, poolID: poolID, unlock: unlock, sync: sync())
    }

    /// The worker's report: lend or not, and how much. The hub hands out work in bursts, and a friends' pool that
    /// offers its idle hours gets the network's work in between. Time runs as fast as the app's demo speed.
    private func heartbeat(_ r: HTTPRequest, _ res: HTTPResponder) {
        guard let beat = try? JSONDecoder.dots.decode(Heartbeat.self, from: r.body) else { res.error(400, "Send a heartbeat."); return }
        world.update { s in
            if let i = s.devices.firstIndex(where: { $0.id == s.thisMacID }) {
                s.devices[i].kind = beat.macKind
                s.devices[i].memoryGB = beat.memoryGB
            }
        }
        world.updateLending { $0.limitGB = beat.lendGB }
        lastActive = beat.active
        let unlock = world.applyLendingStatus(active: beat.active)
        if beat.active, let pool = world.state.lendingPool {
            if let forced = forcedWorking { working = forced } else if Double.random(in: 0...1) < 0.2 { working.toggle() }
            if working || pool.isPublic || !pool.offersIdleToNetwork {
                network = false
            } else if let forced = forcedNetwork {
                network = forced
            } else if Double.random(in: 0...1) < 0.2 {
                network.toggle()
            }
        } else {
            working = false
            network = false
        }
        world.tick(seconds: beat.seconds, lendingActive: beat.active, working: working || network, network: network && !working)
        noteSeconds += beat.seconds
        if noteSeconds >= 7 * 86_400 {
            noteSeconds = 0
            issueNote()
        }
        res.encoded(200, HeartbeatReply(sync: sync(), unlock: unlock))
    }

    /// A pool's answer, in the OpenAI format, with the Macs answering and a Mac falling asleep now and then.
    private func poolAnswer(_ poolID: String, _ r: HTTPRequest, _ res: HTTPResponder) async {
        let s = world.state
        guard let pool = s.pool(poolID), let model = pool.model, pool.lendingMacs > 0 else {
            res.error(404, "No pool \(poolID) is running a model right now.", type: "not_found_error"); return
        }
        guard pool.isPublic || pool.isMember(s.meID) else { res.error(403, "You're not in \(pool.name).", type: "permission_error"); return }
        guard let body = ParsedRequest.parse(r.body, format: .openai) else { res.error(400, "Send a JSON body with a messages array."); return }
        let credits = pool.isPublic ? model.credits : 0
        guard Double(credits) <= s.credits else {
            res.error(402, "This answer costs \(credits) credits, and you have \(Int(s.credits)).", type: "insufficient_credits"); return
        }
        let lenders = pool.macs.filter { $0.state == .lending }
        let macs: [[String: String]] = lenders.compactMap { m in
            guard let d = s.device(m.macID), let p = s.person(d.ownerID) else { return nil }
            let you = d.id == s.thisMacID
            return ["id": m.macID, "name": you ? "You" : p.name, "initial": you ? "You" : p.initial]
        }
        let others = lenders.filter { $0.macID != s.thisMacID }
        var sleeper: (id: String, name: String)?
        if !pool.isPublic, others.count >= 3, Double.random(in: 0...1) < sleepChance, let m = others.last,
           let name = s.device(m.macID).flatMap({ s.person($0.ownerID) })?.name {
            sleeper = (m.macID, name)
        }
        let source = upstream()
        let simulated = source == nil
        let words = source.map { OpenAIClient.stream($0, messages: body.messages) }
            ?? SimulatedAnswers.stream(SimulatedAnswers.answer(for: body.lastUserText, documentName: nil), tokPerSec: Double(pool.tokPerSec) * answerSpeed)
        let id = "chatcmpl-dots-" + Simulation.randomCode(12)
        let input = body.inputTokens
        var text = ""
        if body.stream {
            res.startStream()
            res.event(OpenAIChunks.chunk(id: id, model: model.id, extra: ["dots": ["event": "macs", "macs": macs, "pool": pool.name,
                                                                                   "model": model.name, "simulated": simulated]]))
        }
        var pieces = 0
        do {
            for try await event in words {
                guard case .text(let t) = event else { continue }
                text += t
                pieces += 1
                guard body.stream else { continue }
                res.event(OpenAIChunks.chunk(id: id, model: model.id, text: t))
                if let mac = sleeper, pieces == 12 {
                    sleeper = nil
                    res.event(OpenAIChunks.chunk(id: id, model: model.id, extra: ["dots": ["event": "asleep", "mac_id": mac.id, "name": mac.name]]))
                    world.setMemberAsleep(macID: mac.id, poolID: pool.id, asleep: true)
                    try? await Task.sleep(for: .milliseconds(900))
                    res.event(OpenAIChunks.chunk(id: id, model: model.id, extra: ["dots": ["event": "resumed"]]))
                }
            }
        } catch {
            if body.stream { res.event(["error": ["message": error.localizedDescription]]); res.finishStream() } else {
                res.error(502, error.localizedDescription, type: "upstream_error")
            }
            return
        }
        if credits > 0 { world.spend(credits, note: "\(model.name) in \(pool.name)") }
        let output = Pricing.tokens(text)
        let dots: [String: Any] = ["credits": credits, "simulated": simulated, "pool": pool.name]
        if body.stream {
            res.event(OpenAIChunks.chunk(id: id, model: model.id, finish: "stop",
                                         extra: ["usage": ["prompt_tokens": input, "completion_tokens": output, "total_tokens": input + output], "dots": dots]))
            res.finishStream()
        } else {
            res.json(200, OpenAIChunks.completion(model: model.id, text: text, input: input, output: output,
                                                  extra: ["dots": dots.merging(["macs": macs]) { a, _ in a }]))
        }
    }

    /// Frontier models paid in credits: list price less the discount. Simulated: there's no model behind the mock.
    private func gateway(_ r: HTTPRequest, _ res: HTTPResponder, format: APIFormat) async {
        guard let body = ParsedRequest.parse(r.body, format: format) else {
            res.apiError(format, 400, "Send a JSON body with a messages array."); return
        }
        let f = FrontierModel.named(body.model) ?? .opus
        let text = SimulatedAnswers.frontier(for: body.lastUserText, model: f.name)
        let input = body.inputTokens, output = Pricing.tokens(text)
        let credits = Pricing.credits(f.id, input: input, output: output)
        let list = Pricing.listCredits(f.id, input: input, output: output)
        guard Double(credits) <= world.state.credits else {
            res.apiError(format, 402, "This answer costs \(credits) credits through the gateway, and you have \(Int(world.state.credits)). Lend a little longer, or use your own key.",
                         type: "insufficient_credits")
            return
        }
        world.spend(credits, note: "\(f.name) through the dots gateway, \(Int((Pricing.terms.discount * 100).rounded()))% off")
        let dots: [String: Any] = ["credits": credits, "list_credits": list, "discount": Pricing.terms.discount, "simulated": true]
        let words = SimulatedAnswers.stream(text, tokPerSec: f.tokPerSec * 4 * answerSpeed)
        switch (format, body.stream) {
        case (.openai, true):
            let id = "chatcmpl-dots-" + Simulation.randomCode(12)
            res.startStream()
            do { for try await event in words { if case .text(let t) = event { res.event(OpenAIChunks.chunk(id: id, model: f.id, text: t)) } } } catch {}
            res.event(OpenAIChunks.chunk(id: id, model: f.id, finish: "stop",
                                         extra: ["usage": ["prompt_tokens": input, "completion_tokens": output, "total_tokens": input + output], "dots": dots]))
            res.finishStream()
        case (.openai, false):
            res.json(200, OpenAIChunks.completion(model: f.id, text: text, input: input, output: output, extra: ["dots": dots]))
        case (.anthropic, true):
            let stream = AnthropicStream(res: res, model: f.id)
            stream.start(inputTokens: input, headers: ["x-dots-credits": "\(credits)", "x-dots-simulated": "true"])
            do { for try await event in words { if case .text(let t) = event { stream.text(t) } } } catch {}
            stream.finish(outputTokens: output)
        case (.anthropic, false):
            res.json(200, AnthropicStream.message(model: f.id, text: text, input: input, output: output, extra: ["dots": dots]))
        }
    }
}

extension HTTPRequest {
    /// The body as a JSON object, or empty.
    var object: [String: Any] { ((try? JSONSerialization.jsonObject(with: body)) as? [String: Any]) ?? [:] }
}

extension HTTPResponder {
    func encoded<T: Encodable>(_ status: Int, _ value: T) {
        raw(status, contentType: "application/json", (try? JSONEncoder.dots.encode(value)) ?? Data("{}".utf8))
    }
}
