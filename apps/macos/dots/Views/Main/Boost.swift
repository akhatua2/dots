import SwiftUI

extension PoolBoost {
    /// "Saturday, 2 to 6 PM" or "Friday, 10 PM to 2 AM"
    var whenShort: String { "\(starts.formatted(.dateTime.weekday(.wide))), \(Self.span(starts, ends))" }
    /// "Saturday, October 24 · 2 to 6 PM"
    var whenLong: String { "\(starts.formatted(.dateTime.weekday(.wide).month(.wide).day())) · \(Self.span(starts, ends))" }

    /// A time range the way people say it: the AM or PM once when both ends share it.
    static func span(_ a: Date, _ b: Date) -> String {
        func parts(_ d: Date) -> (time: String, pm: Bool) {
            let c = Calendar.current.dateComponents([.hour, .minute], from: d)
            let h = c.hour ?? 0, m = c.minute ?? 0
            let hour = h % 12 == 0 ? 12 : h % 12
            return (m == 0 ? "\(hour)" : String(format: "%d:%02d", hour, m), h >= 12)
        }
        let s = parts(a), e = parts(b)
        let suffix = { (pm: Bool) in pm ? "PM" : "AM" }
        return s.pm == e.pm ? "\(s.time) to \(e.time) \(suffix(e.pm))" : "\(s.time) \(suffix(s.pm)) to \(e.time) \(suffix(e.pm))"
    }

    /// What Count Me In does to your Mac, said once, on hover.
    func promise(gb: Int) -> String {
        "Your Mac stays awake and lends \(gb) GB from \(Self.span(starts, ends)). Keep it plugged in."
    }
}

/// A boost coming up, across the top of the pool: when, how close, who's boosting, and Count Me In.
struct BoostBanner: View {
    @Environment(AppModel.self) private var model
    var pool: Pool
    var boost: PoolBoost

    var body: some View {
        let s = model.state
        let mine = boost.pledge(of: s.meID)
        let togo = max(0, boost.targetGB - boost.pledgedGB)
        let gb = mine?.gb ?? s.thisMac.maxLendGB
        HStack(spacing: 16) {
            Button { model.sheet = .boost(pool.id) } label: {
                HStack(spacing: 14) {
                    CalendarTile(date: boost.starts)
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(spacing: 8) {
                            Text(boost.title).font(D.ui(14.5, .semibold)).foregroundStyle(D.t1)
                            if !boost.title.localizedCaseInsensitiveContains("boost") { CapsTag(text: "Boost", accent: true, plain: true) }
                        }
                        HStack(spacing: 8) {
                            Text(boost.whenShort)
                            BoostBar(boost: boost, meID: s.meID).frame(width: 72, height: 4)
                            Text(togo > 0 ? "\(togo.grouped) GB to go" : "\(Ladder.tier(boost.modelID)?.name ?? "It")'s on").monospacedDigit()
                        }
                        .font(D.ui(12.5)).foregroundStyle(D.t3)
                    }
                    Spacer(minLength: 8)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            HStack(spacing: 10) {
                AvatarStack(faces: faces).help(whoLine)
                Text("\(boost.pledges.count) boosting").font(D.ui(12).monospacedDigit()).foregroundStyle(Color(hex: 0x8A8A8A))
            }
            if mine == nil {
                Button("Count Me In") { model.backend.pledgeBoost(poolID: pool.id, gb: s.thisMac.maxLendGB) }
                    .buttonStyle(TintButtonStyle())
                    .disabled(s.thisMac.maxLendGB == 0)
                    .help(boost.promise(gb: gb))
            } else {
                Button { model.backend.pledgeBoost(poolID: pool.id, gb: nil) } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "checkmark").font(.system(size: 10, weight: .heavy)).foregroundStyle(D.accentText)
                        Text("Boosting")
                    }
                }
                .buttonStyle(TintButtonStyle(neutral: true))
                .help(boost.promise(gb: gb).replacingOccurrences(of: "Keep it plugged in.", with: "Click to back out."))
            }
        }
        .padding(.leading, 14).padding(.trailing, 16)
        .frame(height: 72)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(D.card))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(D.panelEdge, lineWidth: 0.5))
    }

    private var faces: [(text: String, isYou: Bool, dim: Bool)] {
        let s = model.state
        return boost.pledges.prefix(7).map { p in
            p.personID == s.meID ? ("You", true, false) : (s.person(p.personID)?.initial ?? "?", false, p.guest)
        }
    }

    /// Who's boosting, with the guests named apart: on hover.
    private var whoLine: String {
        let s = model.state
        func names(_ ps: [PoolBoost.Pledge]) -> String {
            ps.map { $0.personID == s.meID ? "you" : s.person($0.personID)?.name ?? "someone" }.joined(separator: ", ")
        }
        let members = boost.pledges.filter { !$0.guest }, guests = boost.pledges.filter(\.guest)
        return [members.isEmpty ? nil : names(members).capitalizedFirst,
                guests.isEmpty ? nil : "Guests, boosting just this once: \(names(guests))"].compactMap { $0 }.joined(separator: ". ")
    }
}

/// The boost's pledges as one thin bar: members, guests, you, and what's still to come.
struct BoostBar: View {
    var boost: PoolBoost
    var meID: String

    var body: some View {
        GeometryReader { geo in
            let per = geo.size.width / CGFloat(max(boost.targetGB, boost.pledgedGB, 1))
            let members = boost.pledges.filter { !$0.guest && $0.personID != meID }.reduce(0) { $0 + $1.gb }
            let guests = boost.pledges.filter { $0.guest && $0.personID != meID }.reduce(0) { $0 + $1.gb }
            let mine = boost.pledge(of: meID)?.gb ?? 0
            HStack(spacing: 1) {
                Rectangle().fill(Color(hex: 0x8A8A8A)).frame(width: CGFloat(members) * per)
                Rectangle().fill(Color(hex: 0xD6D6D6)).frame(width: CGFloat(guests) * per)
                if mine > 0 { Rectangle().fill(D.accent).frame(width: CGFloat(mine) * per) }
                Spacer(minLength: 0)
            }
            .background(Color(hex: 0x333333))
            .clipShape(RoundedRectangle(cornerRadius: 2))
        }
        .animation(.easeOut(duration: 0.4), value: boost.pledgedGB)
        .help("\(boost.pledgedGB.grouped) of \(boost.targetGB.grouped) GB")
    }
}

/// A button with an orange tint: a second action that still matters, like Count Me In next to the page's main one.
struct TintButtonStyle: ButtonStyle {
    var neutral = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(D.ui(13, .semibold))
            .foregroundStyle(neutral ? D.t2 : Color(hex: 0xFF9A70))
            .padding(.horizontal, 14).frame(height: 30)
            .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(neutral ? Color.white.opacity(0.06) : D.accent.opacity(0.18)))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(neutral ? Color.white.opacity(0.14) : D.accent.opacity(0.5), lineWidth: 0.5))
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .contentShape(Rectangle())
    }
}

// MARK: - The sheet

/// A boost: members commit their Macs to one window, so the pool can run a bigger model for a few hours.
/// A dot for every 3 GB pledged.
struct BoostSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    var poolID: String

    var body: some View {
        let s = model.state
        if let pool = s.pool(poolID), let boost = pool.boost {
            let mine = boost.pledge(of: s.meID)
            let togo = max(0, boost.targetGB - boost.pledgedGB)
            let tier = Ladder.tier(boost.modelID)
            let gb = mine?.gb ?? s.thisMac.maxLendGB
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 16) {
                        CalendarTile(date: boost.starts, width: 54)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("BOOST").font(D.ui(10.5, .bold)).tracking(1.4).foregroundStyle(D.accentText)
                            Text(boost.title).font(D.ui(24, .bold)).foregroundStyle(D.t1)
                            Text("\(boost.whenLong) · planned by \(s.person(boost.plannedBy)?.name ?? "the host")")
                                .font(D.ui(13)).foregroundStyle(Color(hex: 0x8A8A8A))
                        }
                    }
                    Text("Reach \(boost.targetGB.grouped) GB together, and \(pool.name) runs \(tier?.name ?? "it") until \(boost.ends.formatted(.dateTime.hour())).")
                        .font(D.ui(14)).foregroundStyle(Color(hex: 0xC8C8C8)).lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 20)

                    VStack(spacing: 0) {
                        ZStack {
                            BoostGauge(boost: boost, meID: s.meID, mine: Double(mine?.gb ?? 0))
                                .frame(width: 236, height: 236)
                            VStack(spacing: 2) {
                                Text(boost.pledgedGB.grouped).font(D.ui(40, .semibold).monospacedDigit()).tracking(-1).foregroundStyle(D.t1)
                                    .contentTransition(.numericText())
                                Text("of \(boost.targetGB.grouped) GB").font(D.ui(12)).foregroundStyle(Color(hex: 0x8A8A8A))
                            }
                        }
                        Text(togo > 0 ? "\(togo.grouped) GB to go" : "\(tier?.name ?? "It")'s on")
                            .font(D.ui(12.5, .semibold).monospacedDigit()).foregroundStyle(D.t1)
                            .frame(height: 28)
                            .padding(.top, 8)
                    }
                    .stage(padding: EdgeInsets(top: 18, leading: 18, bottom: 10, trailing: 18))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 18)

                    VStack(alignment: .leading, spacing: 0) {
                        CardHeader("Boosting", note: "\(boost.pledges.count) Macs")
                        HStack(spacing: 12) {
                            ForEach(boost.pledges, id: \.personID) { p in
                                let isMe = p.personID == s.meID
                                let name = isMe ? "You" : s.person(p.personID)?.name ?? "Someone"
                                VStack(spacing: 5) {
                                    Text(isMe ? "You" : s.person(p.personID)?.initial ?? "?")
                                        .font(.system(size: isMe ? 11 : 14, weight: .bold))
                                        .foregroundStyle(isMe ? D.onAccent : p.guest ? D.t2 : D.t1)
                                        .frame(width: 40, height: 40)
                                        .background(Circle().fill(isMe ? D.accent : p.guest ? Color.clear : Color(hex: 0x2A2A2A)))
                                        .overlay { if p.guest && !isMe { Circle().strokeBorder(Color(hex: 0x6E6E6E), style: StrokeStyle(lineWidth: 1.5, dash: [3, 3])) } }
                                    Text("\(p.gb) GB").font(D.ui(11.5).monospacedDigit()).foregroundStyle(isMe ? D.accentText : Color(hex: 0x8A8A8A))
                                }
                                .frame(width: 52)
                                .help(p.guest && !isMe ? "\(name), a guest: boosting just this once" : name)
                            }
                        }
                        .padding(.top, 14)
                    }
                    .panel()
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 14)
                }
                .padding(.horizontal, 32).padding(.top, 30).padding(.bottom, 22)

                Rectangle().fill(Color.white.opacity(0.12)).frame(height: 0.5)
                HStack(spacing: 12) {
                    Button("Back") { dismiss() }.buttonStyle(.plain).font(D.ui(13.5, .medium)).foregroundStyle(D.t3)
                    Spacer()
                    Button("Bring a Booster") { dismiss(); model.sheet = .invite(pool.id) }
                        .buttonStyle(DButtonStyle(kind: .grey, large: true))
                    if mine == nil {
                        Button("Count Me In") { model.backend.pledgeBoost(poolID: pool.id, gb: s.thisMac.maxLendGB) }
                            .buttonStyle(DButtonStyle(kind: .primary, large: true))
                            .keyboardShortcut(.defaultAction)
                            .disabled(s.thisMac.maxLendGB == 0)
                            .help(boost.promise(gb: gb))
                    } else {
                        Button { model.backend.pledgeBoost(poolID: pool.id, gb: nil) } label: {
                            HStack(spacing: 7) { Image(systemName: "checkmark").font(.system(size: 11, weight: .heavy)); Text("Boosting") }
                        }
                        .buttonStyle(TintButtonStyle())
                        .help(boost.promise(gb: gb).replacingOccurrences(of: "Keep it plugged in.", with: "Click to back out."))
                    }
                }
                .padding(.horizontal, 32).frame(height: 66)
            }
            .frame(width: 560)
            .background(Color(hex: 0x1A1A1A))
            .preferredColorScheme(.dark)
        }
    }
}

/// The boost's gauge: a ring of dots, one for every 3 GB, clockwise from the top. Members, then guests, then you.
struct BoostGauge: View, Animatable {
    var boost: PoolBoost
    var meID: String
    var mine: Double

    var animatableData: Double {
        get { mine }
        set { mine = newValue }
    }

    /// How many dots (3 GB each) the pledges of members, or of guests, fill. You are counted separately.
    private func dots(guest: Bool) -> Int {
        let others = boost.pledges.filter { $0.guest == guest && $0.personID != meID }
        return others.reduce(0) { $0 + $1.gb } / 3
    }

    private enum DotColor {
        static let member: UInt32 = 0x8A8A8A
        static let guest: UInt32 = 0xD6D6D6
        static let you: UInt32 = 0xFF5A1F
        static let empty: UInt32 = 0x262626
    }

    var body: some View {
        Canvas { ctx, sz in
            let w = sz.width
            let c = w / 2, r = w * 0.43
            let total = max(1, boost.targetGB / 3)
            let members = dots(guest: false)
            let guests = dots(guest: true)
            let you = Int((mine / 3).rounded())
            let size = w * 7 / 472

            // One path per colour, so the ring is a handful of fills and not one per dot.
            var paths: [UInt32: Path] = [:]
            for i in 0..<total {
                let angle = Double(i) / Double(total) * 2 * .pi - .pi / 2
                let colour: UInt32
                if i < members { colour = DotColor.member }
                else if i < members + guests { colour = DotColor.guest }
                else if i < members + guests + you { colour = DotColor.you }
                else { colour = DotColor.empty }
                let x = (c + r * cos(angle) - size / 2).rounded()
                let y = (c + r * sin(angle) - size / 2).rounded()
                paths[colour, default: Path()].addRect(CGRect(x: x, y: y, width: size, height: size))
            }
            for (colour, path) in paths { ctx.fill(path, with: .color(Color(hex: colour))) }
            ctx.fill(Path(CGRect(x: c - 1, y: c - r - size * 1.9, width: 2, height: size * 1.15)), with: .color(D.accentText))
        }
        .accessibilityElement()
        .accessibilityLabel("\(boost.pledgedGB) of \(boost.targetGB) GB pledged")
        .help("A dot for every 3 GB: members, then guests, then you")
    }
}
