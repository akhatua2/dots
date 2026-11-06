import Foundation

/// `dots --selftest` checks every user story end to end. It runs a mock cloud of its own (fresh, never saved),
/// the local API on a spare port, and stand-ins for a model on this Mac and a provider behind your own key,
/// then calls them the way other apps would. Nothing is saved, and no real key is used.
@MainActor
enum SelfTest {
    private static var failures = 0
    private static var passes = 0
    /// Requests the stand-in provider (your own frontier key) answered
    private static var providerHits = 0

    /// What every story's checks share
    private struct Rig {
        let model: AppModel
        let cloud: MockCloud
        let base: URL
        let secret: String
        let keyID: String
        let macPort: UInt16
        let providerPort: UInt16
        let thisMac: LocalGateway
    }

    static func run() async -> Bool {
        // A mock cloud of its own, with answers sped up and no friend's Mac falling asleep mid-answer
        let cloud = MockCloud(port: UInt16.random(in: 19_500..<19_700), fresh: true, persists: false)
        cloud.answerSpeed = 20
        cloud.sleepChance = 0
        var settings = AppSettings()
        settings.gatewayPort = Int.random(in: 18_700..<18_900)
        settings.poolEndpoint.enabled = false
        settings.frontierEndpoint.enabled = false
        settings.cloud.mockPort = Int(cloud.port)
        let client = CloudClient(baseURL: URL(string: "http://127.0.0.1:\(cloud.port)")!, token: AppModel.mockToken)
        let model = AppModel(backend: CloudBackend(client: client, persists: false), settings: settings)
        model.persistsSettings = false
        model.useMock(cloud)
        model.signals.onPower = true
        model.signals.idleSeconds = 999

        // Stand-ins for LM Studio on this Mac, and for a provider your own frontier key reaches
        let macPort = UInt16.random(in: 19_200..<19_300)
        let providerPort = UInt16.random(in: 19_300..<19_400)
        let thisMac = LocalGateway(port: macPort) { r, res in
            await SelfTest.standIn(r, res, text: "From the model on this Mac.", usage: nil, counts: false)
        }
        let provider = LocalGateway(port: providerPort) { r, res in
            await SelfTest.standIn(r, res, text: "From your own key.", usage: (1_500, 600), counts: true)
        }
        do {
            try cloud.start()
            try thisMac.start()
            try provider.start()
        } catch {
            check("The mock cloud and the stand-ins start", false)
            return false
        }
        defer { cloud.stop(); thisMac.stop(); provider.stop() }
        model.restartGateway()
        try? await Task.sleep(for: .milliseconds(400))
        await model.backend.syncNow()
        let base = URL(string: "http://127.0.0.1:\(settings.gatewayPort)/v1")!
        print("dots self-test: the local API on \(base.absoluteString), a mock cloud of its own on port \(cloud.port). Nothing is saved.")
        check("The app reaches the mock cloud", model.backend.isConnected && model.backend.isMock)

        let made = model.backend.createKey(name: "Self-test", reach: .all, monthlyLimit: nil)
        let rig = Rig(model: model, cloud: cloud, base: base, secret: made.secret, keyID: made.key.id,
                      macPort: macPort, providerPort: providerPort, thisMac: thisMac)
        await routerStories(rig)
        if model.state.thisMac.maxLendGB > 0 {
            await lendingStories(rig)
            await communityStories(rig)
        } else {
            print("\nThis Mac can't lend (it needs more than 8 GB of memory), so stories 7 to 17 can't run here.")
        }
        if let url = ProcessInfo.processInfo.environment["DOTS_SELFTEST_ENDPOINT"] {
            await realAnswers(url, rig)
        }
        await workspaceChecks()
        answerFormatting()

        print("\n\(passes) passed, \(failures) failed.")
        return failures == 0
    }

    /// A question about a photo is personal, so it stays in your pools, and only goes to a model that reads images.
    private static func pictureStories(_ model: AppModel) {
        let photo = RequestFacts(inputTokens: 40, hasImages: true)
        let studio = model.state.myPools.first { $0.name == "Studio 9" }
        let ask = { Router.decide(question: "What's in this photo?", hasDocument: true, state: model.state,
                                  rules: model.settings.rules, facts: photo) }
        let noReader = ask()
        check("A photo stays private, and with no pool of yours that reads images it asks first",
              noReader.chosen == nil && noReader.needsConsent?.contains("reads images") == true)
        let saved = LadderData(tiers: Ladder.tiers, special: Ladder.special)
        Ladder.adopt(LadderData(tiers: Ladder.tiers.map { var t = $0; t.readsImages = t.id == studio?.model?.id; return t },
                                special: Ladder.special))
        let reader = ask()
        check("Once Studio 9's model reads images, the photo goes there, free and private",
              reader.chosen?.poolID == studio?.id && reader.isPrivate && reader.chosen?.credits == 0)
        Ladder.adopt(saved)
        let sent = (try? JSONEncoder().encode(ChatMessage(role: "user", content: "What is this?", images: [Data([1, 2, 3])])))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let parts = sent?["content"] as? [[String: Any]]
        check("A question with a photo goes out as an image part, then its text",
              parts?.first?["type"] as? String == "image_url" && parts?.last?["text"] as? String == "What is this?")
    }

    // MARK: Stories 1 to 6: the router and the endpoint

    private static func routerStories(_ rig: Rig) async {
        let model = rig.model, base = rig.base, secret = rig.secret

        story(1, "One address and one key, in OpenAI's format or Anthropic's")
        let list = await call(base, "GET", "/models")
        let ids = ((list.json?["data"] as? [[String: Any]]) ?? []).compactMap { $0["id"] as? String }
        check("GET /v1/models lists auto, the pools' models and Claude, without a key",
              list.code == 200 && ids.contains("auto") && ids.contains("glm-5.3") && ids.contains("claude-opus-5-5"))
        let noKey = await call(base, "POST", "/chat/completions", body: ask("hi"))
        check("Without a key, OpenAI's format gets a 401 in OpenAI's error shape", noKey.code == 401 && noKey.errorType == "authentication_error")
        let noKeyA = await call(base, "POST", "/messages", format: .anthropic, body: ask("hi", format: .anthropic))
        check("...and Anthropic's format gets one in Anthropic's",
              noKeyA.code == 401 && noKeyA.json?["type"] as? String == "error" && noKeyA.errorType == "authentication_error")
        check("A new key starts with dots_sk_live_", secret.hasPrefix("dots_sk_live_"))
        let bad = await call(base, "POST", "/chat/completions", key: secret, raw: Data("{nope".utf8))
        check("A body that isn't JSON is refused (400)", bad.code == 400)
        let plain = await call(base, "POST", "/chat/completions", key: secret, body: ask("What's a good name for a cat?"))
        let usage = plain.json?["usage"] as? [String: Any]
        check("OpenAI's format: an answer, with its token usage", plain.code == 200 && !plain.answer.isEmpty && (usage?["prompt_tokens"] as? Int ?? 0) > 0)
        let msg = await call(base, "POST", "/messages", key: secret, format: .anthropic, body: ask("What's a good name for a dog?", format: .anthropic))
        let msgUsage = msg.json?["usage"] as? [String: Any]
        check("Anthropic's format, with the key in x-api-key: a message, with its usage",
              msg.code == 200 && msg.json?["type"] as? String == "message" && !msg.answer.isEmpty && (msgUsage?["input_tokens"] as? Int ?? 0) > 0)
        let msgStream = await call(base, "POST", "/messages", key: secret, format: .anthropic,
                                   body: ask("And a name for a fish?", format: .anthropic, stream: true))
        let names = events(msgStream.body).compactMap { $0.name }
        check("Anthropic's format, streamed: the Messages events in order, then dots's receipt",
              msgStream.code == 200 && names.first == "message_start" && names.contains("content_block_delta")
              && Array(names.suffix(4)) == ["content_block_stop", "message_delta", "message_stop", "dots"] && !anthropicText(msgStream.body).isEmpty)
        let openStream = await call(base, "POST", "/chat/completions", key: secret, body: ask("Say hello", stream: true))
        let dataLines = openStream.body.components(separatedBy: "\n").filter { $0.hasPrefix("data: ") }
        let lastChunk = events(openStream.body).last?.data
        check("OpenAI's format, streamed: chunks, a stop, a receipt chunk, then data: [DONE]",
              openStream.code == 200 && dataLines.last == "data: [DONE]" && openStream.body.contains("\"finish_reason\":\"stop\"")
              && !openAIText(openStream.body).isEmpty && lastChunk?["dots"] != nil && (lastChunk?["choices"] as? [Any])?.isEmpty == true)
        let count = await call(base, "POST", "/messages/count_tokens", key: secret, format: .anthropic, body: ask("Count these words.", format: .anthropic))
        check("Anthropic's count_tokens answers", count.code == 200 && (count.json?["input_tokens"] as? Int ?? 0) > 0)
        let unknown = await call(base, "GET", "/embeddings")
        check("Anything else is a 404", unknown.code == 404)

        story(2, "auto sends each request to the cheapest place that can answer it well; a model can still be pinned")
        check("An everyday question runs free in your own pool", plain.text("ran_on").contains("Studio 9") && plain.int("credits") == 0)
        print("      ran on \(plain.text("ran_on")), \(plain.text("model"))")
        let hard = await call(base, "POST", "/chat/completions", key: secret, body: ask("Design a caching layer for our API, step by step."))
        check("So does a hard one, while the pool runs a model big enough for it", hard.text("ran_on").contains("Studio 9") && hard.int("credits") == 0)
        model.settings.rules.prefer = .quality
        let best = await call(base, "POST", "/chat/completions", key: secret, body: ask(report + "Design a caching layer for this API, step by step."))
        model.settings.rules.prefer = .balanced
        check("Preferring the best answer sends it to Claude instead", best.code == 200 && best.text("ran_on") == "Anthropic" && best.int("credits") > 0)
        let tools = await call(base, "POST", "/chat/completions", key: secret, body: ask("What's the weather in Lisbon?", extra: ["tools": [weatherTool]]))
        check("A request with tools goes to a frontier model, the only kind that takes them", tools.code == 200 && tools.text("ran_on") == "Anthropic")
        pictureStories(model)
        let kimi = await call(base, "POST", "/chat/completions", key: secret, body: ask("Summarise this paper.", model: "kimi-k2.5"))
        let fastest = model.state.publicPools.filter { $0.model?.id == "kimi-k2.5" && $0.lendingMacs > 0 }.max { $0.tokPerSec < $1.tokPerSec }
        check("Pinning kimi-k2.5 runs it in the fastest public pool that has it (\(fastest?.name ?? "none")), for its credits",
              kimi.code == 200 && fastest != nil && kimi.text("ran_on").contains(fastest?.name ?? "?") && kimi.int("credits") == Ladder.tier("kimi-k2.5")?.credits)
        model.settings.poolEndpoint = EndpointConfig(enabled: true, label: "LM Studio", baseURL: "http://127.0.0.1:\(rig.macPort)/v1",
                                                     model: "qwen3-8b", keychainAccount: "dots-selftest-none")
        let local = await call(base, "POST", "/chat/completions", key: secret, body: ask("What's a good name for a cat?"))
        check("With a model on this Mac, an everyday question runs there: free, and private",
              local.text("ran_on") == "This Mac" && local.int("credits") == 0 && local.flag("private") && local.answer == "From the model on this Mac.")
        let hardLocal = await call(base, "POST", "/chat/completions", key: secret, body: ask("Design a caching layer for our API, step by step."))
        check("...but a hard question still goes to a bigger model", hardLocal.text("ran_on").contains("Studio 9"))
        let pinnedLocal = await call(base, "POST", "/chat/completions", key: secret, body: ask("Hard or not, answer here.", model: "qwen3-8b"))
        check("Pinning the model on this Mac runs it there", pinnedLocal.text("ran_on") == "This Mac")

        story(3, "Every response says where it ran, why, and what it cost")
        let fields = ["ran_on", "model", "why", "credits", "usd", "saved_usd", "compared_with", "private", "simulated", "input_tokens", "output_tokens"]
        check("An answer carries a dots receipt with \(fields.count) fields", fields.allSatisfy { plain.dots[$0] != nil })
        let why = plain.dots["why"] as? [String] ?? []
        check("...the reasons, ending with what answered it", why.count >= 2 && why.last?.hasPrefix("Answered through") == true)
        print("      why: \(why.joined(separator: " "))")
        check("...and headers, for clients that only read those", plain.header("x-dots-route") == "Studio 9" && plain.header("x-dots-private") == "false")
        let anthropicReceipt = events(msgStream.body).last { $0.name == "dots" }?.data["dots"] as? [String: Any]
        check("A streamed answer ends with the receipt too, in either format", lastChunk?["dots"] != nil && anthropicReceipt?["ran_on"] != nil)
        check("A simulated answer says so, and a real one doesn't", plain.flag("simulated") && local.dots["simulated"] as? Bool == false)

        story(4, "Savings each month, against the model you'd otherwise send everything to")
        let plainBaseline = Pricing.usd(FrontierModel.opus.id, input: plain.int("input_tokens"), output: plain.int("output_tokens"))
        check("A free answer saved what Claude Opus 5.5 would have cost for its tokens (\(plainBaseline.dollars))",
              plain.text("compared_with") == FrontierModel.opus.id && plainBaseline > 0 && abs(plain.number("saved_usd") - plainBaseline) < 1e-9)
        let bestBaseline = Pricing.usd(FrontierModel.opus.id, input: best.int("input_tokens"), output: best.int("output_tokens"))
        check("A gateway answer saved the discount", best.number("saved_usd") > 0 && best.number("saved_usd") < bestBaseline)
        let booked = model.state.requests.filter { $0.source == "Self-test" }
        check("Each request is booked with its tokens and what it saved", booked.count >= 10 && booked.allSatisfy { $0.baselineUSD != nil && $0.inputTokens != nil })
        let month = model.monthRequests.reduce(0) { $0 + $1.savedUSD }
        check("The month's savings add up on the API page: \(month.dollars)", month > 0)
        model.settings.savingsBaseline = FrontierModel.sonnet.id
        let vsSonnet = await call(base, "POST", "/chat/completions", key: secret, body: ask("What's a good name for a cat?"))
        model.settings.savingsBaseline = FrontierModel.opus.id
        let sonnetBaseline = Pricing.usd(FrontierModel.sonnet.id, input: vsSonnet.int("input_tokens"), output: vsSonnet.int("output_tokens"))
        check("Comparing with Claude Sonnet 5.5 instead measures against it",
              vsSonnet.text("compared_with") == FrontierModel.sonnet.id && abs(vsSonnet.number("saved_usd") - sonnetBaseline) < 1e-9)

        await keyStories(rig)

        story(6, "When a place doesn't answer, the request goes to the next")
        rig.thisMac.stop()
        try? await Task.sleep(for: .milliseconds(300))
        let fell = await call(base, "POST", "/chat/completions", key: secret, body: ask("What's a good name for a horse?"))
        check("This Mac's model is down, so it goes to your pool, and says why",
              fell.code == 200 && fell.text("ran_on").contains("Studio 9") && fell.text("fallback").contains("This Mac didn't answer")
              && fell.header("x-dots-fallback") != nil)
        print("      \(fell.text("fallback"))")
        let next = await call(base, "POST", "/chat/completions", key: secret, body: ask("And a name for a goat?"))
        check("For the next minute it doesn't try this Mac first", next.text("ran_on").contains("Studio 9") && next.dots["fallback"] == nil)
        check("The request log keeps the fallback", model.state.requests.last { $0.fallback != nil }?.place == "Studio 9")
        model.settings.poolEndpoint.enabled = false
        model.localDownUntil = nil
    }

    /// Story 5: what each key may do
    private static func keyStories(_ rig: Rig) async {
        let model = rig.model, base = rig.base, secret = rig.secret, cloud = rig.cloud
        story(5, "Rules for each key: private only, a cap in dollars, where it may go")
        let poolsOnly = model.backend.createKey(name: "Pools only", reach: .yourPools, monthlyLimit: nil).secret
        let reach = await call(base, "POST", "/chat/completions", key: poolsOnly, body: ask("hi", model: "claude-opus-5-5"))
        check("A key limited to your pools can't name Claude (400)", reach.code == 400)
        model.createKey(name: "Private", reach: .all, monthlyLimit: nil, privateOnly: true)
        let privateKey = model.revealedKey?.secret ?? ""
        let priv = await call(base, "POST", "/chat/completions", key: privateKey, body: ask("What's a good name for a cat?"))
        check("A private key's everyday question stays on this Mac, marked private",
              priv.code == 200 && priv.flag("private") && priv.text("ran_on") == "This Mac")
        let privClaude = await call(base, "POST", "/chat/completions", key: privateKey, body: ask("hi", model: "claude-opus-5-5"))
        check("...it can't name Claude (400)", privClaude.code == 400)
        let privTools = await call(base, "POST", "/chat/completions", key: privateKey, body: ask("What's the weather?", extra: ["tools": [weatherTool]]))
        check("...and a request with tools, which only Claude takes, is refused (409)", privTools.code == 409)
        let capped = model.backend.createKey(name: "Five credits", reach: .all, monthlyLimit: 5).secret
        let over = await call(base, "POST", "/chat/completions", key: capped, body: ask("hi", model: "claude-opus-5-5"))
        check("A key over its monthly credits is refused (429)", over.code == 429)

        // Your own key, at a provider stand-in, billed in dollars
        model.settings.frontierEndpoint = EndpointConfig(enabled: true, label: "Provider", baseURL: "http://127.0.0.1:\(rig.providerPort)/v1",
                                                         model: "claude-opus-5-5", keychainAccount: "dots-selftest-none")
        model.settings.payFrontierWith = .key
        model.createKey(name: "A cent on my key", reach: .all, monthlyLimit: nil, monthlyCapUSD: 0.01)
        let centKey = model.revealedKey?.secret ?? ""
        providerHits = 0
        let mine = await call(base, "POST", "/chat/completions", key: centKey, body: ask("hi", model: "claude-opus-5-5"))
        check("Paying with your key first: Claude answers on your key, billed in dollars, no credits",
              mine.code == 200 && mine.answer == "From your own key." && mine.number("usd") > 0 && mine.int("credits") == 0 && providerHits == 1)
        let used = model.state.apiKeys.first { $0.name == "A cent on my key" }?.usdThisMonth ?? 0
        let past = await call(base, "POST", "/chat/completions", key: centKey, body: ask("hi again", model: "claude-opus-5-5"))
        check("Past the key's $0.01 cap on your key (\(used.dollars) used), it goes through the gateway in credits",
              past.code == 200 && past.int("credits") > 0 && past.number("usd") == 0 && providerHits == 1)
        model.settings.payFrontierWith = .credits
        let first = await call(base, "POST", "/chat/completions", key: secret, body: ask("hi", model: "claude-opus-5-5"))
        check("Paying with credits first: the gateway, while credits last", first.int("credits") > 0 && providerHits == 1)
        cloud.world.update { $0.credits = 3 }
        await model.backend.syncNow()
        let short = await call(base, "POST", "/chat/completions", key: secret, body: ask("hi", model: "claude-opus-5-5"))
        check("...and your own key once they run short", short.code == 200 && short.number("usd") > 0 && providerHits == 2)
        cloud.world.update { $0.credits = 1_000 }
        await model.backend.syncNow()
        model.settings.frontierEndpoint.enabled = false
    }

    // MARK: Stories 7 to 11: lending and credits

    private static func lendingStories(_ rig: Rig) async {
        let model = rig.model, cloud = rig.cloud, base = rig.base, secret = rig.secret
        let me = model.state.thisMacID
        func mine(_ pool: String) -> PoolMac? { cloud.world.state.pool(pool)?.macs.first { $0.macID == me } }

        story(7, "Lend on your terms: dots starts and stops the worker, and tells the hub")
        model.join(model.state.pool("studio-9")!)
        await model.backend.settled()
        await model.backend.beatNow(active: model.lendingStatus.isActive)
        check("Lending to Studio 9: the hub has this Mac lending \(model.state.lending.limitGB) GB",
              mine("studio-9")?.state == .lending && mine("studio-9")?.lendGB == model.state.lending.limitGB)
        let worker = model.backend.worker
        check("The hub gives this Mac its share of the model's layers",
              worker?.poolName == "Studio 9" && worker?.layers == model.state.pool("studio-9")?.model?.layers && (worker.map { $0.lastLayer >= $0.firstLayer } ?? false))
        print("      \(worker?.line ?? "no worker")")
        model.pauseForAnHour()
        await model.backend.beatNow(active: model.lendingStatus.isActive)
        check("Pausing for an hour stops the worker: the hub has this Mac online, not lending",
              mine("studio-9")?.state == .online && model.backend.worker == nil)
        model.resume()
        await model.backend.beatNow(active: model.lendingStatus.isActive)
        check("Resuming starts it again", mine("studio-9")?.state == .lending && model.backend.worker != nil)
        model.signals.onPower = false
        model.refreshLending()
        await model.backend.beatNow(active: model.lendingStatus.isActive)
        check("Unplugged, it waits for power, as set", model.lendingStatus == .waitingForPower && mine("studio-9")?.state == .online)
        model.signals.onPower = true
        model.refreshLending()
        await model.backend.beatNow(active: model.lendingStatus.isActive)
        check("Plugged back in, it lends again", mine("studio-9")?.state == .lending)

        story(8, "Credits for measured work, every entry visible")
        cloud.forcedWorking = true
        let before = cloud.world.state
        await model.backend.beatNow(active: true, adding: 3_600)
        let after = cloud.world.state
        let studio = after.pool("studio-9")!
        let lend = after.lending.limitGB
        let rate = studio.creditsPerHour(lendGB: lend)
        let compute = Simulation.computePF(pool: studio, lendGB: lend, seconds: 3_600)
        let entry = after.creditLog.first
        check("An hour of work earns this Mac's rate in Studio 9: \(rate) credits", abs(after.credits - before.credits - Double(rate)) < 0.01)
        check("It's one ledger entry, with the hour and the compute the hub measured: \(String(format: "%.2f", compute)) PF",
              entry?.kind == "lend" && entry?.poolID == "studio-9" && abs((entry?.minutes ?? 0) - 60) < 0.01
              && abs((entry?.computePF ?? 0) - compute) < 1e-6 && compute > 0)
        check("The app shows the same entry", model.state.creditLog.first?.id == entry?.id)
        check("Hours lent go up by one", abs((after.hoursLent ?? 0) - (before.hoursLent ?? 0) - 1) < 1e-6)

        story(9, "Spend credits on Claude at a discount, with no key of your own")
        let balance = cloud.world.state.credits
        let opus = await call(base, "POST", "/chat/completions", key: secret, body: ask(report + "What should we fix first?", model: "claude-opus-5-5"))
        let usage = opus.json?["usage"] as? [String: Any]
        let input = usage?["prompt_tokens"] as? Int ?? 0, output = usage?["completion_tokens"] as? Int ?? 0
        let charged = opus.int("credits"), list = Pricing.listCredits(FrontierModel.opus.id, input: input, output: output)
        check("Naming claude-opus-5-5 goes through the dots gateway",
              opus.code == 200 && opus.text("ran_on") == "Anthropic" && (opus.dots["why"] as? [String])?.last?.contains("gateway") == true)
        check("It costs list price less 30%, in whole credits: \(charged) instead of \(list) for \(input.grouped) tokens in and \(output) out",
              charged == Pricing.credits(FrontierModel.opus.id, input: input, output: output) && charged < list)
        let spent = cloud.world.state.creditLog.first
        check("The credits come off your balance as a ledger entry",
              abs(balance - cloud.world.state.credits - Double(charged)) < 0.001 && spent?.amount == -charged && spent?.kind == "spend")
        check("The key shows what it spent", (model.state.apiKeys.first { $0.id == rig.keyID }?.creditsThisMonth ?? 0) >= charged)
        let viaMessages = await call(base, "POST", "/messages", key: secret, format: .anthropic,
                                     body: ask("Write a haiku about Macs.", model: "claude-sonnet-5-5", format: .anthropic, stream: true))
        let receipt = events(viaMessages.body).last { $0.name == "dots" }?.data["dots"] as? [String: Any]
        check("In Anthropic's format too, streamed from the gateway, with the credits in the receipt",
              viaMessages.code == 200 && !anthropicText(viaMessages.body).isEmpty && (receipt?["credits"] as? Int ?? 0) > 0)
        cloud.world.update { $0.credits = 0 }
        await model.backend.syncNow()
        let broke = await call(base, "POST", "/chat/completions", key: secret, body: ask("hi", model: "claude-opus-5-5"))
        check("With no credits and no key of your own, the gateway says so (402)", broke.code == 402 && broke.errorType == "insufficient_credits")
        cloud.world.update { $0.credits = 1_245 }
        await model.backend.syncNow()

        story(10, "Credits in plain terms")
        let terms: Terms? = try? await model.backend.client.get("v1/pricing")
        check("The cloud sends its terms: a credit is worth $0.001, and Claude is 30% off", terms == Pricing.terms && terms?.discount == 0.30)
        check("An everyday Claude Opus 5.5 answer is \(FrontierModel.opus.credits) credits (\(FrontierModel.opus.listCredits) at list price)",
              FrontierModel.opus.credits == 13 && FrontierModel.opus.listCredits == 18)
        let credits = model.state.credits
        check("So \(Int(credits).grouped) credits is about \(Pricing.answers(credits).grouped) answers, worth \(Pricing.dollars(credits: credits).dollars)",
              Pricing.answers(credits) == Int(credits) / 13 && abs(Pricing.dollars(credits: credits) - credits / 1_000) < 1e-9)

        story(11, "A friends' pool can offer its idle hours to the network")
        cloud.forcedWorking = false
        cloud.forcedNetwork = true
        await model.backend.beatNow(active: true, adding: 1_800)
        let network = cloud.world.state.creditLog.first
        check("While Studio 9 has no work of its own, this Mac works for the network through it, and earns",
              model.backend.worker?.network == true && network?.kind == "network" && network?.note == "Network work through Studio 9"
              && (network?.amount ?? 0) > 0 && (network?.computePF ?? 0) > 0)
        model.backend.setOffersIdle(poolID: "studio-9", false)
        await model.backend.settled()
        check("Switching it off reaches the cloud", cloud.world.state.pool("studio-9")?.offersIdleToNetwork == false)
        await model.backend.beatNow(active: true, adding: 600)
        let idle = cloud.world.state.creditLog.first
        check("...and then idle time earns only the waiting rate, with no network work",
              model.backend.worker?.network == false && idle?.kind == "lend" && idle?.computePF == 0)
        model.backend.setOffersIdle(poolID: "studio-9", true)
        await model.backend.settled()
        cloud.forcedNetwork = nil
    }

    // MARK: Stories 12 to 17: the community

    private static func communityStories(_ rig: Rig) async {
        let model = rig.model, cloud = rig.cloud
        let me = model.state.thisMacID
        func badges() -> Set<String> { Set(cloud.world.state.badges.map(\.id)) }

        story(12, "Joining a pool is a moment")
        let union = model.state.pool("student-union")!
        model.join(union)
        let joined = joinedMoment(model, "student-union")
        check("Joining Student Union shows the moment: this Mac is its \(union.totalMacs + 1)th", joined?.macNumber == union.totalMacs + 1)
        await model.backend.settled()
        let inCloud = cloud.world.state.pool("student-union")
        check("The cloud has you in it, lending there, and only there",
              inCloud?.isMember(model.state.meID) == true && inCloud?.macs.first { $0.macID == me }?.state == .lending
              && cloud.world.state.lendingPoolID == "student-union" && cloud.world.state.pool("studio-9")?.macs.contains { $0.macID == me } == false)

        story(13, "An unlock names the Mac that tipped it")
        check("This Mac took Student Union from \(union.pooledGB) GB over GLM 5.3's line",
              joined?.unlock?.modelID == "glm-5.3" && joined?.unlock?.byMacID == me)
        check("The cloud records the unlock against this Mac, and the pool runs it now",
              inCloud?.unlocks.contains { $0.modelID == "glm-5.3" && $0.byMacID == me } == true && inCloud?.model?.id == "glm-5.3")

        story(14, "Badges for showing up")
        await model.backend.beatNow(active: true, adding: 3 * 3_600)
        check("Passing 100 hours lent earns 100 hours", badges().contains("hours-100") && (cloud.world.state.hoursLent ?? 0) >= 100)
        check("Taking a pool over a line earns Unlocker", badges().contains("unlock-student-union-glm-5.3"))
        model.endorse(model.state.pool("student-union")!)
        await model.backend.settled()
        check("Endorsing a pool of fewer than 20 Macs earns Early backer", badges().contains("backer-student-union"))
        model.startPool(name: "Self-test Lab", isPublic: false)
        await model.backend.settled()
        let lab = cloud.world.state.pools.first { $0.name == "Self-test Lab" }
        check("Starting a pool earns Pool starter, and the cloud keeps the pool under the app's id",
              badges().contains("starter") && lab != nil && lab?.id == model.state.pools.first { $0.name == "Self-test Lab" }?.id)
        check("New badges stay new until you see them", cloud.world.state.badges.filter(\.isNew).count >= 4)
        model.open(.profile)
        await model.backend.settled()
        check("Opening your profile marks them seen, in the cloud too", !cloud.world.state.badges.contains(where: \.isNew))

        story(15, "A note each week")
        let note: WeeklyNote? = try? await model.backend.client.send("POST", "v1/mock/note")
        await model.backend.syncNow()
        check("The week's note: hours lent, credits earned, and a headline",
              note != nil && model.backend.note == note && (note?.hoursLent ?? 0) > 4 && (note?.creditsEarned ?? 0) > 0 && note?.headline.isEmpty == false)
        print("      \(note?.headline ?? "")")
        check("It names the badges earned this week",
              Set(note?.badges ?? []).isSuperset(of: ["100 hours", "Unlocker", "Early backer", "Pool starter"]))
        let latest: WeeklyNote? = try? await model.backend.client.get("v1/notes/latest")
        check("GET /v1/notes/latest returns it", latest != nil && latest == note)
        await model.backend.beatNow(active: true, adding: 7 * 86_400)
        check("A week of lending later, the next note comes on its own", model.backend.note != nil && model.backend.note?.id != note?.id)

        story(16, "An invite link shows what your Mac would unlock")
        check("Invite links read in any form",
              ["dots://invite/LATE7Q", "https://dots.link/LATE7Q", " LATE7Q "].allSatisfy { AppModel.inviteCode(in: $0) == "LATE7Q" })
        check("Sam's invite waits for you in Explore", model.backend.invites.contains { $0.code == "LATE7Q" && $0.fromName == "Sam" })
        let preview = try? await model.backend.preview(code: "LATE7Q")
        let lateGB = cloud.world.state.pool("late-night-lab")?.pooledGB ?? 0
        let expected = Ladder.best(for: lateGB + model.state.thisMac.maxLendGB)?.name
        check("Opened, it says this Mac would take Late Night Lab from \(lateGB) GB to \(expected ?? "?")",
              preview?.unlocks != nil && preview?.unlocks == expected && preview?.addGB == model.state.thisMac.maxLendGB)
        let small: InviteDetails? = try? await model.backend.client.get("v1/invites/LATE7Q", query: [URLQueryItem(name: "memory_gb", value: "8")])
        check("For a Mac with nothing to lend, it says how far the pool still is",
              small?.unlocks == nil && small?.nextModel == "MiniMax M2.7" && (small?.shortGB ?? 0) > 0)
        var refused = false
        do { _ = try await model.backend.preview(code: "NOPE99") } catch { refused = true }
        check("A code that isn't valid says so", refused)
        model.openInviteLink("dots://invite/LATE7Q")
        let loaded = await until { model.openInvite?.preview != nil }
        check("dots://invite/LATE7Q opens the invite sheet, with the preview", model.sheet == .openInvite && loaded)
        if let p = model.openInvite?.preview { model.acceptInvite(p) }
        let accepted = await until(5) { joinedMoment(model, "late-night-lab") != nil }
        let late = joinedMoment(model, "late-night-lab")
        check("Accepting joins the pool, with the moment and the unlock this Mac caused",
              accepted && late?.unlock?.modelID == Ladder.best(for: lateGB + model.state.lending.limitGB)?.id && late?.unlock?.byMacID == me)
        check("...and the invite is used up", !(cloud.world.state.invitesForMe ?? []).contains("LATE7Q") && model.backend.invites.isEmpty)
        let invite = model.backend.createInvite(poolID: "studio-9")
        await model.backend.settled()
        let ours = try? await model.backend.preview(code: invite.code)
        check("An invite you make for Studio 9 works from the cloud at once", ours?.poolName == "Studio 9" && ours?.isMember == true)

        story(17, "Explore shows friends and endorsements, and you can endorse")
        let science = model.state.pool("open-science")!
        let friends = model.state.friends(in: science).map(\.name)
        check("Explore shows who you know in each pool: \(friends.joined(separator: " and ")) in Open Science", friends == ["Maya", "Jun"])
        check("...and how many people back it: \(science.endorsements.grouped)", science.endorsements == 2_412)
        check("Your endorsement of Student Union counted once", cloud.world.state.pool("student-union")?.endorsements == 43
              && cloud.world.state.pool("student-union")?.endorsedByMe == true)
        model.endorse(model.state.pool("student-union")!)
        await model.backend.settled()
        check("Endorsing it again doesn't count twice", cloud.world.state.pool("student-union")?.endorsements == 43)
        model.endorse(science)
        await model.backend.settled()
        check("Endorsing Open Science adds one, with no badge past 20 Macs",
              cloud.world.state.pool("open-science")?.endorsements == 2_413 && !badges().contains("backer-open-science"))

        story(18, "A boost to plan together, and the nights your Mac lent")
        let boost = cloud.world.state.pool("late-night-lab")?.boost
        check("Late Night Lab plans \(boost?.title ?? "a boost"): \(boost?.pledgedGB ?? 0) of \(boost?.targetGB ?? 0) GB pledged, guests welcome",
              boost?.title == "GLM Boost" && boost?.pledgedGB == 296 && boost?.targetGB == 396 && boost?.pledges.contains { $0.guest } == true)
        let spare = model.state.thisMac.maxLendGB
        model.backend.pledgeBoost(poolID: "late-night-lab", gb: spare)
        await model.backend.settled()
        let pledged = cloud.world.state.pool("late-night-lab")?.boost?.pledge(of: model.state.meID)
        check("Count Me In pledges this Mac's \(spare) GB, as a member", pledged?.gb == spare && pledged?.guest == false
              && model.state.pool("late-night-lab")?.boost?.pledgedGB == 296 + spare)
        if let b = model.state.pool("late-night-lab")?.boost {
            let wasPool = model.state.lendingPoolID, wasLimit = model.state.lending.limitGB
            model.setLimit(4)
            model.runBoosts(now: b.starts.addingTimeInterval(60))
            check("When the boost starts, this Mac lends its \(spare) GB there, awake",
                  model.boosting == "late-night-lab" && model.state.lendingPoolID == "late-night-lab"
                  && model.state.lending.limitGB == spare && model.state.lending.isOn)
            model.runBoosts(now: b.ends.addingTimeInterval(60))
            check("...and when it ends, lending goes back to how it was", model.boosting == nil && model.state.lending.limitGB == 4
                  && model.state.lendingPoolID == wasPool)
            model.setLimit(wasLimit)
        }
        model.backend.pledgeBoost(poolID: "late-night-lab", gb: nil)
        await model.backend.settled()
        check("...and taking it back takes it out", cloud.world.state.pool("late-night-lab")?.boost?.pledge(of: model.state.meID) == nil)
        check("This Mac's past nights are on record: \(cloud.world.state.nights?.count ?? 0) of them",
              (cloud.world.state.nights?.count ?? 0) >= 45 && cloud.world.state.nights?.first?.poolID == "home")
        cloud.forcedWorking = true
        await model.backend.beatNow(active: true, adding: 3_600)
        let tonight = cloud.world.state.nights?.last
        check("An hour of work counts toward tonight in Late Night Lab: \(tonight?.runs ?? 0) runs",
              tonight?.poolID == "late-night-lab" && (tonight?.minutes ?? 0) >= 60 && (tonight?.runs ?? 0) > 0
              && tonight?.date == Date().nightStart && model.state.nights?.last?.runs == tonight?.runs)
        let thanks = cloud.world.state.pool("late-night-lab")?.activity?.first { $0.kind == "thanks" }
        check("A friend there thanks your Mac for it", thanks != nil && ["sam", "ana"].contains(thanks?.personID ?? ""))
        let recap = NightRecap.last(in: model.state, now: Date().nightStart.addingTimeInterval(19 * 3_600))
        check("The morning after, the recap says: \(recap?.headline(macKind: model.state.thisMac.kind) ?? "nothing")",
              recap?.pool.id == "late-night-lab" && (recap?.night.runs ?? 0) > 0)
    }

    /// With DOTS_SELFTEST_ENDPOINT set to any OpenAI-compatible server (LM Studio, Ollama), answers are real.
    private static func realAnswers(_ url: String, _ rig: Rig) async {
        print("\nReal answers from \(url)")
        let probe = EndpointConfig(enabled: true, label: "Test", baseURL: url, model: "", keychainAccount: "dots-selftest-none")
        let ids = (try? await OpenAIClient.models(probe)) ?? []
        check("The endpoint lists its models", !ids.isEmpty)
        guard let chat = ids.first(where: { !$0.contains("embed") }) ?? ids.first else { return }
        var endpoint = probe
        endpoint.model = chat
        let chosen = endpoint
        rig.model.settings.poolEndpoint = chosen     // the model on this Mac, when the address is local
        rig.model.localDownUntil = nil
        rig.cloud.upstream = { chosen }               // and the hub's answers for your pools
        let real = await call(rig.base, "POST", "/chat/completions", key: rig.secret, body: ask("What's a good name for a dog? One line."))
        check("An everyday question gets the endpoint's own answer, not a simulated one",
              real.code == 200 && !real.answer.isEmpty && real.dots["simulated"] as? Bool == false)
        print("      \(real.text("ran_on")), \(chat): \(real.answer.prefix(90).replacingOccurrences(of: "\n", with: " "))")
        let stream = await call(rig.base, "POST", "/chat/completions", key: rig.secret, body: ask("And a cat? One line.", stream: true))
        check("Streaming passes its words through", !openAIText(stream.body).isEmpty)
    }

    /// Answers read as formatted text: Markdown blocks are found, and math becomes readable symbols.
    private static func answerFormatting() {
        print("\nAnswers are formatted")
        let blocks = Markdown.blocks("""
        ### Math
        1. **Equation:**
          \\[ x = a^2 + b \\]
        2. Next

        ```python
        def f(x):
            return x
        ```
        | a | b |
        |---|---|
        | 1 | 2 |
        """)
        check("A heading, a list, display math, code and a table are each a block", blocks == [
            .heading(level: 3, text: "Math"),
            .list([.init(marker: "1.", text: "**Equation:**", depth: 0)]),
            .math("x = a^2 + b"),
            .list([.init(marker: "2.", text: "Next", depth: 0)]),
            .code(language: "python", code: "def f(x):\n    return x"),
            .table(header: ["a", "b"], rows: [["1", "2"]]),
        ])
        check("A code fence still streaming runs to the end", Markdown.blocks("```\nlet x = 1") == [.code(language: "", code: "let x = 1")])
        check("Math reads as symbols", LaTeX.plain("\\frac{a+b}{2} \\times x^2 \\leq \\sqrt{y_1} + \\alpha") == "(a+b)/2 × x² ≤ √y₁ + α")
        let drafted = Markdown.inline("The sky is" + Markdown.draftMark + " blue because")
        check("A draft follows the answer, greyed out, with no trace of its mark",
              String(drafted.characters) == "The sky is blue because"
              && drafted.runs.filter { $0.foregroundColor != nil }.map { String(drafted[$0.range].characters) } == [" blue because"])
        check("Inline math is converted and a price is left alone",
              String(Markdown.inline("costs $5 and \\(x^2\\) grows").characters) == "costs $5 and x² grows")
    }

    /// A key made for more than one Anthropic workspace has to name its workspace on every request.
    /// A stand-in on this Mac answers the way Anthropic does, so no real key is needed.
    private static func workspaceChecks() async {
        print("\nAnthropic keys that work in more than one workspace")
        // The 400 such a key got from Anthropic on 2026-10-18, and the wording in Anthropic's docs
        let observed = "This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header with the ID of the workspace to use."
        let documented = "anthropic-workspace-id is required when authenticating with an identity-linked API key; send the id of the workspace this request acts in."
        let missing = [observed, documented].map {
            TransportError.http(400, #"{"type":"error","error":{"type":"invalid_request_error","message":"\#($0)"}}"#)
        }
        check("A missing workspace is recognised in both of Anthropic's wordings", missing.allSatisfy(\.needsWorkspace))
        check("...and the message says where to fix it", missing.allSatisfy { ($0.errorDescription ?? "").contains("Pick it in Settings") })
        let malformed = TransportError.http(400, #"{"type":"error","error":{"type":"invalid_request_error","message":"anthropic-workspace-id header must be a valid workspace ID."}}"#)
        check("A malformed workspace ID is told apart from a missing one", malformed.badWorkspace && !malformed.needsWorkspace)

        let saved = (try? JSONEncoder().encode(AppSettings())) ?? Data()
        let loaded = try? JSONDecoder().decode(AppSettings.self, from: saved)
        check("Settings saved before there was a workspace still load",
              loaded != nil && loaded?.frontierEndpoint.workspaceID == nil && !String(decoding: saved, as: UTF8.self).contains("workspaceID"))

        let wanted = "wrkspc_01SELFTEST"
        let other = "wrkspc_01DEFAULT"
        let port = UInt16.random(in: 19_000..<19_200)
        let anthropic = LocalGateway(port: port) { request, res in
            if request.path.hasSuffix("/organizations/workspaces") {
                if request.headers["anthropic-workspace-id"] != nil { res.error(400, "This list is for the whole organization."); return }
                res.json(200, ["data": [["id": other, "name": "Default"], ["id": wanted, "name": "dots"]], "has_more": false])
                return
            }
            guard let workspace = request.headers["anthropic-workspace-id"] else { res.error(400, observed); return }
            guard workspace == wanted || workspace == other else {
                res.json(404, ["type": "error", "error": ["type": "not_found_error", "message": "Workspace `\(workspace)` not found."]])
                return
            }
            if request.path.hasSuffix("/models") { res.json(200, ["data": [["id": "claude-opus-5-5"]]]); return }
            res.startStream()
            res.event(["model": "claude-opus-5-5", "choices": [["delta": ["content": "Answered in \(workspace)."]]]])
            res.finishStream()
        }
        do { try anthropic.start() } catch { check("The stand-in for Anthropic starts", false); return }
        defer { anthropic.stop() }
        try? await Task.sleep(for: .milliseconds(300))

        var endpoint = EndpointConfig(enabled: true, label: "Anthropic", baseURL: "http://127.0.0.1:\(port)/v1",
                                      model: "claude-opus-5-5", keychainAccount: "dots-selftest-none")
        var refused: TransportError?
        do { _ = try await OpenAIClient.models(endpoint) } catch { refused = error as? TransportError }
        check("Testing a key without its workspace asks for one", refused?.needsWorkspace == true)

        let found = (try? await OpenAIClient.workspaces(endpoint)) ?? []
        check("Settings can offer the workspaces the key can use, the Default Workspace included",
              found.map(\.id) == [other, wanted] && found.first?.name == "Default")

        endpoint.workspaceID = "  \(wanted)  "
        let listed = (try? await OpenAIClient.models(endpoint)) ?? []
        check("With its workspace set, the key's models come back", listed == ["claude-opus-5-5"])

        var answer = ""
        do {
            for try await event in OpenAIClient.stream(endpoint, messages: [ChatMessage(role: "user", content: "Hello")]) {
                if case let .text(text) = event { answer += text }
            }
        } catch { answer = error.localizedDescription }
        check("Answers name the workspace too", answer == "Answered in \(wanted).")

        endpoint.workspaceID = "wrkspc_01NOPE"
        var shown = ""
        do { _ = try await OpenAIClient.models(endpoint) } catch { shown = error.localizedDescription }
        check("A workspace the key can't use is named in plain words", shown == "The endpoint answered 404. Workspace `wrkspc_01NOPE` not found.")
    }

    // MARK: Helpers

    private static func story(_ n: Int, _ title: String) {
        print("\n\(n). \(title)")
    }

    private static func check(_ name: String, _ ok: Bool) {
        if ok { passes += 1 } else { failures += 1 }
        print("\(ok ? "PASS" : "FAIL")  \(name)")
    }

    /// Waits for something the app does in the background, for up to `seconds`.
    private static func until(_ seconds: Double = 3, _ condition: () -> Bool) async -> Bool {
        let end = Date().addingTimeInterval(seconds)
        while !condition() {
            if Date() > end { return false }
            try? await Task.sleep(for: .milliseconds(50))
        }
        return true
    }

    /// The joined moment the app is showing for a pool, if it is.
    private static func joinedMoment(_ model: AppModel, _ poolID: String) -> (macNumber: Int, unlock: UnlockEvent?)? {
        guard case let .some(.joined(pool, number, unlock, _)) = model.moment, pool == poolID else { return nil }
        return (number, unlock)
    }

    /// A pasted report, so a request is the size of a real one (whole credits round tiny answers up)
    private static let report = String(repeating: "Our API serves 40,000 requests a minute at peak, and the slowest calls read the same rows again and again. ", count: 50) + "\n\n"

    private static let weatherTool: [String: Any] = [
        "type": "function",
        "function": [
            "name": "get_weather", "description": "The weather in a city",
            "parameters": ["type": "object", "properties": ["city": ["type": "string"]], "required": ["city"]] as [String: Any],
        ] as [String: Any],
    ]

    /// A request body in either format
    private static func ask(_ text: String, model: String = "auto", format: APIFormat = .openai, stream: Bool = false,
                            extra: [String: Any] = [:]) -> [String: Any] {
        var body: [String: Any] = ["model": model, "messages": [["role": "user", "content": text]]]
        if format == .anthropic { body["max_tokens"] = 400 }
        if stream { body["stream"] = true }
        for (k, v) in extra { body[k] = v }
        return body
    }

    /// One exchange with the local API.
    private struct Reply {
        var json: [String: Any]?
        var code: Int
        var body: String
        var response: HTTPURLResponse?

        var dots: [String: Any] { json?["dots"] as? [String: Any] ?? [:] }
        func text(_ key: String) -> String { dots[key] as? String ?? "" }
        func int(_ key: String) -> Int { dots[key] as? Int ?? -1 }
        func number(_ key: String) -> Double { dots[key] as? Double ?? -1 }
        func flag(_ key: String) -> Bool { dots[key] as? Bool ?? false }
        func header(_ name: String) -> String? { response?.value(forHTTPHeaderField: name) }
        var errorType: String? { (json?["error"] as? [String: Any])?["type"] as? String }

        /// The answer's text, in OpenAI's format or Anthropic's
        var answer: String {
            if let choice = (json?["choices"] as? [[String: Any]])?.first, let message = choice["message"] as? [String: Any] {
                return message["content"] as? String ?? ""
            }
            let blocks = json?["content"] as? [[String: Any]] ?? []
            return blocks.compactMap { $0["text"] as? String }.joined()
        }
    }

    private static func call(_ base: URL, _ method: String, _ path: String, key: String? = nil, format: APIFormat = .openai,
                             body: [String: Any]? = nil, raw: Data? = nil) async -> Reply {
        var r = URLRequest(url: base.appendingPathComponent(String(path.dropFirst())), timeoutInterval: 60)
        r.httpMethod = method
        if format == .anthropic {
            r.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
            if let key { r.setValue(key, forHTTPHeaderField: "x-api-key") }
        } else if let key {
            r.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        }
        if let body { r.httpBody = try? JSONSerialization.data(withJSONObject: body) }
        if let raw { r.httpBody = raw }
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            let (data, response) = try await URLSession.shared.data(for: r)
            let http = response as? HTTPURLResponse
            return Reply(json: (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], code: http?.statusCode ?? 0,
                         body: String(decoding: data, as: UTF8.self), response: http)
        } catch {
            print("      \(method) \(path): \(error.localizedDescription)")
            return Reply(json: nil, code: 0, body: "", response: nil)
        }
    }

    /// Server-sent events: each one's name (Anthropic names them) and its JSON
    private static func events(_ body: String) -> [(name: String?, data: [String: Any])] {
        var out: [(name: String?, data: [String: Any])] = []
        var name: String?
        for line in body.components(separatedBy: "\n") {
            if line.hasPrefix("event:") { name = line.dropFirst(6).trimmingCharacters(in: .whitespaces); continue }
            guard line.hasPrefix("data:") else { continue }
            let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
            if let o = (try? JSONSerialization.jsonObject(with: Data(payload.utf8))) as? [String: Any] { out.append((name: name, data: o)) }
            name = nil
        }
        return out
    }

    /// The text of a streamed answer in OpenAI's format, put back together from its deltas
    private static func openAIText(_ body: String) -> String {
        events(body).compactMap { e -> String? in
            let delta = (e.data["choices"] as? [[String: Any]])?.first?["delta"] as? [String: Any]
            return delta?["content"] as? String
        }.joined()
    }

    /// The same, in Anthropic's
    private static func anthropicText(_ body: String) -> String {
        events(body).filter { $0.name == "content_block_delta" }.compactMap { ($0.data["delta"] as? [String: Any])?["text"] as? String }.joined()
    }

    /// A model server that speaks the OpenAI API: LM Studio on this Mac, or a provider behind your own key.
    private static func standIn(_ r: HTTPRequest, _ res: HTTPResponder, text: String, usage: (Int, Int)?, counts: Bool) {
        if r.path.hasSuffix("/models") { res.json(200, ["object": "list", "data": [["id": "qwen3-8b", "object": "model"]]]); return }
        guard r.path.hasSuffix("/chat/completions") else { res.error(404, "Not here."); return }
        if counts { providerHits += 1 }
        let body = r.object
        let model = body["model"] as? String ?? "model"
        let input = usage?.0 ?? 40, output = usage?.1 ?? 12
        guard body["stream"] as? Bool == true else {
            res.json(200, OpenAIChunks.completion(model: model, text: text, input: input, output: output))
            return
        }
        res.startStream()
        for (i, word) in text.split(separator: " ").enumerated() {
            res.event(OpenAIChunks.chunk(id: "chatcmpl-standin", model: model, text: (i == 0 ? "" : " ") + word))
        }
        res.event(OpenAIChunks.chunk(id: "chatcmpl-standin", model: model, finish: "stop"))
        if (body["stream_options"] as? [String: Any])?["include_usage"] as? Bool == true {
            res.event(["id": "chatcmpl-standin", "object": "chat.completion.chunk", "model": model, "choices": [Any](),
                       "usage": ["prompt_tokens": input, "completion_tokens": output, "total_tokens": input + output]] as [String: Any])
        }
        res.finishStream()
    }
}
