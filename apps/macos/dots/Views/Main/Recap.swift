import SwiftUI

/// What last night came to for this Mac: the pool it ended the night in, how long it lent, what it ran and earned,
/// and who thanked it.
struct NightRecap {
    var night: NightLent
    var pool: Pool
    var earned: Int
    var thanks: [PoolActivity]
    /// Hours of that night your Mac boosted the pool, if you were counted in
    var boosted: Double?

    /// Last night, as seen in the morning: before noon it's the night still on the clock; later, the one before.
    static func last(in s: NetworkState, now: Date) -> NightRecap? {
        let hour = Calendar.current.component(.hour, from: now)
        let night = hour < 12 ? now.nightStart : Calendar.current.date(byAdding: .day, value: -1, to: now.nightStart) ?? now.nightStart
        guard let lent = (s.nights ?? []).last(where: { $0.date == night }), let pool = s.pool(lent.poolID) else { return nil }
        let end = night.addingTimeInterval(86_400)
        let earned = s.creditLog.filter { ($0.kind == "lend" || $0.kind == "network") && $0.poolID == pool.id && $0.date >= night && $0.date < end }
            .reduce(0) { $0 + max(0, $1.amount) }
        let thanks = (pool.activity ?? []).filter { $0.kind == "thanks" && $0.date >= night && $0.date < end }
        let boosted = pool.boost.flatMap { b -> Double? in
            guard b.pledge(of: s.meID) != nil, b.starts < end, b.ends > night else { return nil }
            return b.hours
        }
        return NightRecap(night: lent, pool: pool, earned: earned, thanks: thanks, boosted: boosted)
    }

    var lent: String { Self.duration(night.minutes) }

    func headline(macKind: String) -> String {
        "Your \(macKind) lent \(lent) to \(pool.name) last night and earned \(earned.grouped) credit\(earned == 1 ? "" : "s")."
    }

    /// 400 minutes -> "6 h 40 m"
    static func duration(_ minutes: Double) -> String {
        let m = Int(minutes.rounded())
        return m >= 60 ? (m % 60 == 0 ? "\(m / 60) h" : "\(m / 60) h \(m % 60) m") : "\(m) m"
    }
}

/// The morning after: last night's edition, what your Mac gave and got, and the night's timeline.
struct RecapSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.artClock) private var artClock

    var body: some View {
        let s = model.state
        let now = artClock ?? .now
        if let recap = NightRecap.last(in: s, now: now) {
            let history = PoolHistory.make(recap.pool, thisMacID: s.thisMacID, now: now)
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Good morning, \(s.me.name)").font(D.ui(26, .bold)).foregroundStyle(D.t1)
                        Text("Last night in \(recap.pool.name)").font(D.ui(13)).foregroundStyle(Color(hex: 0x8A8A8A))
                    }
                    HStack(alignment: .top, spacing: 16) {
                        VStack(spacing: 6) {
                            RingsArt(history: history, size: 240, cells: 120, lastNights: 30, showNow: false)
                            HStack(spacing: 8) {
                                Text("No. \(history.nights.count)").font(D.ui(12, .semibold)).foregroundStyle(D.t2)
                                Text(recap.night.date.formatted(.dateTime.month(.wide).day())).font(D.ui(12)).foregroundStyle(Color(hex: 0x6E6E6E))
                                Spacer()
                                IconButton(symbol: "arrow.down.to.line", help: "Download") {
                                    ArtExport.save(pool: recap.pool, history: history, thisMacKind: s.thisMac.kind)
                                }
                                .padding(.trailing, -6)
                            }
                            .frame(height: 26)
                        }
                        .stage(padding: EdgeInsets(top: 16, leading: 16, bottom: 10, trailing: 16))
                        .frame(width: 272)
                        macCard(recap)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    NightTimeline(recap: recap, history: history, thisMacID: s.thisMacID)
                }
                .padding(.horizontal, 28).padding(.top, 26).padding(.bottom, 22)
                Rectangle().fill(Color.white.opacity(0.07)).frame(height: 0.5)
                HStack(spacing: 18) {
                    Spacer()
                    Button("Done") { dismiss() }.buttonStyle(.plain).font(D.ui(13, .medium)).foregroundStyle(D.t3)
                    Button("Open \(recap.pool.name)") { model.route = .pool(recap.pool.id); dismiss() }
                        .buttonStyle(DButtonStyle(kind: .primary, large: true))
                        .keyboardShortcut(.defaultAction)
                }
                .padding(.horizontal, 28).frame(height: 62)
            }
            .frame(width: 720)
            .background(D.bg2)
            .preferredColorScheme(.dark)
        } else {
            Text("No night to recap yet.").font(D.ui(13)).foregroundStyle(D.t3).padding(40).frame(width: 400)
        }
    }

    private func macCard(_ recap: NightRecap) -> some View {
        let s = model.state
        let n = recap.night
        let window = n.from.flatMap { from in n.to.map { "\(from.formatted(date: .omitted, time: .shortened)) to \($0.formatted(date: .omitted, time: .shortened))" } }
        return VStack(alignment: .leading, spacing: 0) {
            CardHeader("Your \(s.thisMac.kind)", note: window)
            Spacer(minLength: 14)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("+\(recap.earned.grouped)").font(D.ui(44, .semibold).monospacedDigit()).tracking(-1.2).foregroundStyle(D.accentText)
                Text("credits earned").font(D.ui(15)).foregroundStyle(D.t3)
            }
            Rule().padding(.top, 22)
            StatRow(stats: [.init(value: recap.lent, label: "lent"), .init(value: n.runs.grouped, label: "runs"),
                            .init(value: Runs.tokens(n.runs), label: "tokens")], size: 19)
                .padding(.top, 16)
            Spacer(minLength: 16)
            if let hours = recap.boosted {
                HStack(spacing: 10) {
                    DotGlyph(rows: DotGlyph.sparkle, size: 10).foregroundStyle(D.t1)
                        .frame(width: 24, height: 24).background(Circle().fill(Color(hex: 0x2E2E2E)))
                    Text("Your \(s.thisMac.kind) boosted \(recap.pool.name) for \(hours == 1 ? "an hour" : hours == hours.rounded() ? "\(Int(hours)) hours" : String(format: "%.1f hours", hours))")
                        .font(D.ui(13)).foregroundStyle(D.t2)
                    Spacer()
                }
                .padding(.top, 14)
                .overlay(alignment: .top) { Rule() }
            }
            if let thanks = recap.thanks.first, let person = thanks.personID.flatMap({ s.person($0) }) {
                HStack(spacing: 10) {
                    Text(person.initial).font(.system(size: 10.5, weight: .bold)).foregroundStyle(D.t1)
                        .frame(width: 24, height: 24).background(Circle().fill(Color(hex: 0x2E2E2E)))
                    Text("\(person.name) thanked your \(s.thisMac.kind)").font(D.ui(13)).foregroundStyle(D.t2)
                    Spacer()
                    DotGlyph(rows: DotGlyph.heart, size: 12).foregroundStyle(D.accentText)
                }
                .padding(.top, 14)
                .overlay(alignment: .top) { Rule() }
            }
        }
        .panel()
    }
}

/// The night, Mac by Mac: when each one lent, from the pool's own record of the night.
struct NightTimeline: View {
    var recap: NightRecap
    var history: PoolHistory
    var thisMacID: String
    @Environment(AppModel.self) private var model

    struct Row: Identifiable {
        var id: String
        var name: String
        var isYou: Bool
        var start: Double
        var end: Double
    }

    var body: some View {
        let rows = self.rows
        let lo = floor(rows.map(\.start).min() ?? 9)
        let hi = ceil(rows.map(\.end).max() ?? 19)
        let span = max(1, hi - lo)
        VStack(alignment: .leading, spacing: 0) {
            CardHeader("The night", note: "\(rows.count) Mac\(rows.count == 1 ? "" : "s")")
            VStack(spacing: 9) {
                ForEach(rows) { row in
                    HStack(spacing: 12) {
                        Text(row.name).font(D.ui(12.5, row.isYou ? .semibold : .regular)).foregroundStyle(row.isYou ? D.accentText : D.t3)
                            .frame(width: 52, alignment: .leading)
                        GeometryReader { geo in
                            let w = geo.size.width
                            DotRun(color: row.isYou ? D.accent : Color(hex: 0x9A9A9A))
                                .frame(width: max(4, w * (row.end - row.start) / span))
                                .offset(x: w * (row.start - lo) / span)
                        }
                        .frame(height: 8)
                        Text(NightRecap.duration((row.end - row.start) * 60)).font(D.ui(12.5).monospacedDigit())
                            .foregroundStyle(row.isYou ? D.accentText : D.label)
                            .frame(width: 72, alignment: .trailing)
                    }
                }
                HStack(spacing: 12) {
                    Color.clear.frame(width: 52, height: 1)
                    GeometryReader { geo in
                        ForEach(Array(stride(from: lo, through: hi, by: 2)), id: \.self) { h in
                            Text(Self.clock(h)).font(D.ui(10.5)).foregroundStyle(D.t5)
                                .fixedSize()
                                .position(x: geo.size.width * (h - lo) / span, y: 7)
                        }
                    }
                    .frame(height: 14)
                    Color.clear.frame(width: 72, height: 1)
                }
            }
            .padding(.top, 14)
        }
        .panel()
        .fixedSize(horizontal: false, vertical: true)
    }

    /// Each Mac's first and last run that night, in hours since noon; yours as it actually lent.
    private var rows: [Row] {
        let s = model.state
        guard let range = history.nights.last else { return [] }
        var spans: [Int: (Double, Double)] = [:]
        for i in range {
            let seed = history.seeds[i]
            let h = PoolHistory.intoNight(seed.hour)
            let cur = spans[seed.owner] ?? (h, h)
            spans[seed.owner] = (min(cur.0, h), max(cur.1, h))
        }
        var out: [Row] = spans.compactMap { owner, span in
            let id = history.owners[owner]
            guard id != thisMacID else { return nil }
            let name = id == "crowd" ? "Others" : s.device(id).flatMap { s.person($0.ownerID)?.name } ?? "A Mac"
            return Row(id: id, name: name, isYou: false, start: span.0, end: max(span.1, span.0 + 0.5))
        }
        .sorted { $0.start < $1.start }
        let n = recap.night
        if let from = n.from, let to = n.to {
            out.append(Row(id: thisMacID, name: "You", isYou: true, start: Self.hours(from), end: max(Self.hours(to), Self.hours(from) + n.minutes / 60)))
        } else if let you = history.you, let span = spans[you] {
            out.append(Row(id: thisMacID, name: "You", isYou: true, start: span.0, end: span.0 + n.minutes / 60))
        }
        return out
    }

    private static func hours(_ date: Date) -> Double {
        let c = Calendar.current.dateComponents([.hour, .minute], from: date)
        return PoolHistory.intoNight(Double(c.hour ?? 0) + Double(c.minute ?? 0) / 60)
    }

    /// Hours since noon as a clock: 9 -> "9 PM", 13 -> "1 AM"
    private static func clock(_ h: Double) -> String {
        let hour = (Int(h) + 12) % 24
        return hour == 0 ? "12 AM" : hour == 12 ? "12 PM" : hour < 12 ? "\(hour) AM" : "\(hour - 12) PM"
    }
}

/// A run of dots filling its width: a stretch of time someone lent.
struct DotRun: View {
    var color: Color

    var body: some View {
        Canvas { ctx, sz in
            var path = Path()
            var x: CGFloat = 0
            while x + 3 <= sz.width {
                for row in 0..<2 {
                    path.addEllipse(in: CGRect(x: x + 0.6, y: CGFloat(row) * 4 + 0.6, width: 2.4, height: 2.4))
                }
                x += 4
            }
            ctx.fill(path, with: .color(color))
        }
    }
}
