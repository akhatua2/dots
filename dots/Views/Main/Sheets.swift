import AppKit
import SwiftUI

/// A sheet's frame: title, subtitle, content and a footer, on the dark card.
struct SheetFrame<Content: View, Footer: View>: View {
    var title: String
    var subtitle: String
    var width: CGFloat = 440
    @ViewBuilder var content: Content
    @ViewBuilder var footer: Footer

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(D.ui(17, .bold)).foregroundStyle(D.t1)
                Text(subtitle).font(D.ui(13)).foregroundStyle(D.t4)
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

    var body: some View {
        let hash = name.unicodeScalars.reduce(UInt64(5)) { $0 &* 31 &+ UInt64($1.value) }
        SheetFrame(title: "Start a pool", subtitle: "Where friends' Macs team up to run a model none of them could run alone.") {
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
                        choice("Friends", "By invite link", selected: !isPublic) { isPublic = false }
                        choice("Public", "Anyone, in Explore", selected: isPublic) { isPublic = true }
                    }
                }
                Text(isPublic
                     ? "Public pools run on strangers' Macs, so private questions never go there."
                     : "Everyone you invite can lend their Mac and ask the pool for free.")
                    .font(D.ui(12.5)).foregroundStyle(D.t4)
            }
        } footer: {
            Button("Cancel") { dismiss() }.buttonStyle(DButtonStyle(kind: .quiet))
            Button("Start pool") {
                model.startPool(name: name, isPublic: isPublic)
                dismiss()
            }
            .buttonStyle(DButtonStyle(kind: .primary, large: true))
            .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            .keyboardShortcut(.defaultAction)
        }
    }

    private func choice(_ title: String, _ detail: String, selected: Bool, action: @escaping () -> Void) -> some View {
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
                   subtitle: "Every Mac that joins grows what the pool can run.", width: 480) {
            VStack(alignment: .leading, spacing: 16) {
                if let pool, let invite = pool.invite {
                    HStack(spacing: 10) {
                        Text(invite.url).font(D.mono(14, .medium)).foregroundStyle(D.t1).textSelection(.enabled)
                        Spacer()
                        CopyButton(text: invite.url, copied: $copied, tag: "invite")
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
                    Text("Make a link to send. It shows your friend who's in \(pool.name), what it runs, and what their Mac would unlock.")
                        .font(D.ui(13)).foregroundStyle(D.t3)
                    Button { _ = model.backend.createInvite(poolID: pool.id) } label: { Label("Make a link", systemImage: "link") }
                        .buttonStyle(DButtonStyle(kind: .primary, large: true))
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
                    Text("\(pool.lendingMacs) lending · runs \(pool.model?.name ?? "nothing yet")").font(D.ui(12.5)).foregroundStyle(D.t3)
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
                    (Text(key.name).bold() + Text(" can reach \(key.reach.summary.lowercased())"
                        + (key.monthlyLimit.map { ", up to \($0) credits a month" } ?? "") + ". Revoke it on the API page anytime."))
                        .font(D.ui(12.5)).foregroundStyle(D.t3)
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
            SheetFrame(title: "Create an API key", subtitle: "For any app that speaks the OpenAI API.") {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 6) {
                        CapLabel("Name")
                        Field(placeholder: "My scripts", text: $name)
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        CapLabel("What it can reach")
                        reachRow("Your pools", "Private, and free for you", $yourPools)
                        reachRow("Public pools", "Spends credits", $publicPools)
                        reachRow("Frontier models", "Spends credits, and leaves your pools", $frontier)
                    }
                    if frontier || publicPools {
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
                    model.createKey(name: n.isEmpty ? "My scripts" : n, reach: reach,
                                    monthlyLimit: (frontier || publicPools) ? Int(limit) : nil)
                }
                .buttonStyle(DButtonStyle(kind: .primary, large: true))
                .disabled(!(yourPools || publicPools || frontier))
                .keyboardShortcut(.defaultAction)
            }
        }
    }

    private func reachRow(_ title: String, _ detail: String, _ isOn: Binding<Bool>) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(D.ui(13.5, .semibold)).foregroundStyle(D.t1)
                Text(detail).font(D.ui(12)).foregroundStyle(D.t4)
            }
            Spacer()
            DSwitch(isOn: isOn, label: title)
        }
    }
}
