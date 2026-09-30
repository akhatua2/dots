import SwiftUI

struct PoolView: View {
    @Environment(AppModel.self) private var model
    var pool: Pool

    var body: some View {
        let s = model.state
        let isMember = pool.isMember(s.meID)
        let lendingHere = s.lendingPoolID == pool.id
        let myUnlock = pool.unlocks.last { $0.byMacID == s.thisMacID }

        LiveScroll {
            VStack(alignment: .leading, spacing: 0) {
                header(isMember: isMember)

                if let unlock = myUnlock, let tier = Ladder.tier(unlock.modelID) {
                    UnlockBanner(pool: pool, tier: tier, event: unlock, macKind: s.thisMac.kind)
                        .padding(.top, 22)
                }

                if !isMember || !lendingHere {
                    JoinPreview(pool: pool, isMember: isMember)
                        .padding(.top, 22)
                }

                section("Macs lending now · \(pool.lendingMacs.grouped)") {
                    LendingFaces(pool: pool)
                }

                section("What it can run") {
                    ModelLadder(pool: pool)
                }

                if pool.isPublic {
                section("Endorsements") {
                    VStack(alignment: .leading, spacing: 10) {
                        IconLine(symbol: "checkmark.seal", text: "\(pool.endorsements.grouped) endorsements", color: D.t2, size: 13.5, weight: .medium)
                        if pool.endorsedByMe, let at = pool.endorsedAtMacs, at < 20 {
                            HStack(spacing: 7) {
                                Image(systemName: "checkmark.seal.fill").font(.system(size: 12))
                                Text("You're an early backer. You endorsed it at \(at) Macs.")
                            }
                            .font(D.ui(13, .semibold)).foregroundStyle(D.t1)
                            .padding(.horizontal, 12).frame(height: 30)
                            .background(Capsule().fill(D.raised))
                            .overlay(Capsule().strokeBorder(Color.white.opacity(0.08)))
                        }
                    }
                }
                }

                if isMember && !pool.isPublic {
                    section("Friends") {
                        FriendsPoolControls(pool: pool)
                    }
                }

                if isMember {
                    Button("Leave \(pool.name)") { model.leave(pool) }
                        .buttonStyle(DButtonStyle(kind: .quiet))
                        .padding(.top, 28)
                }
            }
            .padding(.horizontal, 32)
            .padding(.top, 34)
            .padding(.bottom, 40)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func header(isMember: Bool) -> some View {
        HStack(alignment: .top, spacing: 16) {
            EmblemView(macs: pool.totalMacs, phase: pool.phase, size: 60)
            VStack(alignment: .leading, spacing: 6) {
                Text(pool.name).font(D.ui(25, .bold)).foregroundStyle(D.t1)
                Text([pool.isPublic ? "Public pool" : "Friends' pool",
                      pool.model?.name ?? "No model yet",
                      "\(pool.totalMacs.grouped) Macs",
                      "\(pool.pooledGB.grouped) GB"].joined(separator: " · "))
                    .font(D.ui(13.5)).foregroundStyle(D.t3)
            }
            Spacer()
            if pool.isPublic {
                if pool.endorsedByMe {
                    Label("Endorsed", systemImage: "checkmark.seal.fill")
                        .font(D.ui(13, .semibold)).foregroundStyle(D.t1)
                        .padding(.horizontal, 14).frame(height: 32)
                        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(D.btn))
                } else {
                    HoverAccentButton(title: "Endorse", symbol: "checkmark.seal") { model.endorse(pool) }
                }
            }
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            CapLabel(title)
            content()
        }
        .padding(.top, 30)
    }
}

/// "Your MacBook Pro unlocked DeepSeek V3.1 here tonight."
struct UnlockBanner: View {
    var pool: Pool
    var tier: ModelTier
    var event: UnlockEvent
    var macKind: String

    var body: some View {
        HStack(spacing: 12) {
            Circle().fill(D.accent).frame(width: 9, height: 9)
                .shadow(color: D.accent.opacity(0.7), radius: 6)
            Text("Your \(macKind) unlocked \(tier.name) here \(event.date.relativeDayPhrase).")
                .font(D.ui(14.5, .semibold)).foregroundStyle(D.t1)
            Spacer()
            Text("It took the pool from \(event.fromGB.grouped) to \(event.toGB.grouped) GB")
                .font(D.ui(13)).foregroundStyle(D.t4)
        }
        .padding(.horizontal, 18)
        .frame(height: 54)
        .card()
    }
}

/// Before you lend here: what your Mac would add, and the button to add it.
struct JoinPreview: View {
    @Environment(AppModel.self) private var model
    var pool: Pool
    var isMember: Bool

    var body: some View {
        let s = model.state
        let add = s.lending.limitGB
        let after = pool.pooledGB + add
        let next = pool.nextModel
        // Your Mac lends to one pool at a time
        let elsewhere = s.pool(s.lendingPoolID).flatMap { $0.id == pool.id ? nil : $0 }
        HStack(alignment: .center, spacing: 20) {
            VStack(alignment: .leading, spacing: 8) {
                Text(elsewhere != nil ? "Move your \(s.thisMac.kind) here" : isMember ? "Lend your \(s.thisMac.kind) here" : "Add your \(s.thisMac.kind)")
                    .font(D.ui(16, .bold)).foregroundStyle(D.t1)
                if let next {
                    if after >= next.minGB {
                        Text("Your \(add) GB takes \(pool.name) to \(after.grouped) GB, past \(next.name)'s line. You'd unlock it for everyone here.")
                            .font(D.ui(13)).foregroundStyle(D.t2)
                    } else {
                        Text("Your \(add) GB takes \(pool.name) to \(after.grouped) GB. \(next.name) needs \(next.minGB.grouped).")
                            .font(D.ui(13)).foregroundStyle(D.t3)
                    }
                } else {
                    Text("Your \(add) GB makes it faster for everyone here.").font(D.ui(13)).foregroundStyle(D.t3)
                }
                if let next {
                    GrowthBar(current: pool.pooledGB, adding: add, target: next.minGB)
                        .frame(height: 8)
                        .padding(.vertical, 6)
                }
                Text("About \(pool.creditsPerHour(lendGB: max(1, add))) credits an hour while it lends. \(s.thisMac.keptGB) GB always stays with your Mac.")
                    .font(D.ui(12)).foregroundStyle(D.t4)
                if let elsewhere {
                    let now = elsewhere.model
                    let after = Ladder.best(for: elsewhere.pooledGB - add)
                    Text(now != nil && after != nil && after!.minGB < now!.minGB
                         ? "It stops lending to \(elsewhere.name), which drops back to \(after!.name)."
                         : "It stops lending to \(elsewhere.name).")
                        .font(D.ui(12)).foregroundStyle(D.t3)
                }
            }
            Spacer(minLength: 10)
            HoverAccentButton(title: elsewhere != nil ? "Lend here instead" : isMember ? "Lend my Mac here" : "Add my Mac",
                              large: true, alwaysAccent: true) {
                model.join(pool)
            }
        }
        .padding(18)
        .card()
    }
}

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
                Rectangle().fill(D.t1).frame(width: 2, height: 16).offset(x: CGFloat(target) * scale - 1)
            }
            .clipShape(Capsule())
        }
    }
}

struct LendingFaces: View {
    @Environment(AppModel.self) private var model
    var pool: Pool

    var body: some View {
        let s = model.state
        let known = pool.macs.compactMap { m -> (PoolMac, MacDevice, Person)? in
            guard let d = s.device(m.macID), let p = s.person(d.ownerID) else { return nil }
            return (m, d, p)
        }
        let faces = pool.crowdLending > 0 ? SampleData.faces(for: pool) : []
        let others = max(0, pool.lendingMacs - faces.count - known.filter { $0.0.state == .lending }.count)

        HStack(spacing: 8) {
            ForEach(faces.indices, id: \.self) { i in Avatar(text: faces[i], size: 32) }
            ForEach(known, id: \.0.macID) { m, d, p in
                let isMe = d.id == s.thisMacID
                Avatar(text: isMe ? "You" : p.initial, size: 32, isYou: isMe && m.state == .lending,
                       dimmed: m.state != .lending)
                    .overlay(alignment: .bottomTrailing) {
                        if m.state == .asleep {
                            Image(systemName: "moon.fill").font(.system(size: 9)).foregroundStyle(D.t3)
                                .padding(3).background(Circle().fill(D.bg2)).offset(x: 3, y: 3)
                        }
                    }
                    .help(isMe ? "Your \(d.kind)" : "\(p.name)'s \(d.kind), \(m.lendGB) GB")
            }
            if others > 0 {
                Text("+\(others.grouped)").font(D.ui(12, .semibold)).foregroundStyle(D.t3)
                    .padding(.horizontal, 10).frame(height: 32)
                    .background(Capsule().fill(D.raised))
            }
        }
    }
}

/// The models the pool runs now, and the next one with how far there is to go.
struct ModelLadder: View {
    @Environment(AppModel.self) private var model
    var pool: Pool

    var body: some View {
        let gb = pool.pooledGB
        let unlocked = Ladder.tiers.filter { $0.minGB <= gb }.suffix(2)
        let next = pool.nextModel
        VStack(spacing: 8) {
            ForEach(Array(unlocked)) { tier in
                let event = pool.unlocks.last { $0.modelID == tier.id }
                row(icon: "checkmark", name: tier.name, detail: event.map { "Unlocked \($0.date.relativeDayPhrase) at \(tier.minGB.grouped) GB" }
                    ?? "Unlocked at \(tier.minGB.grouped) GB", on: true, highlight: event?.byMacID == model.state.thisMacID)
            }
            if let next {
                row(icon: "lock", name: next.name, detail: "\((next.minGB - gb).grouped) GB to go", on: false, highlight: false,
                    progress: Double(gb) / Double(next.minGB))
            }
        }
    }

    private func row(icon: String, name: String, detail: String, on: Bool, highlight: Bool, progress: Double? = nil) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon).font(.system(size: 11, weight: .bold)).foregroundStyle(on ? D.t2 : D.t4).frame(width: 16)
            Text(name).font(D.ui(14, .semibold)).foregroundStyle(on ? D.t1 : D.t3).frame(width: 150, alignment: .leading)
            Text(detail).font(D.ui(13)).foregroundStyle(highlight ? D.t2 : D.t4)
            Spacer()
            if let progress {
                ZStack(alignment: .leading) {
                    Capsule().fill(D.raised).frame(width: 240, height: 6)
                    Capsule().fill(D.swirl).frame(width: 240 * min(1, progress), height: 6)
                }
            }
        }
        .padding(.horizontal, 16)
        .frame(height: 46)
        .card()
    }
}

/// Invites and the network switch, for pools you share with people you know.
struct FriendsPoolControls: View {
    @Environment(AppModel.self) private var model
    var pool: Pool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Invite friends").font(D.ui(14, .semibold)).foregroundStyle(D.t1)
                    Text(pool.invite.map { "Link active until \($0.expires.formatted(date: .abbreviated, time: .omitted))" }
                         ?? "A link that shows what their Mac would add")
                        .font(D.ui(12.5)).foregroundStyle(D.t4)
                }
                Spacer()
                HoverAccentButton(title: pool.invite == nil ? "Make a link" : "Share link", symbol: "link") {
                    model.sheet = .invite(pool.id)
                }
            }
            .padding(16)
            .card()
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Offer idle hours to the network").font(D.ui(14, .semibold)).foregroundStyle(D.t1)
                    Text("When no one here is asking, public pools can use these Macs, and everyone here earns credits.")
                        .font(D.ui(12.5)).foregroundStyle(D.t4)
                }
                Spacer()
                DSwitch(isOn: Binding(get: { pool.offersIdleToNetwork },
                                      set: { model.backend.setOffersIdle(poolID: pool.id, $0) }),
                        label: "Offer idle hours to the network")
            }
            .padding(16)
            .card()
        }
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
