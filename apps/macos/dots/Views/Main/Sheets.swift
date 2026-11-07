import AppKit
import SwiftUI

/// A sheet's frame: title, subtitle, content and a footer, on the dark card.
struct SheetFrame<Content: View, Footer: View>: View {
    var title: String
    var subtitle: String = ""
    /// What the sheet is for, on hover over its title
    var hint: String? = nil
    var width: CGFloat = 440
    @ViewBuilder var content: Content
    @ViewBuilder var footer: Footer

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(D.ui(17, .bold)).foregroundStyle(D.t1).tip(hint)
                if !subtitle.isEmpty { Text(subtitle).font(D.ui(13)).foregroundStyle(D.t4) }
            }
            .padding(.horizontal, 20).padding(.top, 20)
            content.padding(20)
            Hairline()
            HStack(spacing: 10) {
                Spacer()
                footer
            }
            .padding(.horizontal, 20).frame(height: 62)
        }
        .frame(width: width)
        .background(D.card)
        .preferredColorScheme(.dark)
    }
}

// MARK: - Start a pool

struct StartPoolSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var isPublic = false
    /// What it runs: a Hugging Face link, and what the cloud read from it
    @State private var link = ""
    @State private var described: ModelDescription?
    @State private var linkProblem: String?
    @State private var reading = false

    var body: some View {
        let hash = name.unicodeScalars.reduce(UInt64(5)) { $0 &* 31 &+ UInt64($1.value) }
        SheetFrame(title: "Start a Pool", hint: "Where friends' Macs team up to run a model none of them could run alone.") {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 14) {
                    EmblemView(macs: 1, phase: Double(hash % 628) / 100, size: 64)
                        .animation(.easeOut(duration: 0.2), value: name)
                    VStack(alignment: .leading, spacing: 6) {
                        CapLabel("Name")
                        Field(placeholder: "Studio 9, Home, Film club…", text: $name)
                    }
                }
                VStack(alignment: .leading, spacing: 8) {
                    CapLabel("Who can join")
                    HStack(spacing: 8) {
                        choice("Friends", "By invite link", help: "Everyone you invite can lend their Mac and ask the pool for free.",
                               selected: !isPublic) { isPublic = false }
                        choice("Public", "Anyone, in Explore", help: "Public pools run on strangers' Macs, so private prompts never go there.",
                               selected: isPublic) { isPublic = true }
                    }
                }
                Text(isPublic
                     ? "Public pools run on strangers' Macs, so private prompts never go there."
                     : "Everyone you invite can lend their Mac and ask the pool for free.")
                    .font(D.ui(12.5)).foregroundStyle(D.t4)
                VStack(alignment: .leading, spacing: 8) {
                    CapLabel("What it runs")
                    Field(placeholder: "A Hugging Face link, like huggingface.co/mlx-community/…", text: $link)
                    if reading {
                        Text("Reading the model…").font(D.ui(12.5)).foregroundStyle(D.t4)
                    } else if let linkProblem {
                        Text(linkProblem).font(D.ui(12.5)).foregroundStyle(D.t3)
                    } else if let described {
                        ModelCard(model: described)
                    }
                }
            }
            .task(id: link) { await read(link) }
        } footer: {
            Button("Cancel") { dismiss() }.buttonStyle(DButtonStyle(kind: .quiet))
            Button("Start pool") {
                model.startPool(name: name, isPublic: isPublic, model: described?.id)
                dismiss()
            }
            .buttonStyle(DButtonStyle(kind: .primary, large: true))
            .disabled(!canStart)
            .keyboardShortcut(.defaultAction)
        }
    }

    /// A name, and if a link was given, one dots can run that has been read
    private var canStart: Bool {
        guard !name.trimmingCharacters(in: .whitespaces).isEmpty else { return false }
        if link.trimmingCharacters(in: .whitespaces).isEmpty { return true }
        return !reading && linkProblem == nil && described != nil && described?.runs != false
    }

    /// Reads the link once typing pauses
    private func read(_ text: String) async {
        let link = text.trimmingCharacters(in: .whitespaces)
        described = nil
        linkProblem = nil
        guard !link.isEmpty else { return }
        try? await Task.sleep(for: .milliseconds(600))
        guard !Task.isCancelled else { return }
        reading = true
        defer { reading = false }
        do {
            let model = try await model.backend.describeModel(link)
            guard !Task.isCancelled else { return }
            described = model
            if model.runs == false { linkProblem = model.why.map { "dots can't run \(model.name): \($0)." } }
        } catch {
            guard !Task.isCancelled else { return }
            linkProblem = "Couldn't read that link. Check it's a Hugging Face model you can open."
        }
    }

    private func choice(_ title: String, _ detail: String, help: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(D.ui(13.5, .semibold)).foregroundStyle(D.t1)
                Text(detail).font(D.ui(12)).foregroundStyle(D.t4)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(selected ? D.raised : D.bg0))
            .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous).strokeBorder(selected ? D.t3 : D.line, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(help)
    }
}

/// What a link runs and what it needs, under the link in Start a Pool
private struct ModelCard: View {
    var model: ModelDescription

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(model.name).font(D.ui(13.5, .semibold)).foregroundStyle(D.t1)
            Text(what).font(D.ui(12.5)).foregroundStyle(D.t3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(D.bg0))
        .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous).strokeBorder(D.line, lineWidth: 1))
    }

    private var size: String { String(format: "%.1f GB", model.gb) }

    private var what: String {
        switch model.kind {
        case "pictures": "Draws and changes pictures. Each one is drawn on a single Mac with room for its \(size)."
        case "text": "Answers questions. Its \(size) runs on one Mac with room for it, or is split across the pool's Macs."
        default: "\(size) of weights."
        }
    }
}

// MARK: - Invite

struct InviteSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    var poolID: String
    @State private var copied: String?

    var body: some View {
        let s = model.state
        let pool = s.pool(poolID)
        SheetFrame(title: "Invite friends to \(pool?.name ?? "the pool")",
                   hint: "Every Mac that joins grows what the pool can run.", width: 480) {
            VStack(alignment: .leading, spacing: 16) {
                if let pool, let invite = pool.invite {
                    HStack(spacing: 10) {
                        let link = invite.link(cloud: model.backend.cloudAddress)
                        Text(link).font(D.mono(14, .medium)).foregroundStyle(D.t1).textSelection(.enabled)
                            .lineLimit(1).truncationMode(.middle)
                        Spacer()
                        CopyButton(text: link, copied: $copied, tag: "invite")
                    }
                    .padding(.leading, 14).padding(.trailing, 7).frame(height: 46)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(D.bg0))
                    HStack {
                        Text("Expires \(invite.expires.formatted(date: .abbreviated, time: .omitted)).")
                            .font(D.ui(12.5)).foregroundStyle(D.t4)
                        Button("Revoke link") { model.backend.revokeInvite(poolID: pool.id) }
                            .buttonStyle(.plain).font(D.ui(12.5, .semibold)).foregroundStyle(D.t2)
                    }
                    InvitePreview(pool: pool, inviter: s.me.name)
                } else if let pool {
                    Button { _ = model.backend.createInvite(poolID: pool.id) } label: { Label("Make a Link", systemImage: "link") }
                        .buttonStyle(DButtonStyle(kind: .primary, large: true))
                        .help("It shows your friend who's in \(pool.name), what it runs, and what their Mac would unlock.")
                }
            }
        } footer: {
            Button("Done") { dismiss() }.buttonStyle(DButtonStyle(kind: .grey, large: true)).keyboardShortcut(.defaultAction)
        }
    }
}

/// What your friend sees when they open the link.
struct InvitePreview: View {
    var pool: Pool
    var inviter: String

    var body: some View {
        // What one more Mac like the ones already lending here would add
        let typicalAdd = max(8, Int((Double(pool.pooledGB) / Double(max(pool.lendingMacs, 1)) / 4).rounded()) * 4)
        let after = pool.pooledGB + typicalAdd
        VStack(alignment: .leading, spacing: 10) {
            CapLabel("What they'll see")
            HStack(spacing: 12) {
                EmblemView(macs: pool.totalMacs, phase: pool.phase, size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(inviter) invited you to \(pool.name)").font(D.ui(14, .semibold)).foregroundStyle(D.t1)
                    Text("\(pool.lendingMacs) lending · runs \(pool.modelLabel ?? "nothing yet")").font(D.ui(12.5)).foregroundStyle(D.t3)
                }
            }
            if let next = pool.nextModel {
                Text(after >= next.minGB
                     ? "One more Mac like the ones here, about \(typicalAdd) GB, would unlock \(next.name) for everyone."
                     : "One more Mac like the ones here, about \(typicalAdd) GB, takes it to \(after.grouped) GB. \(next.name) needs \(next.minGB.grouped).")
                    .font(D.ui(12.5)).foregroundStyle(D.t2)
                GrowthBar(current: pool.pooledGB, adding: typicalAdd, target: next.minGB).frame(height: 8)
            }
        }
        .padding(14)
        .card(fill: D.deep)
    }
}

// MARK: - Create an API key

struct CreateKeySheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var yourPools = true
    @State private var publicPools = true
    @State private var frontier = false
    @State private var limit = "500"
    @State private var privateOnly = false
    @State private var capUSD = ""
    @State private var copied: String?

    var body: some View {
        if let revealed = model.revealedKey, let key = model.state.apiKeys.first(where: { $0.id == revealed.id }) {
            SheetFrame(title: "Your new key", subtitle: "Copy it now. You won't see it again.") {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text(revealed.secret).font(D.mono(13.5, .medium)).foregroundStyle(D.t1).textSelection(.enabled)
                        Spacer()
                        Button {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(revealed.secret, forType: .string)
                            copied = "key"
                        } label: { Text(copied == "key" ? "Copied" : "Copy") }
                            .buttonStyle(DButtonStyle(kind: copied == "key" ? .grey : .primary))
                    }
                    .padding(.leading, 14).padding(.trailing, 7).frame(height: 46)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(D.bg0))
                    (Text(key.name).bold() + Text(": \(key.rulesSummary.lowercased())."))
                        .font(D.ui(12.5)).foregroundStyle(D.t3)
                        .help("Revoke it on the API page anytime.")
                }
            } footer: {
                Button("Done") {
                    model.revealedKey = nil
                    dismiss()
                }
                .buttonStyle(DButtonStyle(kind: .grey, large: true))
                .keyboardShortcut(.defaultAction)
            }
        } else {
            SheetFrame(title: "Create an API Key", hint: "For any app that speaks the OpenAI API or Anthropic's.") {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 6) {
                        CapLabel("Name")
                        Field(placeholder: "My scripts", text: $name)
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        CapLabel("What it can reach")
                        reachRow("Keep it private", "Only this Mac and your own pools, whatever it asks", $privateOnly)
                        if !privateOnly {
                            reachRow("Your pools", "Private, and free for you", $yourPools)
                            reachRow("Public pools", "Spends credits", $publicPools)
                            reachRow("Frontier models", "Credits, or your own key, and leaves your pools", $frontier)
                        }
                    }
                    if !privateOnly && frontier {
                        HStack {
                            Text("Most on your own key").font(D.ui(13.5)).foregroundStyle(D.t2)
                            Spacer()
                            Text("$").font(D.ui(13.5)).foregroundStyle(D.t3)
                            TextField("No cap", text: $capUSD)
                                .textFieldStyle(.plain).multilineTextAlignment(.trailing)
                                .font(D.ui(13.5)).foregroundStyle(D.t1)
                                .frame(width: 60)
                            Text("a month").font(D.ui(13.5)).foregroundStyle(D.t3)
                        }
                        .padding(.horizontal, 12).frame(height: 36)
                        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(D.bg0))
                    }
                    if !privateOnly && (frontier || publicPools) {
                        HStack {
                            Text("Monthly limit").font(D.ui(13.5)).foregroundStyle(D.t2)
                            Spacer()
                            TextField("", text: $limit)
                                .textFieldStyle(.plain).multilineTextAlignment(.trailing)
                                .font(D.ui(13.5)).foregroundStyle(D.t1)
                                .frame(width: 60)
                            Text("credits").font(D.ui(13.5)).foregroundStyle(D.t3)
                        }
                        .padding(.horizontal, 12).frame(height: 36)
                        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(D.bg0))
                    }
                }
            } footer: {
                Button("Cancel") { dismiss() }.buttonStyle(DButtonStyle(kind: .quiet))
                Button("Create key") {
                    var reach: Reach = []
                    if yourPools { reach.insert(.yourPools) }
                    if publicPools { reach.insert(.publicPools) }
                    if frontier { reach.insert(.frontier) }
                    let n = name.trimmingCharacters(in: .whitespaces)
                    if privateOnly { reach = .yourPools }
                    model.createKey(name: n.isEmpty ? "My scripts" : n, reach: reach,
                                    monthlyLimit: !privateOnly && (frontier || publicPools) ? Int(limit) : nil,
                                    privateOnly: privateOnly, monthlyCapUSD: !privateOnly && frontier ? Double(capUSD) : nil)
                }
                .buttonStyle(DButtonStyle(kind: .primary, large: true))
                .disabled(!(privateOnly || yourPools || publicPools || frontier))
                .keyboardShortcut(.defaultAction)
            }
        }
    }

    private func reachRow(_ title: String, _ detail: String, _ isOn: Binding<Bool>) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(D.ui(13.5, .semibold)).foregroundStyle(D.t1).help(detail)
            }
            Spacer()
            DSwitch(isOn: isOn, label: title)
        }
    }
}

// MARK: - Open an invite link

/// An invite link someone sent you: the pool, who's in it, how much your Mac would lend, and honestly what that does.
struct OpenInviteSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var link = ""
    @State private var choice: Int?

    var body: some View {
        let s = model.state
        let open = model.openInvite
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text(open?.preview.map { "Join \($0.poolName)" } ?? "Open an invite link").font(D.ui(19, .bold)).foregroundStyle(D.t1)
                Text(open?.preview.map { p in p.fromName.map { "An invite from \($0)" } ?? "You're invited" }
                     ?? "Paste a link a friend sent you, like dots://invite/LATE7Q.")
                    .font(D.ui(13)).foregroundStyle(Color(hex: 0x8A8A8A))
            }
            .padding(.horizontal, 28).padding(.top, 26)

            VStack(alignment: .leading, spacing: 14) {
                if let p = open?.preview {
                    let pool = s.pool(p.poolID)
                    poolCard(p, pool)
                    if !p.isMember, let pool, !options.isEmpty { lendChoice(pool) }
                } else if let error = open?.error {
                    Text(error).font(D.ui(13)).foregroundStyle(D.t3)
                } else if open != nil, !(open?.code.isEmpty ?? true) {
                    Text("Opening the invite…").font(D.ui(13)).foregroundStyle(D.t3)
                }
                if open?.preview == nil {
                    Field(placeholder: "dots://invite/…", text: $link, mono: true)
                        .onSubmit { model.openInviteLink(link) }
                }
            }
            .padding(.horizontal, 28).padding(.top, 20).padding(.bottom, 24)

            Rectangle().fill(Color.white.opacity(0.08)).frame(height: 0.5)
            HStack(spacing: 10) {
                Spacer()
                Button("Not Now") { model.openInvite = nil; dismiss() }.buttonStyle(DButtonStyle(kind: .quiet, large: true))
                if let p = open?.preview {
                    if p.isMember {
                        Button("Open \(p.poolName)") { model.openInvite = nil; model.route = .pool(p.poolID); dismiss() }
                            .buttonStyle(DButtonStyle(kind: .primary, large: true)).keyboardShortcut(.defaultAction)
                    } else {
                        let gb = chosen
                        Button(gb > 0 ? "Join and Lend \(gb) GB" : "Join \(p.poolName)") { model.acceptInvite(p, lendGB: gb) }
                            .buttonStyle(DButtonStyle(kind: .primary, large: true)).keyboardShortcut(.defaultAction)
                    }
                } else {
                    Button("Open") { model.openInviteLink(link) }
                        .buttonStyle(DButtonStyle(kind: .primary, large: true)).keyboardShortcut(.defaultAction)
                        .disabled(link.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .padding(.horizontal, 28).frame(height: 66)
        }
        .frame(width: 480)
        .background(D.bg2)
        .preferredColorScheme(.dark)
    }

    /// What the pool is: its art, its level and model, who's in it and who's live.
    private func poolCard(_ p: InviteDetails, _ pool: Pool?) -> some View {
        let s = model.state
        return HStack(alignment: .top, spacing: 14) {
            Group {
                if let pool {
                    RingsArt(history: PoolHistory.make(pool, thisMacID: s.thisMacID), size: 88, cells: 88, guides: true, showNow: false)
                } else {
                    EmblemView(macs: p.macs, phase: 0.7, size: 88)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            VStack(alignment: .leading, spacing: 6) {
                Text(p.poolName).font(D.ui(15, .semibold)).foregroundStyle(D.t1)
                Text([pool.map { "Level \($0.level)" }, p.modelNow, "\(p.pooledGB.grouped) GB live"].compactMap { $0 }.joined(separator: " · "))
                    .font(D.ui(12.5)).foregroundStyle(D.t3)
                if let pool {
                    HStack(spacing: 6) {
                        ForEach(pool.memberIDs.prefix(6), id: \.self) { pid in
                            let mac = pool.macs.first { s.device($0.macID)?.ownerID == pid }
                            Text(s.person(pid)?.initial ?? "?").font(.system(size: 10, weight: .bold)).foregroundStyle(D.t1)
                                .frame(width: 22, height: 22).background(Circle().fill(Color(hex: 0x262626)))
                                .overlay(alignment: .bottomTrailing) {
                                    Circle().fill(mac?.state == .lending ? D.accent : Color(hex: 0x6E6E6E)).frame(width: 8, height: 8)
                                        .overlay(Circle().strokeBorder(D.card, lineWidth: 1.5)).offset(x: 2, y: 2)
                                }
                        }
                        Text("\(pool.lendingMacs) live").font(D.ui(12)).foregroundStyle(D.t2).padding(.leading, 4)
                        Text("\(pool.memberIDs.count) members").font(D.ui(12)).foregroundStyle(D.t4)
                    }
                }
                Text(p.blurb).font(D.ui(12.5)).foregroundStyle(D.t3).lineLimit(2)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(D.card))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(D.panelEdge, lineWidth: 0.5))
    }

    /// Half of what this Mac has left to lend, or all of it
    private var options: [Int] {
        let left = model.openInvite?.preview.map { model.state.mostGB(for: $0.poolID) } ?? model.state.thisMac.maxLendGB
        return Array(Set([left / 2, left])).filter { $0 > 0 }.sorted()
    }

    private var chosen: Int { choice ?? options.last ?? 0 }

    /// How much to lend, and what it does: the next level reached, or how far short.
    private func lendChoice(_ pool: Pool) -> some View {
        let s = model.state
        let gb = chosen
        let after = pool.pooledGB + gb
        let next = pool.nextModel
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Your Mac lends").font(D.ui(13, .semibold)).foregroundStyle(D.title)
                Spacer()
                Text("\(s.thisMac.kind) · \(s.thisMac.memoryGB) GB").font(D.ui(12)).foregroundStyle(D.label)
            }
            HStack(spacing: 10) {
                ForEach(options, id: \.self) { option in
                    let selected = option == gb
                    Button { choice = option } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(option) GB").font(D.ui(15, .semibold).monospacedDigit()).foregroundStyle(D.t1)
                            Text(option == s.thisMac.maxLendGB ? "Everything it can spare" : "What you lend now")
                                .font(D.ui(12)).foregroundStyle(selected ? D.t2 : D.t3)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 13)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(selected ? D.accent.opacity(0.14) : D.card))
                        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(selected ? D.accent.opacity(0.75) : Color.white.opacity(0.08), lineWidth: 1))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(PressStyle())
                }
            }
            VStack(alignment: .leading, spacing: 10) {
                if let next {
                    let level = (Ladder.tiers.firstIndex { $0.id == next.id } ?? 0) + 1
                    let legend = pool.macs.filter { $0.state == .lending }.compactMap { m in
                        s.device(m.macID).flatMap { s.person($0.ownerID) }.map { "\($0.name) \(m.lendGB)" }
                    }.joined(separator: " · ") + " · you \(gb) GB. Level \(level) at \(next.minGB.grouped) GB."
                    GrowthBar(current: pool.pooledGB, adding: gb, target: next.minGB).frame(height: 10).help(legend)
                    if after >= next.minGB {
                        Text("Your Mac unlocks \(next.name) for everyone in \(pool.name).").font(D.ui(14, .semibold)).foregroundStyle(D.accentText)
                            .fixedSize(horizontal: false, vertical: true)
                            .help("That's Level \(level), at \(next.minGB.grouped) GB.")
                    } else {
                        Text("\(next.minGB - after) GB short of Level \(level).").font(D.ui(14, .semibold)).foregroundStyle(D.t1)
                            .help(pool.pooledGB + s.thisMac.maxLendGB >= next.minGB
                                  ? "Lend \(s.thisMac.maxLendGB) GB to unlock \(next.name), or wait for another Mac."
                                  : "It grows with every Mac that joins.")
                    }
                } else {
                    Text("Your Mac makes \(pool.name) faster for everyone in it.").font(D.ui(13)).foregroundStyle(D.t2)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(D.card))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(D.panelEdge, lineWidth: 0.5))
            .animation(.easeOut(duration: 0.2), value: gb)
        }
    }
}
