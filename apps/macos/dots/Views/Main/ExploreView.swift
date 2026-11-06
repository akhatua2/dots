import SwiftUI

struct ExploreView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.isSnapshot) private var isSnapshot
    @State private var query = ""
    @State private var filter = "All"
    private let filters = ["All", "Code", "Research", "Writing", "Nearby"]

    private var pools: [Pool] {
        model.state.publicPools
            .filter { filter == "All" || $0.tags.contains(filter) }
            .filter {
                let q = query.trimmingCharacters(in: .whitespaces).lowercased()
                guard !q.isEmpty else { return true }
                return $0.name.lowercased().contains(q) || $0.blurb.lowercased().contains(q)
                    || ($0.model?.name.lowercased().contains(q) ?? false)
            }
            .sorted { $0.totalMacs > $1.totalMacs }
    }

    var body: some View {
        LiveScroll {
            VStack(alignment: .leading, spacing: 0) {
                PageHeader(title: "Explore", subtitle: "Public pools you can lend to. Your Mac earns credits in every one.") {
                    HStack(spacing: 14) {
                        HStack(spacing: 7) {
                            Circle().fill(D.t4).frame(width: 7, height: 7)
                            Text("\(model.state.networkLendingNow.grouped) Macs lending right now")
                                .font(D.ui(12.5, .medium)).foregroundStyle(D.t3)
                                .contentTransition(.numericText())
                        }
                        Button {
                            model.openInvite = nil
                            model.sheet = .openInvite
                        } label: { Label("Invite Link", systemImage: "link") }
                            .buttonStyle(DButtonStyle(kind: .grey))
                    }
                }

                ForEach(model.backend.invites) { invite in
                    InviteCard(invite: invite).padding(.top, 18)
                }

                HStack(spacing: 8) {
                    HStack(spacing: 8) {
                        Image(systemName: "magnifyingglass").font(.system(size: 12, weight: .medium)).foregroundStyle(D.t5)
                        if isSnapshot {
                            Text("Search pools, models, places").font(D.ui(13)).foregroundStyle(D.t5)
                            Spacer()
                        } else {
                            TextField("Search pools, models, places", text: $query)
                                .textFieldStyle(.plain).font(D.ui(13)).foregroundStyle(D.t1)
                        }
                    }
                    .padding(.horizontal, 11)
                    .frame(width: 320, height: 34)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(D.bg0))
                    ForEach(filters, id: \.self) { f in
                        Chip(title: f, selected: filter == f) { filter = f }
                    }
                }
                .padding(.top, 22)
                .padding(.bottom, 20)

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 250, maximum: 340), spacing: 16, alignment: .top)],
                          alignment: .leading, spacing: 16) {
                    ForEach(pools) { pool in PoolCard(pool: pool) }
                }
                if pools.isEmpty {
                    Text("No pools match that.")
                        .font(D.ui(13)).foregroundStyle(D.t4).padding(.top, 20)
                        .help("Try another word, or start one yourself.")
                }
            }
            .padding(.horizontal, 32)
            .padding(.top, 34)
            .padding(.bottom, 32)
        }
    }
}

/// The big title at the top of each page, with an optional thing on the right.
struct PageHeader<Trailing: View>: View {
    var title: String
    var subtitle: String
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(D.ui(26, .bold)).foregroundStyle(D.t1).help(subtitle)
            }
            Spacer(minLength: 20)
            trailing.padding(.top, 8)
        }
    }
}

extension PageHeader where Trailing == EmptyView {
    init(title: String, subtitle: String) {
        self.init(title: title, subtitle: subtitle) { EmptyView() }
    }
}

struct PoolCard: View {
    @Environment(AppModel.self) private var model
    var pool: Pool
    @State private var hovering = false

    var body: some View {
        let s = model.state
        let friends = s.friends(in: pool)
        let lendingHere = s.lendingPoolID == pool.id
        let isMember = pool.isMember(s.meID)

        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                MiniRings(pool: pool, size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(pool.name).font(D.ui(15, .bold)).foregroundStyle(D.t1).lineLimit(1)
                    Text("\(pool.model?.name ?? "No model yet") · \(pool.totalMacs.grouped) Macs")
                        .font(D.ui(12)).foregroundStyle(D.t3).lineLimit(1)
                }
            }
            Text(pool.blurb)
                .font(D.ui(12.5)).foregroundStyle(D.t3).lineLimit(2)
                .frame(maxWidth: .infinity, minHeight: 36, alignment: .topLeading)
                .padding(.top, 12)
            HStack(spacing: 14) {
                HStack(spacing: 6) {
                    Circle().fill(D.t4).frame(width: 7, height: 7)
                    Text("\(pool.lendingMacs.grouped) lending").font(D.ui(12, .medium)).foregroundStyle(D.t2)
                }
                IconLine(symbol: "gauge.with.dots.needle.33percent", text: "\(pool.tokPerSec) tok/s for you", color: D.t4, size: 12)
            }
            .padding(.top, 10)
            IconLine(symbol: "smallcircle.filled.circle",
                     text: "About \(pool.creditsPerHour(lendGB: max(1, s.lending.limitGB))) credits an hour for your Mac", size: 12)
                .padding(.top, 7)
            Group {
                if !friends.isEmpty {
                    HStack(spacing: 8) {
                        HStack(spacing: -6) {
                            ForEach(friends.prefix(3)) { f in
                                Avatar(text: f.initial, size: 22).overlay(Circle().strokeBorder(D.card, lineWidth: 2))
                            }
                        }
                        Text(friends.count == 1 ? "\(friends[0].name) is here" : "\(friends.prefix(2).map(\.name).joined(separator: " and ")) are here")
                            .font(D.ui(12)).foregroundStyle(D.t3).lineLimit(1)
                    }
                } else if pool.isNew {
                    Text("New this week. Back it early.").font(D.ui(12, .medium)).foregroundStyle(D.t2)
                } else {
                    Text("No friends here yet").font(D.ui(12)).foregroundStyle(D.t5)
                }
            }
            .frame(height: 22, alignment: .leading)
            .padding(.top, 10)
            Hairline().padding(.top, 12).padding(.bottom, 12)
            HStack {
                IconLine(symbol: pool.endorsedByMe ? "checkmark.seal.fill" : "checkmark.seal",
                         text: "\(pool.endorsements.compact) endorsements", color: D.t2, size: 12, weight: .medium)
                Spacer()
                if lendingHere {
                    HStack(spacing: 6) {
                        Circle().fill(D.accent).frame(width: 7, height: 7)
                        Text("Lending")
                    }
                    .font(D.ui(12.5, .semibold)).foregroundStyle(D.t1)
                    .padding(.horizontal, 12).frame(height: 28)
                    .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(D.raised))
                } else {
                    HoverAccentButton(title: isMember ? "Lend Here" : "Add My Mac", alwaysAccent: hovering && !isMember) {
                        model.join(pool)
                    }
                }
            }
        }
        .padding(16)
        .card()
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .strokeBorder(Color.white.opacity(hovering ? 0.12 : 0), lineWidth: 1))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture { model.open(.pool(pool.id)) }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }
}

/// An invite waiting for you, with what your Mac would do for that pool.
struct InviteCard: View {
    @Environment(AppModel.self) private var model
    var invite: InviteDetails

    var body: some View {
        HStack(spacing: 14) {
            if let pool = model.state.pool(invite.poolID) {
                MiniRings(pool: pool, size: 40)
            } else {
                EmblemView(macs: invite.macs, phase: 0.7, size: 40)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text("\(invite.fromName ?? "A friend") invited you to \(invite.poolName)").font(D.ui(14, .semibold)).foregroundStyle(D.t1)
                Text(invite.unlocks.map { "Your Mac would unlock \($0) for everyone in it." }
                     ?? "\(invite.macs) Macs · runs \(invite.modelNow ?? "nothing yet")")
                    .font(D.ui(12.5)).foregroundStyle(invite.unlocks == nil ? D.t3 : D.accentText)
            }
            Spacer()
            Button("Open") { model.openInviteLink(invite.code) }.buttonStyle(DButtonStyle(kind: .primary))
        }
        .padding(16)
        .card()
    }
}
