import SwiftUI

/// A pool, like a server: who's here, its art growing tonight, the next level, what your Mac gives, and what happened.
struct PoolView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.artClock) private var artClock
    @Environment(\.isSnapshot) private var isSnapshot
    var pool: Pool
    /// The Mac whose dots light up while the pointer is on its owner
    @State private var focus: String?
    @State private var replayStart: Date?
    @State private var confirmLeave = false

    var body: some View {
        let s = model.state
        let history = PoolHistory.make(pool, thisMacID: s.thisMacID, now: artClock ?? .now)
        LiveScroll {
            VStack(alignment: .leading, spacing: 24) {
                header
                VStack(spacing: 16) {
                    if let boost = pool.boost, boost.ends > (artClock ?? .now) {
                        BoostBanner(pool: pool, boost: boost)
                    }
                    HStack(alignment: .top, spacing: 16) {
                        artStage(history)
                            .frame(width: 460)
                        VStack(spacing: 16) {
                            LevelCard(pool: pool)
                                .fixedSize(horizontal: false, vertical: true)
                            if pool.isMember(s.meID) {
                                ContributionCard(pool: pool, confirmLeave: $confirmLeave)
                                    .fixedSize(horizontal: false, vertical: true)
                            } else {
                                JoinCard(pool: pool)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if pool.isPublic {
                                EndorseCard(pool: pool)
                            } else {
                                ActivityCard(pool: pool)
                            }
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
            .page()
        }
        .confirmationDialog("Leave \(pool.name)?", isPresented: $confirmLeave) {
            Button("Leave \(pool.name)", role: .destructive) { model.leave(pool) }
        } message: {
            Text(pool.isPublic ? "Your Mac stops lending here." : "Your Mac stops lending here, and you'll need a new invite to come back.")
        }
    }

    // MARK: Header

    private var header: some View {
        let s = model.state
        return HStack(alignment: .center, spacing: 18) {
            VStack(alignment: .leading, spacing: 5) {
                Text(pool.name).font(D.ui(26, .bold)).foregroundStyle(D.t1).lineLimit(1)
                Text(subtitle).font(D.ui(13)).foregroundStyle(Color(hex: 0x8A8A8A)).lineLimit(1)
            }
            Spacer(minLength: 12)
            if pool.isPublic {
                HStack(spacing: -6) {
                    ForEach(Array(SampleData.faces(for: pool).prefix(5).enumerated()), id: \.offset) { i, f in
                        Avatar(text: f, size: 32).overlay(Circle().strokeBorder(D.bg2, lineWidth: 2).padding(-2)).zIndex(Double(10 - i))
                    }
                }
                Text("\(pool.lendingMacs.grouped) live").font(D.ui(12.5)).foregroundStyle(D.t4)
                if pool.endorsedByMe {
                    Label("Endorsed", systemImage: "checkmark.seal.fill").font(D.ui(13, .semibold)).foregroundStyle(D.t2)
                        .padding(.horizontal, 13).frame(height: 32)
                        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Color.white.opacity(0.12)))
                } else {
                    outlineButton("Endorse", symbol: "checkmark.seal") { model.endorse(pool) }
                }
            } else {
                HStack(spacing: 10) {
                    ForEach(members, id: \.personID) { m in
                        MemberFace(member: m)
                            .onHover { focus = $0 ? m.macID : (focus == m.macID ? nil : focus) }
                    }
                }
                outlineButton("Invite", symbol: "plus") { model.sheet = .invite(pool.id) }
            }
            if pool.isMember(s.meID) && !pool.isPublic {
                if isSnapshot {
                    Image(systemName: "ellipsis").font(.system(size: 13, weight: .semibold)).foregroundStyle(D.t3).frame(width: 28, height: 32)
                } else {
                    Menu {
                        Toggle("Offer Idle Hours to the Network", isOn: Binding(get: { pool.offersIdleToNetwork },
                                                                                set: { model.backend.setOffersIdle(poolID: pool.id, $0) }))
                        Divider()
                        Button("Leave \(pool.name)…", role: .destructive) { confirmLeave = true }
                    } label: {
                        Image(systemName: "ellipsis").font(.system(size: 13, weight: .semibold)).foregroundStyle(D.t3)
                    }
                    .menuStyle(.button).menuIndicator(.hidden).buttonStyle(.plain)
                    .frame(width: 28, height: 32)
                    .help("More")
                }
            }
        }
        .frame(height: 52)
    }

    private var subtitle: String {
        var parts: [String] = []
        if pool.isPublic { parts.append("Public pool") }
        if pool.runsOneModel {
            parts.append("Runs \(pool.modelLabel ?? "")")
        } else {
            parts.append(pool.model?.name ?? "No model yet")
            if pool.level > 0 { parts.append("Level \(pool.level)") }
        }
        if pool.isPublic { parts.append(pool.totalMacs.counted("Mac")) }
        return parts.joined(separator: " · ")
    }

    private func outlineButton(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 7) {
                Image(systemName: symbol).font(.system(size: 11, weight: .bold))
                Text(title).font(D.ui(13, .semibold))
            }
            .foregroundStyle(D.t2)
            .padding(.horizontal, 13).frame(height: 32)
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Color.white.opacity(0.12)))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
    }

    /// The people in a friends' pool: live first, then you, then those away or offline.
    private var members: [Member] {
        let s = model.state
        let all = pool.memberIDs.compactMap { pid -> Member? in
            guard let person = s.person(pid) else { return nil }
            let mac = pool.macs.first { s.device($0.macID)?.ownerID == pid }
            let device = mac.flatMap { s.device($0.macID) }
            return Member(personID: pid, name: pid == s.meID ? "You" : person.name, initial: pid == s.meID ? "You" : person.initial,
                          isYou: pid == s.meID, isHost: pool.hostID == pid, macID: mac?.macID, macKind: device?.kind,
                          gb: mac?.lendGB, state: mac?.state, joined: mac?.joinedAt ?? .distantPast)
        }
        func rank(_ m: Member) -> Int {
            if m.state == .lending { return m.isHost ? 0 : 1 }
            return m.isYou ? 2 : m.state == .asleep ? 3 : 4
        }
        return all.sorted { rank($0) != rank($1) ? rank($0) < rank($1) : $0.joined < $1.joined }
    }

    // MARK: The art

    private func artStage(_ history: PoolHistory) -> some View {
        let s = model.state
        let now = artClock ?? .now
        let served = model.state.served(in: pool.id)
        let live = pool.lendingMacs > 0
        let focusIndex = focus.flatMap { id in history.owners.firstIndex(of: id) }
        return VStack(spacing: 0) {
            ZStack {
                RingsArt(history: history, size: 420, cells: 210, lastNights: 30, live: live, focus: focusIndex, replayStart: replayStart)
                VStack(spacing: 3) {
                    Text("\(history.nights.count)").font(D.ui(30, .light).monospacedDigit()).foregroundStyle(D.t2)
                    Text("NIGHTS").font(D.ui(9, .bold)).tracking(1.6).foregroundStyle(Color(hex: 0x6E6E6E))
                }
                .allowsHitTesting(false)
            }
            .frame(maxHeight: .infinity)
            .accessibilityElement()
            .accessibilityLabel("\(pool.name)'s art: a ring for each of its \(history.nights.count.counted("night")), the hours around it, a dot for every 10 runs")
            Rule().padding(.top, 14)
            StatRow(stats: [.init(value: served.runs.grouped, label: "answered"),
                            .init(value: Runs.short(served.tokens), label: "tokens"),
                            .init(value: "\(pool.tokPerSec)", label: "tokens a second")], size: 18)
                .padding(.top, 14)
            HStack(spacing: 2) {
                HStack(spacing: 7) {
                    if live { Circle().fill(D.accent).frame(width: 6, height: 6).modifier(Blink(on: !isSnapshot)) }
                    Text(live ? "No. \(history.nights.count) · live" : "No. \(history.nights.count)")
                }
                .font(D.ui(12.5)).foregroundStyle(Color(hex: 0x8A8A8A))
                Spacer()
                IconButton(symbol: "arrow.down.to.line", help: "Download") {
                    ArtExport.save(pool: pool, history: history, served: served, thisMacKind: s.thisMac.kind)
                }
                IconButton(symbol: "arrow.counterclockwise", help: "Replay the \(history.nights.count.counted("night"))") { replayStart = .now }
            }
            .frame(height: 28)
            .padding(.top, 12)
        }
        .stage()
    }
}

/// Someone in a friends' pool, as their face in the header shows them.
struct Member {
    var personID: String
    var name: String
    var initial: String
    var isYou: Bool
    var isHost: Bool
    var macID: String?
    var macKind: String?
    var gb: Int?
    var state: MacState?
    var joined: Date
}

/// A member's face: an orange ring while their Mac lends, yours filled orange, dim while away or offline.
struct MemberFace: View {
    var member: Member

    var body: some View {
        let live = member.state == .lending
        let away = member.state == .asleep
        Text(member.initial)
            .font(.system(size: member.initial.count > 1 ? 10 : 12.5, weight: .bold))
            .foregroundStyle(member.isYou && live ? D.onAccent : away ? Color(hex: 0x8A8A8A) : member.state == nil ? Color(hex: 0x5A5A5A) : D.t1)
            .frame(width: 32, height: 32)
            .background(Circle().fill(member.isYou && live ? D.accent : away ? Color(hex: 0x232323) : member.state == nil ? Color(hex: 0x1D1D1D) : Color(hex: 0x2A2A2A)))
            .overlay {
                if live && !member.isYou { Circle().strokeBorder(D.accent.opacity(0.85), lineWidth: 1).padding(-3) }
            }
            .overlay(alignment: .bottomTrailing) {
                if away {
                    Image(systemName: "moon.fill").font(.system(size: 8)).foregroundStyle(D.t3)
                        .padding(3).background(Circle().fill(D.bg2)).offset(x: 4, y: 4)
                }
            }
            .help(tooltip)
            .accessibilityElement()
            .accessibilityLabel(tooltip)
    }

    private var tooltip: String {
        let role = member.isHost ? " · Host" : ""
        guard let kind = member.macKind, let gb = member.gb, let state = member.state else { return "\(member.name)\(role) · offline" }
        let status = state == .lending ? "live" : state == .asleep ? "away" : "not lending"
        return "\(member.name)\(role) · \(kind) · \(gb) GB · \(status)"
    }
}

// MARK: - What it runs, or the next level

/// A pool that names its model: what it runs, what that needs, and what the pool has. A pool on the ladder: how far
/// it is from its next level, and the one thing that gets it there.
struct LevelCard: View {
    @Environment(AppModel.self) private var model
    @Environment(\.artClock) private var artClock
    var pool: Pool

    var body: some View {
        let s = model.state
        let gb = pool.pooledGB
        let next = pool.nextModel
        let mine = pool.macs.first { $0.macID == s.thisMacID && $0.state == .lending }?.lendGB ?? 0
        let tonight = (artClock ?? .now).nightStart
        let justUnlocked = pool.unlocks.last.flatMap { $0.byMacID == s.thisMacID && $0.date >= tonight && $0.modelID == pool.model?.id ? $0 : nil }
        VStack(alignment: .leading, spacing: 0) {
            if pool.runsOneModel {
                runs(mine: mine)
            } else if let event = justUnlocked, let tier = Ladder.tier(event.modelID) {
                CardHeader("Level \(pool.level)", note: "\(gb.grouped) GB")
                Text("Unlocked").font(D.ui(40, .semibold)).tracking(-1).foregroundStyle(D.accentText).padding(.top, 14)
                Text(pool.isPublic ? "\(tier.name) now runs here, for everyone." : "\(tier.name) now runs here, free.")
                    .font(D.ui(13)).foregroundStyle(D.t3).padding(.top, 10)
                bar(target: max(gb, event.toGB), mine: mine)
                HStack(spacing: 16) {
                    Button("See Level \(pool.level)") { model.moment = .unlocked(poolID: pool.id, event: event) }
                        .buttonStyle(DButtonStyle(kind: .primary, large: true))
                    if !pool.isPublic { bringAMac }
                }
                .padding(.top, 18)
            } else if let next {
                let togo = next.minGB - gb
                CardHeader("Level \(pool.level + 1)", note: "\(gb.grouped) of \(next.minGB.grouped) GB")
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("\(togo.grouped) GB").font(D.ui(40, .semibold).monospacedDigit()).tracking(-1).foregroundStyle(D.t1)
                        .contentTransition(.numericText())
                    Text("to go").font(D.ui(14)).foregroundStyle(Color(hex: 0x8A8A8A))
                }
                .padding(.top, 14)
                Text(pool.isPublic ? "Unlock \(next.name) for everyone here." : "Unlock \(next.name) and keep hard prompts here, free.")
                    .font(D.ui(13)).foregroundStyle(D.t3).lineSpacing(2).padding(.top, 10)
                    .fixedSize(horizontal: false, vertical: true)
                bar(target: next.minGB, mine: mine)
                HStack(spacing: 16) {
                    action(togo: togo)
                    if !pool.isPublic { bringAMac }
                }
                .padding(.top, 18)
            } else {
                CardHeader("Level \(pool.level)", note: "\(gb.grouped) GB")
                Text("Top level").font(D.ui(40, .semibold)).tracking(-1).foregroundStyle(D.t1).padding(.top, 14)
                Text("\(pool.model?.name ?? "") is the largest model on the ladder.").font(D.ui(13)).foregroundStyle(D.t3).padding(.top, 10)
            }
        }
        .panel()
    }

    @ViewBuilder private func runs(mine: Int) -> some View {
        let s = model.state
        let gb = pool.pooledGB
        let needs = pool.needsGB
        // A picture needs one Mac with room for the whole model; text needs the pool's Macs together
        let drawers = pool.macs.filter { $0.state == .lending && $0.lendGB >= (needs ?? 0) }.count
        let ready = pool.draws ? drawers > 0 : gb >= (needs ?? 1)
        CardHeader("Runs", note: pool.draws ? "draws pictures" : "writes text")
        Text(pool.modelLabel ?? "").font(D.ui(30, .semibold)).tracking(-0.6).foregroundStyle(D.t1)
            .lineLimit(1).minimumScaleFactor(0.6).padding(.top, 10)
        Text(runsLine(needs: needs, gb: gb, drawers: drawers))
            .font(D.ui(13)).foregroundStyle(D.t3).lineSpacing(2).padding(.top, 8)
            .fixedSize(horizontal: false, vertical: true)
        StatRow(stats: [.init(value: needs.map { "\($0) GB" } ?? "?", label: pool.draws ? "on one Mac" : "in all"),
                        .init(value: "\(gb.grouped) GB", label: "lent now", accent: ready),
                        .init(value: pool.lendingMacs.grouped, label: pool.lendingMacs == 1 ? "Mac lending" : "Macs lending")], size: 18)
            .padding(.top, 16)
        if !pool.draws, let needs { bar(target: needs, mine: mine) }
        let canLendHere = pool.isMember(s.meID) && !s.isLending(to: pool.id) && s.offerGB(for: pool) > 0
        if canLendHere || !pool.isPublic {
            HStack(spacing: 16) {
                if canLendHere {
                    Button("Lend Here") { model.setShare(pool, gb: s.offerGB(for: pool)); model.setLendingOn(true) }
                        .buttonStyle(DButtonStyle(kind: .primary, large: true))
                }
                if !pool.isPublic { bringAMac }
            }
            .padding(.top, 18)
        }
    }

    private func runsLine(needs: Int?, gb: Int, drawers: Int) -> String {
        guard let needs else { return pool.draws ? "Each picture is drawn whole on one Mac." : "Shared out across the Macs lending here." }
        if pool.draws {
            let now = drawers == 0 ? "None lends that much yet." : drawers == 1 ? "One does now." : "\(drawers) do now."
            return "Each picture is drawn whole on one Mac lending \(needs) GB or more. \(now)"
        }
        let togo = needs - gb
        // A model one Mac can hold is copied whole to each Mac with room for it; a bigger one is split between them
        let whole = pool.macs.contains { $0.state == .lending && $0.lendGB >= needs }
        let how = whole ? "Each Mac lending \(needs) GB or more keeps its own copy, and your questions run on yours."
                        : "Its weights are shared out across the Macs lending here."
        return how + (togo > 0 ? " \(togo) GB to go." : "")
    }

    /// The pool's memory as segments: each Mac's in grey, yours orange, and the stretch still to go outlined.
    private func bar(target: Int, mine: Int) -> some View {
        let s = model.state
        let others = pool.macs.filter { $0.state == .lending && $0.macID != s.thisMacID }.sorted { $0.lendGB > $1.lendGB }
        let total = max(target, pool.pooledGB)
        return GeometryReader { geo in
            let per = geo.size.width / CGFloat(max(total, 1))
            HStack(spacing: 2) {
                if pool.crowdGB > 0 { Rectangle().fill(Color(hex: 0x6A6A6A)).frame(width: max(2, CGFloat(pool.crowdGB) * per - 2)) }
                ForEach(Array(others.enumerated()), id: \.element.macID) { i, m in
                    Rectangle().fill(Color(hex: i % 2 == 0 ? 0x6A6A6A : 0x9A9A9A)).frame(width: max(2, CGFloat(m.lendGB) * per - 2))
                        .help("\(s.device(m.macID).flatMap { s.person($0.ownerID)?.name } ?? "A Mac"), \(m.lendGB) GB")
                }
                if mine > 0 { Rectangle().fill(D.accent).frame(width: max(2, CGFloat(mine) * per - 2)).help("You, \(mine) GB") }
                if target > pool.pooledGB {
                    RoundedRectangle(cornerRadius: 1).strokeBorder(D.accent.opacity(0.7), lineWidth: 1)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 2))
        }
        .frame(height: 4)
        .padding(.top, 16)
    }

    @ViewBuilder private func action(togo: Int) -> some View {
        let s = model.state
        let lent = s.share(in: pool.id)?.gb ?? 0
        let lendingHere = lent > 0 && s.lending.isOn
        let room = s.mostGB(for: pool.id) - lent
        if room >= togo {
            Button(lendingHere ? "Lend \(togo) GB More" : pool.isMember(s.meID) ? "Lend Here" : "Add My Mac") {
                if pool.isMember(s.meID) { model.setShare(pool, gb: lent + togo); model.setLendingOn(true) } else { model.join(pool) }
            }
            .buttonStyle(DButtonStyle(kind: .primary, large: true))
        }
    }

    private var bringAMac: some View {
        Button("Bring a Mac") { model.sheet = .invite(pool.id) }
            .buttonStyle(.plain).font(D.ui(13, .medium)).foregroundStyle(D.t3)
    }
}

// MARK: - Your contribution

/// What your Mac gives this pool right now, and the controls for it: lend here or not, how much, or leave. The most
/// it can lend here is what its other pools leave.
struct ContributionCard: View {
    @Environment(AppModel.self) private var model
    @Environment(\.isSnapshot) private var isSnapshot
    var pool: Pool
    @Binding var confirmLeave: Bool

    var body: some View {
        let s = model.state
        let shareGB = s.share(in: pool.id)?.gb ?? 0
        let here = shareGB > 0
        let on = here && s.lending.isOn
        let status = model.lendingStatus
        let maxLend = s.thisMac.maxLendGB
        let most = s.mostGB(for: pool.id)
        // What the pool counts for this Mac: its worker lends the share, or less when that much isn't free.
        // Until the worker is in the pool it is starting, and lends nothing yet.
        let me = pool.macs.first { $0.macID == s.thisMacID }
        let starting = on && status.isActive && me?.state == .online
        let lent = me?.state == .lending ? me?.lendGB ?? shareGB : shareGB
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Your Contribution").font(D.ui(13, .semibold)).foregroundStyle(D.title)
                Spacer()
                OrangeSwitch(isOn: Binding(get: { on }, set: { want in
                    if want {
                        if !here { model.setShare(pool, gb: s.offerGB(for: pool)) }
                        model.setLendingOn(true)
                    } else {
                        model.setShare(pool, gb: 0)
                    }
                }), label: "Lend in \(pool.name)")
                .disabled(!pool.canLend(s.thisMac) || (!here && s.offerGB(for: pool) == 0))
            }
            .frame(height: 20)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(starting ? "Starting" : on ? "\(lent) GB" : here ? "Paused" : "Off")
                    .font(D.ui(30, .semibold).monospacedDigit()).tracking(-0.6)
                    .foregroundStyle(on ? D.t1 : Color(hex: 0x8A8A8A))
                    .contentTransition(.numericText())
                HStack(spacing: 6) {
                    if status.isActive && here && !starting { Circle().fill(D.accent).frame(width: 6, height: 6).modifier(Blink(on: !isSnapshot)) }
                    Text(starting ? "your Mac is joining the pool" : note(here: here, on: on, most: most, status: status) + (on && lent < shareGB ? ", up to \(shareGB) GB" : ""))
                        .lineLimit(1)
                }
                .help(on && lent < shareGB ? "Only \(lent) GB is free on your Mac right now, so that is what it lends." : "")
                .font(D.ui(13)).foregroundStyle(Color(hex: 0x8A8A8A))
            }
            .padding(.top, 10)
            // The least this Mac must lend for the pool's model to run, given what the other Macs lend
            let others = pool.pooledGB - (me?.state == .lending ? me?.lendGB ?? 0 : 0)
            let least = pool.needsGB.map { max(0, $0 - others) } ?? 0
            ThinSlider(value: Binding(get: { max(1, shareGB) }, set: { model.setShare(pool, gb: $0) }),
                       range: 1...max(2, maxLend), mark: least > 0 && !pool.draws ? least : nil, enabled: on,
                       label: "Lend in \(pool.name)")
                .help(most < maxLend ? "Lend up to \(most) GB here; your other pools have the rest of the \(maxLend) GB this Mac lends."
                                     : "Lend 1 to \(maxLend) GB. \(s.thisMac.keptGB) GB always stays with your Mac.")
                .padding(.top, 12)
            if on && !pool.draws && least > 0 && shareGB < least {
                Text(others == 0 ? "Below \(least) GB, \(pool.name) can't run \(pool.modelLabel ?? "its model"): no other Mac lends here."
                                 : "Below \(least) GB, \(pool.name) can't run \(pool.modelLabel ?? "its model") with the other Macs here.")
                    .font(D.ui(12.5)).foregroundStyle(D.accentText).padding(.top, 10)
                    .fixedSize(horizontal: false, vertical: true)
            }
            LayerTicks(shares: shares(includeMe: on), live: on && model.backend.worker?.working == true)
                .padding(.top, 16)
            HStack {
                Text(layersLine(on: on)).font(D.ui(12)).foregroundStyle(D.label).lineLimit(1)
                Spacer()
                LeaveButton(title: "Leave Pool…") { confirmLeave = true }
            }
            .padding(.top, 10)
        }
        .panel()
    }

    private func note(here: Bool, on: Bool, most: Int, status: LendingStatus) -> String {
        let s = model.state
        if !here, let why = pool.whyCantLend(s.thisMac) { return why.replacingOccurrences(of: "Your Mac can't lend here: ", with: "") }
        if !here { return s.offerGB(for: pool) == 0 ? "your other pools have what this Mac lends" : "not lending here" }
        if !on { return "in this pool" }
        return status.isActive ? "lending now" : status.headline.lowercased()
    }

    /// The model's layers, shared out in proportion to the memory each lending Mac gives, in the order they joined.
    private func shares(includeMe: Bool) -> [(layers: Int, rgb: UInt32)] {
        let s = model.state
        if includeMe, let w = model.backend.worker, w.poolID == pool.id {
            return [(w.firstLayer, 0x4E4E4E), (w.lastLayer - w.firstLayer + 1, 0xFF5A1F), (max(0, w.layers - w.lastLayer - 1), 0x8A8A8A)]
        }
        // A pool that names its model is laid out by the hub, which says where this Mac's part is (above)
        guard !pool.runsOneModel, let layers = pool.model?.layers else { return [] }
        var macs = pool.macs.filter { $0.state == .lending && ($0.macID != s.thisMacID || includeMe) }.sorted { $0.joinedAt < $1.joinedAt }
        if includeMe, !macs.contains(where: { $0.macID == s.thisMacID }) {
            macs.append(PoolMac(macID: s.thisMacID, lendGB: s.share(in: pool.id)?.gb ?? 0, state: .lending, joinedAt: .now))
        }
        let crowd = pool.crowdGB
        let total = Double(max(1, macs.reduce(crowd) { $0 + $1.lendGB }))
        var out: [(layers: Int, rgb: UInt32)] = []
        var used = 0
        if crowd > 0 {
            let n = Int((Double(layers) * Double(crowd) / total).rounded())
            out.append((n, 0x4E4E4E))
            used += n
        }
        let greys: [UInt32] = [0x4E4E4E, 0x8A8A8A, 0x6A6A6A, 0xAAAAAA]
        for (i, m) in macs.enumerated() {
            let isMe = m.macID == s.thisMacID
            let n = i == macs.count - 1 ? layers - used : Int((Double(layers) * Double(m.lendGB) / total).rounded())
            out.append((max(0, n), isMe ? 0xFF5A1F : greys[(i + (crowd > 0 ? 1 : 0)) % greys.count]))
            used += max(0, n)
        }
        return out
    }

    private func layersLine(on: Bool) -> String {
        let s = model.state
        if on, let w = model.backend.worker, w.poolID == pool.id {
            return w.firstLayer == 0 && w.lastLayer + 1 == w.layers ? "Holds the whole model" : "Runs layers \(w.firstLayer + 1) to \(w.lastLayer + 1)"
        }
        // A pool that names its model is laid out by the hub; until the hub says, there's nothing to show
        if pool.runsOneModel { return on ? "Gets its part of the model when it's in" : "Not lending here" }
        if on, pool.model == nil { return "Layers are shared out once the pool runs a model" }
        if on {
            var start = 0
            for share in shares(includeMe: true) {
                if share.rgb == 0xFF5A1F { return "Runs layers \(start + 1) to \(start + share.layers)" }
                start += share.layers
            }
        }
        let names = pool.macs.filter { $0.state == .lending && $0.macID != s.thisMacID }
            .compactMap { s.device($0.macID).flatMap { s.person($0.ownerID)?.name } }
        if pool.draws {
            switch names.count {
            case 0: return "No one else draws here now"
            case 1: return "\(names[0])'s Mac draws here"
            default: return "\(names.dropLast().joined(separator: ", ")) and \(names.last!) draw here"
            }
        }
        switch names.count {
        case 0: return pool.isPublic ? "The pool carries the model" : "No one is lending here now"
        case 1: return "\(names[0]) carries the model"
        default: return "\(names.dropLast().joined(separator: ", ")) and \(names.last!) carry the model"
        }
    }
}

/// Before you join: what your Mac would add (what your other pools leave), and the button to join. With nothing left
/// to lend, joining still lets you ask the pool.
struct JoinCard: View {
    @Environment(AppModel.self) private var model
    var pool: Pool

    var body: some View {
        let s = model.state
        let add = s.offerGB(for: pool)
        let after = pool.pooledGB + add
        let next = pool.nextModel
        VStack(alignment: .leading, spacing: 0) {
            CardHeader("Your \(s.thisMac.kind)", note: "\(s.thisMac.memoryGB) GB")
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("+\(add) GB").font(D.ui(30, .semibold).monospacedDigit()).tracking(-0.6).foregroundStyle(D.t1)
                Text("if it lends here").font(D.ui(13)).foregroundStyle(Color(hex: 0x8A8A8A))
            }
            .padding(.top, 10)
            Group {
                if let why = pool.whyCantLend(s.thisMac) {
                    Text("\(why). Join to ask it all the same.").foregroundStyle(D.t3)
                } else if add == 0 {
                    Text("Your other pools have what this Mac lends. Join to ask \(pool.name), and give it a share later.")
                        .foregroundStyle(D.t3)
                } else if pool.runsOneModel {
                    Text(pool.draws ? "Enough to draw \(pool.name)'s pictures on your Mac." : "Shares out \(pool.modelLabel ?? "its model") with the Macs here.")
                        .foregroundStyle(D.t3)
                } else if let next, after >= next.minGB {
                    Text("Takes \(pool.name) to \(after.grouped) GB, past \(next.name)'s line, for everyone here.")
                        .foregroundStyle(D.accentText)
                } else if let next {
                    Text("Takes \(pool.name) to \(after.grouped) GB. \(next.name) needs \(next.minGB.grouped).").foregroundStyle(D.t3)
                } else {
                    Text("Makes \(pool.name) faster for everyone here.").foregroundStyle(D.t3)
                }
            }
            .font(D.ui(13)).fixedSize(horizontal: false, vertical: true).padding(.top, 10)
            Button(add > 0 ? "Add My Mac" : "Join \(pool.name)") { model.join(pool) }
                .buttonStyle(DButtonStyle(kind: .primary, large: true))
                .help(add > 0 ? "About \(pool.creditsPerHour(lendGB: add).counted("credit")) an hour while it lends." : "")
                .padding(.top, 16)
        }
        .panel()
    }
}

// MARK: - What happened

/// Thanks, people stepping away, unlocks and milestones, newest first.
struct ActivityCard: View {
    @Environment(AppModel.self) private var model
    var pool: Pool

    struct Item: Identifiable {
        var id: String
        var date: Date
        var face: String?
        var glyph: [String]?
        var text: String
        var dim = false
    }

    var body: some View {
        let items = Array(self.items.prefix(3))
        VStack(alignment: .leading, spacing: 0) {
            CardHeader("Activity")
            VStack(spacing: 0) {
                if items.isEmpty {
                    Text("Nothing yet.").font(D.ui(12.5)).foregroundStyle(D.label)
                        .help("Thanks, unlocks and milestones show up here.")
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 10)
                }
                ForEach(Array(items.enumerated()), id: \.element.id) { i, item in
                    HStack(spacing: 11) {
                        Group {
                            if let glyph = item.glyph {
                                DotGlyph(rows: glyph, size: 10).foregroundStyle(D.t1)
                            } else {
                                Text(item.face ?? "").font(.system(size: (item.face?.count ?? 1) > 1 ? 8 : 10.5, weight: .bold))
                                    .foregroundStyle(item.dim ? Color(hex: 0x8A8A8A) : D.t1)
                            }
                        }
                        .frame(width: 24, height: 24)
                        .background(Circle().fill(item.dim ? Color(hex: 0x262626) : Color(hex: 0x2E2E2E)))
                        Text(item.text).font(D.ui(13)).foregroundStyle(D.t2).lineLimit(1)
                        Spacer(minLength: 8)
                        Text(when(item.date)).font(D.ui(11.5).monospacedDigit()).foregroundStyle(Color(hex: 0x6E6E6E))
                    }
                    .frame(height: 36)
                    .overlay(alignment: .top) { if i > 0 { Rule() } }
                }
            }
            .padding(.top, 6)
        }
        .panel()
    }

    private var items: [Item] {
        let s = model.state
        var out: [Item] = []
        for a in pool.activity ?? [] {
            let person = a.personID.flatMap { s.person($0) }
            let name = person?.name ?? "Someone"
            switch a.kind {
            case "thanks": out.append(Item(id: a.id, date: a.date, face: person?.initial, text: "\(name) thanked your \(s.thisMac.kind)"))
            case "away": out.append(Item(id: a.id, date: a.date, face: person?.initial, text: "\(name) stepped away" + (a.gb.map { ", \($0) GB paused" } ?? ""), dim: true))
            case "boost", "night": out.append(Item(id: a.id, date: a.date, face: person?.initial, text: "\(name) planned \(pool.boost?.title ?? "a boost")"))
            default: out.append(Item(id: a.id, date: a.date, glyph: DotGlyph.sparkle, text: a.text ?? ""))
            }
        }
        // Unlocks are the ladder's: a pool that names its one model doesn't climb it
        for u in pool.unlocks where !pool.runsOneModel && (Ladder.tier(u.modelID)?.minGB ?? .max) <= pool.pooledGB {
            let tier = Ladder.tier(u.modelID)?.name ?? u.modelID
            let who = u.byMacID == s.thisMacID ? "Your \(s.thisMac.kind)" : s.device(u.byMacID).flatMap { s.person($0.ownerID)?.name } ?? "A Mac"
            out.append(Item(id: "unlock-\(u.modelID)", date: u.date, glyph: DotGlyph.padlock, text: "\(who) unlocked \(tier)"))
        }
        for m in pool.macs where m.joinedAt > Date().addingTimeInterval(-14 * 86_400) {
            guard let owner = s.device(m.macID).flatMap({ s.person($0.ownerID) }) else { continue }
            let isMe = m.macID == s.thisMacID
            out.append(Item(id: "joined-\(m.macID)", date: m.joinedAt, face: isMe ? "You" : owner.initial,
                            text: isMe ? "You joined with your \(s.thisMac.kind)" : "\(owner.name) joined"))
        }
        return out.sorted { $0.date > $1.date }
    }

    private func when(_ date: Date) -> String {
        if date > Date().addingTimeInterval(-18 * 3_600) { return date.formatted(date: .omitted, time: .shortened) }
        if Calendar.current.isDateInYesterday(date) { return "Yesterday" }
        return date.formatted(.dateTime.month(.abbreviated).day())
    }
}

/// A public pool's backing: how many endorse it, and whether you were early.
struct EndorseCard: View {
    var pool: Pool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            CardHeader("Endorsements", note: pool.endorsedByMe ? "Including yours" : nil)
            Text(pool.endorsements.grouped).font(D.ui(30, .semibold).monospacedDigit()).tracking(-0.6).foregroundStyle(D.t1)
                .padding(.top, 10)
            Text(pool.blurb).font(D.ui(13)).foregroundStyle(D.t3).fixedSize(horizontal: false, vertical: true).padding(.top, 8)
            if pool.endorsedByMe, let at = pool.endorsedAtMacs, at < 20 {
                HStack(spacing: 8) {
                    DotGlyph(rows: DotGlyph.sprout, size: 12).foregroundStyle(D.accentText)
                    Text("You're an early backer. You endorsed it at \(at.counted("Mac")).").font(D.ui(12.5, .medium)).foregroundStyle(D.t2)
                }
                .padding(.top, 12)
            }
        }
        .panel()
    }
}

// MARK: - Helpers

/// How far the pool is toward the next model, and the stretch your Mac would add.
struct GrowthBar: View {
    var current: Int
    var adding: Int
    var target: Int

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let scale = w / CGFloat(max(target, current + adding))
            ZStack(alignment: .leading) {
                Capsule().fill(D.raised)
                Capsule().fill(D.t3).frame(width: CGFloat(current) * scale)
                Rectangle().fill(D.accent)
                    .frame(width: max(3, CGFloat(adding) * scale))
                    .offset(x: CGFloat(current) * scale)
                if target < current + adding {
                    Rectangle().fill(D.t1).frame(width: 2, height: 16).offset(x: CGFloat(target) * scale - 1)
                }
            }
            .clipShape(Capsule())
        }
    }
}

/// A gentle blink for live dots.
struct Blink: ViewModifier {
    var on: Bool
    @State private var dim = false

    func body(content: Content) -> some View {
        content
            .opacity(dim ? 0.35 : 1)
            .onAppear {
                guard on else { return }
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { dim = true }
            }
    }
}

/// A button that gives a little when pressed.
struct PressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.scaleEffect(configuration.isPressed ? 0.97 : 1).animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

extension Date {
    /// "tonight", "this morning", "yesterday", "on Sep 28"
    var relativeDayPhrase: String {
        let cal = Calendar.current
        if cal.isDateInToday(self) {
            let h = cal.component(.hour, from: self)
            return h >= 18 || h < 4 ? "tonight" : h < 12 ? "this morning" : "this afternoon"
        }
        if cal.isDateInYesterday(self) { return "yesterday" }
        return "on " + formatted(.dateTime.month(.abbreviated).day())
    }
}
