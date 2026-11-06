import Foundation

/// The world the simulation starts in. Your Mac is the real one (its actual memory and model);
/// everyone else is sample data, the same people and pools as the case study.
enum SampleData {
    static func initialState() -> NetworkState {
        let now = Date()
        let day: TimeInterval = 86_400
        let me = Person(id: "me", name: ThisMac.ownerFirstName)
        let people = [
            me,
            Person(id: "maya", name: "Maya"), Person(id: "theo", name: "Theo"),
            Person(id: "jun", name: "Jun"), Person(id: "priya", name: "Priya"),
            Person(id: "leo", name: "Leo"), Person(id: "nia", name: "Nia"),
        ]
        let thisMac = MacDevice(id: "mac-me", ownerID: "me", kind: ThisMac.kind, memoryGB: ThisMac.memoryGB)
        let devices = [
            thisMac,
            MacDevice(id: "mac-maya", ownerID: "maya", kind: "MacBook Pro", memoryGB: 64),
            MacDevice(id: "mac-theo", ownerID: "theo", kind: "Mac Studio", memoryGB: 128),
            MacDevice(id: "mac-jun", ownerID: "jun", kind: "Mac mini", memoryGB: 32),
            MacDevice(id: "mac-priya", ownerID: "priya", kind: "MacBook Air", memoryGB: 24),
            MacDevice(id: "mac-leo", ownerID: "leo", kind: "iMac", memoryGB: 24),
            MacDevice(id: "mac-nia", ownerID: "nia", kind: "MacBook Pro", memoryGB: 36),
        ]
        func lending(_ id: String, joined days: Double) -> PoolMac {
            let d = devices.first { $0.id == id }!
            return PoolMac(macID: id, lendGB: d.maxLendGB, state: .lending, joinedAt: now.addingTimeInterval(-days * day))
        }

        // Student Union sits just under DeepSeek V3.1's line, so your Mac is the one that tips it over.
        let myLend = thisMac.maxLendGB
        let unionGB = min(395, max(137, 396 - myLend))

        let pools: [Pool] = [
            Pool(id: "studio-9", name: "Studio 9", isPublic: false, blurb: "Our studio's Macs, pooled for client work.",
                 tags: [], memberIDs: ["me", "maya", "theo", "jun", "priya"],
                 macs: [lending("mac-maya", joined: 150), lending("mac-theo", joined: 150), lending("mac-jun", joined: 120), lending("mac-priya", joined: 90)],
                 endorsements: 0, hostID: "maya", offersIdleToNetwork: true, phase: 0.2,
                 createdAt: now.addingTimeInterval(-150 * day), speedFactor: 1.0, demand: 0.6),
            Pool(id: "home", name: "Home", isPublic: false, blurb: "The family's Macs.",
                 tags: [], memberIDs: ["me", "leo", "nia"],
                 macs: [lending("mac-leo", joined: 60), lending("mac-nia", joined: 60)],
                 hostID: "me", phase: 1.1, createdAt: now.addingTimeInterval(-60 * day), speedFactor: 1.0, demand: 0.5),
            Pool(id: "open-science", name: "Open Science", isPublic: true, blurb: "Researchers pooling Macs for long papers and messy data.",
                 tags: ["Research"], memberIDs: ["maya", "jun"], macs: [],
                 crowdMacs: 348, crowdLending: 212, crowdGB: 2_140, endorsements: 2_412,
                 phase: 0.15, createdAt: now.addingTimeInterval(-400 * day), speedFactor: 1.0, demand: 0.92),
            Pool(id: "night-owls-berlin", name: "Night Owls Berlin", isPublic: true, blurb: "Idle Macs across Berlin, quick for anyone nearby.",
                 tags: ["Nearby"], memberIDs: [], macs: [],
                 crowdMacs: 88, crowdLending: 61, crowdGB: 610, endorsements: 640,
                 phase: 0.52, createdAt: now.addingTimeInterval(-200 * day), speedFactor: 1.27, demand: 0.75),
            Pool(id: "indie-hackers", name: "Indie Hackers", isPublic: true, blurb: "Builders sharing a coding model around the clock.",
                 tags: ["Code"], memberIDs: ["theo"], macs: [],
                 crowdMacs: 126, crowdLending: 94, crowdGB: 560, endorsements: 1_104,
                 phase: 0.89, createdAt: now.addingTimeInterval(-300 * day), servesModelID: "qwen3-coder-480b", speedFactor: 1.0, demand: 0.83),
            Pool(id: "film-school", name: "Film School", isPublic: true, blurb: "Editors' Mac Studios, lent out between renders.",
                 tags: ["Research"], memberIDs: [], macs: [],
                 crowdMacs: 64, crowdLending: 38, crowdGB: 1_380, endorsements: 510,
                 phase: 1.26, createdAt: now.addingTimeInterval(-250 * day), speedFactor: 1.33, demand: 1.17),
            Pool(id: "writers-room", name: "Writers' Room", isPublic: true, blurb: "Novelists and editors, with room for a whole manuscript.",
                 tags: ["Writing"], memberIDs: ["priya"], macs: [],
                 crowdMacs: 14, crowdLending: 9, crowdGB: 180, endorsements: 380,
                 phase: 1.63, createdAt: now.addingTimeInterval(-90 * day), speedFactor: 1.0, demand: 0.58),
            Pool(id: "student-union", name: "Student Union", isPublic: true, blurb: "Campus Macs, lent between classes.",
                 tags: ["Nearby"], memberIDs: [], macs: [],
                 crowdMacs: 9, crowdLending: 6, crowdGB: unionGB, endorsements: 42,
                 phase: 2.0, createdAt: now.addingTimeInterval(-3 * day), speedFactor: 0.79, demand: 0.42),
        ]

        let badges = [
            Badge(id: "nights-30", symbol: "moon.fill", title: "30 nights", detail: "Lent 30 nights in a row", date: now.addingTimeInterval(-20 * day)),
            Badge(id: "answers-1000", symbol: "bubble.left.fill", title: "1,000 runs", detail: "Took part in 1,000 runs", date: now.addingTimeInterval(-45 * day)),
        ]

        let keys = [
            APIKey(id: "key-laptop", name: "\(me.name)'s laptop", last4: "9f3a", secretHash: "", reach: .all, monthlyLimit: nil,
                   createdAt: now.addingTimeInterval(-40 * day), lastUsedAt: now.addingTimeInterval(-3_600), creditsThisMonth: 612, requestsThisMonth: 240),
            APIKey(id: "key-raycast", name: "Raycast", last4: "4c1e", secretHash: "", reach: .yourPools, monthlyLimit: nil,
                   createdAt: now.addingTimeInterval(-25 * day), lastUsedAt: now.addingTimeInterval(-7_200), creditsThisMonth: 0, requestsThisMonth: 96),
        ]

        // This month's requests: mostly free in your own pools, some in public pools, a few at frontier models
        var requests: [RequestRecord] = []
        let monthStart = Calendar.current.dateInterval(of: .month, for: now)?.start ?? now.addingTimeInterval(-20 * day)
        let span = max(3_600, now.timeIntervalSince(monthStart))
        // Each with its tokens, and what Claude Opus 5.5 would have cost for them, so the month's savings add up
        for i in 0..<141 {
            let date = monthStart.addingTimeInterval(span * Double(i) / 141)
            let input = 600 + (i * 373) % 2_400, output = 180 + (i * 211) % 700
            let baseline = Pricing.usd(FrontierModel.opus.id, input: input, output: output)
            if i % 10 == 3 {
                let f = i % 20 == 3 ? FrontierModel.opus : FrontierModel.sonnet
                requests.append(RequestRecord(date: date, source: i % 3 == 0 ? "Ask" : "\(me.name)'s laptop", destination: .frontier,
                                              place: "Anthropic", model: f.name, credits: Pricing.credits(f.id, input: input, output: output),
                                              isPrivate: false, inputTokens: input, outputTokens: output, measured: true, baselineUSD: baseline))
            } else if i % 5 == 1 || (i % 10 == 7 && i % 20 != 7) {
                requests.append(RequestRecord(date: date, source: "Ask", destination: .publicPool, place: "Open Science",
                                              model: "Kimi K2", credits: Ladder.tier("kimi-k2")?.credits ?? 4, isPrivate: false,
                                              inputTokens: input, outputTokens: output, measured: true, baselineUSD: baseline))
            } else {
                requests.append(RequestRecord(date: date, source: i % 4 == 0 ? "Raycast" : "Ask", destination: .yourPool,
                                              place: "Studio 9", model: "Qwen3 235B", credits: 0, isPrivate: i % 3 == 0,
                                              inputTokens: input, outputTokens: output, measured: true, baselineUSD: baseline))
            }
        }

        let log = [
            CreditEvent(date: now.addingTimeInterval(-2 * 3_600), amount: -FrontierModel.opus.credits, note: "Claude Opus 5.5, a second opinion", kind: "spend"),
            CreditEvent(date: now.addingTimeInterval(-9 * 3_600), amount: 96, note: "Your Mac lent to Studio 9", kind: "lend",
                        poolID: "studio-9", minutes: 480, computePF: 3.6),
            CreditEvent(date: now.addingTimeInterval(-26 * 3_600), amount: -(Ladder.tier("kimi-k2")?.credits ?? 4), note: "Kimi K2 in Open Science", kind: "spend"),
            CreditEvent(date: now.addingTimeInterval(-33 * 3_600), amount: 88, note: "Your Mac lent to Studio 9", kind: "lend",
                        poolID: "studio-9", minutes: 420, computePF: 3.1),
        ]

        return NetworkState(
            meID: me.id, thisMacID: thisMac.id, people: people, devices: devices, pools: pools,
            credits: 1_245, earnedAllTime: 4_812, questionsHelped: 12_406, creditLog: log, badges: badges,
            apiKeys: keys, requests: requests,
            lending: LendingSettings(isOn: false, limitGB: myLend, onlyWhenPluggedIn: true, pauseWhileInUse: false),
            lendingPoolID: nil, networkLendingNow: 2_914, lendingSince: nil, endorsedBefore: 12)
    }

    /// Initials for the anonymous Macs a public pool shows on its page.
    static func faces(for pool: Pool) -> [String] {
        let letters = Array("AKRDLEMSTBNOC")
        // a stable hash (Swift's own hashValue changes every launch)
        var seed = pool.id.unicodeScalars.reduce(UInt64(7)) { $0 &* 31 &+ UInt64($1.value) }
        var out: [String] = []
        for _ in 0..<min(6, pool.crowdLending) {
            seed = seed &* 6364136223846793005 &+ 1442695040888963407
            out.append(String(letters[Int(seed >> 33) % letters.count]))
        }
        return out
    }
}
