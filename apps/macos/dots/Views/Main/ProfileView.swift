import SwiftUI

/// You: your contribution fingerprint, what your Mac has done all time, your badges, where it lent, and your credits.
struct ProfileView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.isSnapshot) private var isSnapshot
    @State private var editingName = false
    @State private var draftName = ""
    @FocusState private var nameFocused: Bool

    var body: some View {
        let s = model.state
        let nights = s.nights ?? []
        LiveScroll {
            VStack(alignment: .leading, spacing: 24) {
                header(nights)
                HStack(alignment: .top, spacing: 16) {
                    recordStage(nights)
                        .frame(width: 440)
                    VStack(spacing: 16) {
                        AllTimeCard(nights: nights)
                            .fixedSize(horizontal: false, vertical: true)
                        BadgesCard(badges: s.badges)
                    }
                    .frame(maxWidth: .infinity)
                }
                .fixedSize(horizontal: false, vertical: true)
                if !nights.isEmpty {
                    WhereLentCard(nights: nights)
                }
                if let note = model.backend.note {
                    WeekCard(note: note)
                }
                CreditsCard()
            }
            .page()
        }
    }

    private func header(_ nights: [NightLent]) -> some View {
        let s = model.state
        let since = nights.first.map { " · lending since \($0.date.formatted(.dateTime.month(.wide).day()))" } ?? ""
        return HStack(spacing: 16) {
            Text(s.me.initial).font(.system(size: 22, weight: .bold)).foregroundStyle(D.onAccent)
                .frame(width: 52, height: 52).background(Circle().fill(D.accent))
            VStack(alignment: .leading, spacing: 4) {
                if editingName {
                    TextField("Your name", text: $draftName)
                        .textFieldStyle(.plain).font(D.ui(26, .bold)).foregroundStyle(D.t1)
                        .focused($nameFocused)
                        .onSubmit(saveName)
                        .onExitCommand { editingName = false }
                        .onChange(of: nameFocused) { _, focused in if !focused { saveName() } }
                        .frame(maxWidth: 320)
                } else {
                    HStack(spacing: 8) {
                        Text(s.me.name).font(D.ui(26, .bold)).foregroundStyle(D.t1)
                        Button {
                            draftName = s.me.name
                            editingName = true
                            nameFocused = true
                        } label: {
                            Image(systemName: "pencil").font(.system(size: 12, weight: .semibold)).foregroundStyle(D.t4)
                                .frame(width: 24, height: 24).contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .help("Change your name")
                        .accessibilityLabel("Change your name")
                    }
                }
                Text("\(s.thisMac.kind) · \(s.thisMac.memoryGB) GB\(since)").font(D.ui(13)).foregroundStyle(Color(hex: 0x8A8A8A))
            }
        }
        .frame(height: 52)
    }

    private func recordStage(_ nights: [NightLent]) -> some View {
        let s = model.state
        let live = model.lendingStatus.isActive
        return VStack(spacing: 0) {
            ZStack {
                RecordArt(nights: nights, currentPoolID: s.lendingPoolID, size: 400, live: live)
                VStack(spacing: 3) {
                    Text("\(nights.count)").font(D.ui(30, .light).monospacedDigit()).foregroundStyle(D.t2)
                    Text("NIGHTS").font(D.ui(9, .bold)).tracking(1.6).foregroundStyle(Color(hex: 0x6E6E6E))
                }
            }
            .frame(maxHeight: .infinity)
            .accessibilityElement()
            .accessibilityLabel("Your contribution fingerprint: one groove from your Mac's first night at the center to tonight at the edge, a dot for every 5 runs, each night in the tone of its pool")
            HStack {
                HStack(spacing: 7) {
                    if live { Circle().fill(D.accent).frame(width: 6, height: 6).modifier(Blink(on: !isSnapshot)) }
                    Text(live ? "Your contribution fingerprint · live" : "Your contribution fingerprint")
                }
                .font(D.ui(12.5)).foregroundStyle(Color(hex: 0x8A8A8A))
                Spacer()
                RecordShare(nights: nights, currentPoolID: s.lendingPoolID)
            }
            .frame(height: 28)
            .padding(.top, 8)
        }
        .stage()
    }

    private func saveName() {
        guard editingName else { return }
        model.backend.rename(draftName)
        editingName = false
    }
}

/// Share your contribution fingerprint as an image.
struct RecordShare: View {
    var nights: [NightLent]
    var currentPoolID: String?
    @Environment(\.isSnapshot) private var isSnapshot
    @State private var image: Image?

    var body: some View {
        Group {
            if let image, !isSnapshot {
                ShareLink(item: image, preview: SharePreview("Your contribution fingerprint", image: image)) { label }
                    .buttonStyle(.plain)
            } else {
                label
            }
        }
        .onAppear(perform: render)
        .onChange(of: nights.count) { _, _ in render() }
    }

    private var label: some View {
        HStack(spacing: 6) {
            Image(systemName: "square.and.arrow.up").font(.system(size: 11, weight: .semibold))
            Text("Share").font(D.ui(12.5, .medium))
        }
        .foregroundStyle(D.t3)
        .padding(.horizontal, 10).frame(height: 28)
        .contentShape(Rectangle())
    }

    private func render() {
        guard !isSnapshot else { return }
        let art = RecordArt(nights: nights, currentPoolID: currentPoolID, size: 1_200)
            .background(D.bg0)
            .environment(\.artClock, .now)
        let renderer = ImageRenderer(content: art)
        renderer.scale = 1
        if let cg = renderer.cgImage { image = Image(decorative: cg, scale: 1) }
    }
}

// MARK: - All time

/// The big number, three smaller ones, and the hours lent week by week.
struct AllTimeCard: View {
    @Environment(AppModel.self) private var model
    var nights: [NightLent]

    var body: some View {
        let s = model.state
        let weeks = Self.weeks(nights)
        let top = max(1, weeks.max() ?? 1)
        VStack(alignment: .leading, spacing: 0) {
            CardHeader("All time", note: nights.first.map { "Since \($0.date.formatted(.dateTime.month(.wide).day()))" })
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(Runs.short(s.served().tokens)).font(D.ui(40, .semibold).monospacedDigit()).tracking(-1).foregroundStyle(D.t1)
                    .contentTransition(.numericText())
                Text("tokens run").font(D.ui(14)).foregroundStyle(Color(hex: 0x8A8A8A))
            }
            .padding(.top, 14)
            Rule().padding(.top, 18)
            StatRow(stats: [.init(value: Int(s.hoursLent ?? 0).grouped, label: "hours lent"),
                            .init(value: s.questionsHelped.grouped, label: "runs"),
                            .init(value: Int(s.credits).grouped, label: "credits")])
                .padding(.top, 16)
            if !weeks.isEmpty {
                Text("Hours lent, week by week").font(D.ui(11.5)).foregroundStyle(D.label).padding(.top, 20)
                HStack(alignment: .bottom, spacing: 0) {
                    ForEach(Array(weeks.enumerated()), id: \.offset) { i, hours in
                        if i > 0 { Spacer(minLength: 4) }
                        DotColumn(rows: max(1, Int((hours / top * 13).rounded())), color: i == weeks.count - 1 ? D.accent : hours == top ? Color(hex: 0x9A9A9A) : Color(hex: 0x6A6A6A))
                            .help("\(Int(hours.rounded())) hours")
                    }
                }
                .frame(height: 52, alignment: .bottom)
                .padding(.top, 10)
                .accessibilityElement()
                .accessibilityLabel("Hours lent each week: " + weeks.map { "\(Int($0.rounded()))" }.joined(separator: ", "))
            }
        }
        .panel()
    }

    /// Hours lent in each of the last seven weeks, counted back from tonight, oldest first.
    static func weeks(_ nights: [NightLent]) -> [Double] {
        guard let last = nights.last?.date else { return [] }
        var out = Array(repeating: 0.0, count: 7)
        for n in nights {
            let back = Calendar.current.dateComponents([.day], from: n.date, to: last).day ?? 0
            let w = 6 - back / 7
            if w >= 0 { out[w] += n.minutes / 60 }
        }
        return Array(out.drop { $0 == 0 })
    }
}

// MARK: - Badges

/// Badges as tiles: a dot-matrix medal each, the newest ringed in orange.
struct BadgesCard: View {
    var badges: [Badge]

    var body: some View {
        let shown = Array(badges.prefix(4))
        VStack(alignment: .leading, spacing: 0) {
            CardHeader("Badges", note: "\(badges.count) earned")
            if shown.isEmpty {
                Text("No badges yet.").help("Lend your Mac, unlock a model or back a pool early, and badges show up here.")
                    .font(D.ui(12.5)).foregroundStyle(D.label).padding(.top, 12)
            } else {
                HStack(spacing: 8) {
                    ForEach(shown) { badge in
                        VStack(spacing: 11) {
                            DotGlyph(rows: DotGlyph.badge(badge.id))
                                .foregroundStyle(badge.isNew ? D.t1 : D.t2)
                                .frame(width: 46, height: 46)
                                .background(Circle().fill(Color(hex: 0x313131)))
                                .overlay { if badge.isNew { Circle().strokeBorder(D.accent, lineWidth: 1.5).padding(-1.5) } }
                            VStack(spacing: 2) {
                                Text(badge.title).font(D.ui(12, .semibold)).foregroundStyle(D.title).lineLimit(1)
                                Text(badge.isNew ? "New" : badge.date.formatted(.dateTime.month(.abbreviated).day()))
                                    .font(D.ui(11)).foregroundStyle(badge.isNew ? D.accentText : Color(hex: 0x6E6E6E))
                            }
                        }
                        .padding(.vertical, 14).padding(.horizontal, 6)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color(hex: 0x262626)))
                        .help(badge.detail)
                    }
                }
                .padding(.top, 14)
            }
        }
        .panel()
    }
}

// MARK: - Where your Mac lent

/// Each pool your Mac has lent to, newest first, with a dot for every night across the last weeks, lit where it lent.
struct WhereLentCard: View {
    @Environment(AppModel.self) private var model
    var nights: [NightLent]

    var body: some View {
        let s = model.state
        let window = Array(nights.suffix(46))
        let first = window.first?.date ?? .now
        let pools = poolOrder(window)
        VStack(alignment: .leading, spacing: 0) {
            CardHeader("Where your Mac lent", note: nights.count.counted("night"))
            VStack(spacing: 0) {
                HStack(spacing: 16) {
                    Text("Pool").frame(width: 210, alignment: .leading)
                    monthAxis(window)
                    Spacer(minLength: 0)
                    Text("Nights")
                }
                .font(D.ui(11)).foregroundStyle(Color(hex: 0x6E6E6E))
                .frame(height: 22, alignment: .bottom)
                ForEach(Array(pools.enumerated()), id: \.element) { i, pid in
                    let pool = s.pool(pid)
                    let tone = Color(hex: RecordArt.tone(for: pid, in: nights, current: s.lendingPoolID))
                    let total = nights.filter { $0.poolID == pid }.count
                    let tonight = pid == s.lendingPoolID && nights.last?.poolID == pid && nights.last?.date == Date().nightStart
                    HStack(spacing: 16) {
                        HStack(spacing: 10) {
                            Circle().fill(tone).frame(width: 9, height: 9)
                            Text(pool?.name ?? "A pool you left").font(D.ui(13.5, .semibold)).foregroundStyle(D.t1).lineLimit(1)
                            if let tag = tag(pool) { CapsTag(text: tag.0, accent: tag.1) }
                        }
                        .frame(width: 210, alignment: .leading)
                        DotTrack(slots: window.count, lit: Set(window.indices.filter { window[$0].poolID == pid }), color: tone)
                        Spacer(minLength: 0)
                        Text(total == 1 && tonight ? "Tonight" : "\(total)")
                            .font(D.ui(13).monospacedDigit()).foregroundStyle(total == 1 && tonight ? D.accentText : D.t3)
                    }
                    .frame(height: 44)
                    .overlay(alignment: .top) { if i > 0 { Rule() } }
                    .help("\(total.counted("night")) since \(nights.first { $0.poolID == pid }?.date.formatted(.dateTime.month(.abbreviated).day()) ?? "")")
                }
            }
            .padding(.top, 10)
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Where your Mac lent, night by night, since \(first.formatted(.dateTime.month(.wide).day()))")
        }
        .padding(.bottom, -8)
        .panel()
    }

    /// The pools in this window, the one your Mac lent to most recently first.
    private func poolOrder(_ window: [NightLent]) -> [String] {
        var order: [String] = []
        for n in window.reversed() where !order.contains(n.poolID) { order.append(n.poolID) }
        return order
    }

    private func tag(_ pool: Pool?) -> (String, Bool)? {
        let s = model.state
        guard let pool else { return nil }
        if pool.macs.first(where: { $0.macID == s.thisMacID })?.joinedAt ?? .distantPast > Date().addingTimeInterval(-7 * 86_400) { return ("New", true) }
        if pool.hostID == s.meID { return ("Host", false) }
        if pool.unlocks.contains(where: { $0.byMacID == s.thisMacID }) { return ("Unlocker", false) }
        return nil
    }

    /// Month names over the track, where each month starts.
    private func monthAxis(_ window: [NightLent]) -> some View {
        let cal = Calendar.current
        let marks = window.enumerated().compactMap { i, n -> (Int, String)? in
            let starts = i == 0 || cal.component(.month, from: window[i - 1].date) != cal.component(.month, from: n.date)
            return starts ? (i, n.date.formatted(.dateTime.month(.abbreviated))) : nil
        }
        return ZStack(alignment: .bottomLeading) {
            Color.clear.frame(width: CGFloat(window.count) * 11, height: 14)
            ForEach(marks, id: \.0) { i, name in
                Text(name).offset(x: CGFloat(i) * 11)
            }
        }
    }
}

// MARK: - This week

/// The week's note from the cloud: what your Mac did, and the badges that came.
struct WeekCard: View {
    var note: WeeklyNote

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            CardHeader("This week", note: note.issued.formatted(.dateTime.month(.abbreviated).day()))
            Text(note.headline).font(D.ui(14, .semibold)).foregroundStyle(D.t1).padding(.top, 12)
                .fixedSize(horizontal: false, vertical: true)
            StatRow(stats: [.init(value: String(format: "%.1f", note.hoursLent), label: "hours lent"),
                            .init(value: "+\(note.creditsEarned.grouped)", label: "credits earned", accent: true),
                            .init(value: note.questionsHelped.grouped, label: "runs"),
                            .init(value: note.creditsSpent.grouped, label: "credits spent")], size: 18)
                .padding(.top, 14)
            if !note.badges.isEmpty {
                Text("New this week: \(note.badges.joined(separator: ", ")).").font(D.ui(12.5, .medium)).foregroundStyle(D.accentText)
                    .padding(.top, 12)
            }
        }
        .panel()
    }
}

// MARK: - Credits

/// The credit ledger: what your Mac earned, night by night, and what you spent.
struct CreditsCard: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let s = model.state
        let log = Array(s.creditLog.prefix(8))
        VStack(alignment: .leading, spacing: 0) {
            CardHeader("Credits", note: "\(Int(s.credits).grouped) now")
            VStack(spacing: 0) {
                ForEach(Array(log.enumerated()), id: \.element.id) { i, e in
                    HStack {
                        Text(e.note).font(D.ui(13)).foregroundStyle(D.t2)
                        Spacer()
                        Text(e.date.formatted(.relative(presentation: .named))).font(D.ui(12)).foregroundStyle(D.label)
                            .frame(width: 120, alignment: .trailing)
                        Text(e.amount >= 0 ? "+\(e.amount)" : "\(e.amount)")
                            .font(D.ui(13, .semibold).monospacedDigit())
                            .foregroundStyle(e.amount >= 0 ? D.t1 : D.t3)
                            .frame(width: 60, alignment: .trailing)
                    }
                    .frame(minHeight: 40)
                    .contentShape(Rectangle())
                    .tip(e.detail)
                    .overlay(alignment: .top) { if i > 0 { Rule() } }
                }
            }
            .padding(.top, 6)
        }
        .padding(.bottom, -8)
        .panel()
    }
}

extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}

extension CreditEvent {
    /// For work: how long and how much the hub measured. For spending: where it went.
    var detail: String? {
        if let minutes, let compute = computePF {
            let time = minutes >= 90 ? String(format: "%.1f hours", minutes / 60) : "\(Int(minutes.rounded())) min"
            return "\(time) · \(compute < 0.01 ? "under 0.01" : String(format: "%.2f", compute)) PFLOP measured by the hub"
        }
        return nil
    }
}
