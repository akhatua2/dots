import SwiftUI

struct ProfileView: View {
    @Environment(AppModel.self) private var model
    @State private var editingName = false
    @State private var draftName = ""
    @FocusState private var nameFocused: Bool

    var body: some View {
        let s = model.state
        LiveScroll {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 16) {
                    Avatar(text: s.me.initial, size: 64, isYou: true)
                    VStack(alignment: .leading, spacing: 6) {
                        if editingName {
                            TextField("Your name", text: $draftName)
                                .textFieldStyle(.plain).font(D.ui(25, .bold)).foregroundStyle(D.t1)
                                .focused($nameFocused)
                                .onSubmit(saveName)
                                .onExitCommand { editingName = false }
                                .onChange(of: nameFocused) { _, focused in if !focused { saveName() } }
                                .frame(maxWidth: 320)
                        } else {
                            HStack(spacing: 8) {
                                Text(s.me.name).font(D.ui(25, .bold)).foregroundStyle(D.t1)
                                Button {
                                    draftName = s.me.name
                                    editingName = true
                                    nameFocused = true
                                } label: {
                                    Image(systemName: "pencil").font(.system(size: 13, weight: .semibold)).foregroundStyle(D.t4)
                                        .frame(width: 24, height: 24).contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                .help("Change your name")
                                .accessibilityLabel("Change your name")
                            }
                        }
                        Text("\(s.thisMac.kind) · \(s.thisMac.memoryGB) GB · \(s.myPools.count) pools")
                            .font(D.ui(13.5)).foregroundStyle(D.t3)
                    }
                }

                HStack(spacing: 12) {
                    StatCard(label: "Credits", value: Int(s.credits).grouped)
                    StatCard(label: "Earned, all time", value: Int(s.earnedAllTime).grouped)
                    StatCard(label: "Questions helped", value: s.questionsHelped.grouped)
                    StatCard(label: "Pools endorsed", value: s.poolsEndorsed.grouped)
                }
                .padding(.top, 26)

                CapLabel("Badges").padding(.top, 30).padding(.bottom, 12)
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
                    ForEach(s.badges) { badge in BadgeCard(badge: badge) }
                }

                CapLabel("Pools").padding(.top, 30).padding(.bottom, 12)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 12)], spacing: 12) {
                    ForEach(s.myPools) { pool in
                        Button { model.open(.pool(pool.id)) } label: {
                            HStack(spacing: 12) {
                                EmblemView(macs: pool.totalMacs, phase: pool.phase, size: 32)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(pool.name).font(D.ui(13.5, .semibold)).foregroundStyle(D.t1)
                                    Text(s.lendingPoolID == pool.id ? "Lending \(s.lending.limitGB) GB · \(pool.model?.name ?? "")"
                                         : "Member · \(pool.model?.name ?? "No model yet")")
                                        .font(D.ui(12)).foregroundStyle(D.t4)
                                }
                                Spacer()
                            }
                            .padding(14)
                            .card()
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }

                CapLabel("Credits").padding(.top, 30).padding(.bottom, 12)
                VStack(spacing: 0) {
                    ForEach(Array(s.creditLog.prefix(8).enumerated()), id: \.element.id) { i, e in
                        HStack {
                            Text(e.note).font(D.ui(13)).foregroundStyle(D.t2)
                            Spacer()
                            Text(e.date.formatted(.relative(presentation: .named))).font(D.ui(12)).foregroundStyle(D.t4)
                                .frame(width: 120, alignment: .trailing)
                            Text(e.amount >= 0 ? "+\(e.amount)" : "\(e.amount)")
                                .font(D.ui(13, .semibold).monospacedDigit())
                                .foregroundStyle(e.amount >= 0 ? D.t1 : D.t3)
                                .frame(width: 60, alignment: .trailing)
                        }
                        .padding(.horizontal, 16)
                        .frame(height: 40)
                        if i < min(8, s.creditLog.count) - 1 { Hairline().padding(.horizontal, 16) }
                    }
                }
                .card()
            }
            .padding(.horizontal, 32)
            .padding(.top, 34)
            .padding(.bottom, 40)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func saveName() {
        guard editingName else { return }
        model.backend.rename(draftName)
        editingName = false
    }
}

struct StatCard: View {
    var label: String
    var value: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(D.ui(12)).foregroundStyle(D.t4)
            Text(value).font(D.ui(22, .bold).monospacedDigit()).foregroundStyle(D.t1).contentTransition(.numericText())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .card()
    }
}

struct BadgeCard: View {
    var badge: Badge
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: badge.symbol)
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(D.t1)
                .frame(width: 48, height: 48)
                .background(Circle().fill(D.raised))
            VStack(alignment: .leading, spacing: 4) {
                Text(badge.title).font(D.ui(14.5, .bold)).foregroundStyle(D.t1)
                Text(badge.detail).font(D.ui(12.5)).foregroundStyle(D.t3).lineLimit(2)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 6) {
                Text(Calendar.current.isDateInToday(badge.date) || Calendar.current.isDateInYesterday(badge.date) ? badge.date.relativeDayPhrase.capitalizedFirst : badge.date.formatted(.dateTime.month(.abbreviated).day())).font(D.ui(12)).foregroundStyle(D.t4)
                if badge.isNew { Tag(text: "NEW", accent: true) }
            }
        }
        .padding(16)
        .card(stroke: badge.isNew ? D.accent : D.edge)
    }
}

extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
