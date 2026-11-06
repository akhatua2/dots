import AppKit
import SwiftUI

struct APIView: View {
    @Environment(AppModel.self) private var model
    @State private var tab = "curl"
    @State private var copied: String?

    var body: some View {
        let s = model.state
        LiveScroll {
            VStack(alignment: .leading, spacing: 0) {
                PageHeader(title: "API", subtitle: "One endpoint for every model you can reach, in OpenAI's format or Anthropic's.") {
                    Button { model.sheet = .createKey } label: {
                        Label("Create key", systemImage: "plus")
                    }
                    .buttonStyle(DButtonStyle(kind: .primary))
                }

                // Base URL: a real server on this Mac
                HStack(spacing: 14) {
                    VStack(alignment: .leading, spacing: 5) {
                        CapLabel("Base URL")
                        Text(model.gatewayBaseURL).font(D.mono(14, .medium)).foregroundStyle(D.t1).textSelection(.enabled)
                    }
                    Spacer()
                    HStack(spacing: 6) {
                        LiveDot(live: model.gatewayState.hasPrefix("Running"), size: 7)
                        Text(model.gatewayState.hasPrefix("Running") ? "Running on this Mac" : model.gatewayState)
                            .font(D.ui(12)).foregroundStyle(D.t3)
                    }
                    CopyButton(text: model.gatewayBaseURL, copied: $copied, tag: "base")
                }
                .padding(16)
                .card()
                .padding(.top, 24)

                HStack {
                    CapLabel("Keys").help("Use model \"auto\" and dots picks where each request runs.")
                    Spacer()
                }
                .padding(.top, 26).padding(.bottom, 10)

                VStack(spacing: 0) {
                    ForEach(Array(s.apiKeys.enumerated()), id: \.element.id) { i, key in
                        KeyRow(key: key, isNew: model.revealedKey?.id == key.id || key.createdAt > Date().addingTimeInterval(-600))
                        if i < s.apiKeys.count - 1 { Hairline().padding(.horizontal, 16) }
                    }
                    if s.apiKeys.isEmpty {
                        Text("No keys yet.")
                            .font(D.ui(13)).foregroundStyle(D.t4).padding(16)
                            .help("Make one for each app, so you can see and revoke them one by one.")
                    }
                }
                .card()

                CapLabel("Saved this month").padding(.top, 28).padding(.bottom, 12)
                SavingsCard(requests: model.monthRequests, baseline: FrontierModel.named(model.settings.savingsBaseline)?.name ?? model.settings.savingsBaseline)

                CapLabel("Where your requests ran this month").padding(.top, 28).padding(.bottom, 12)
                RequestSplit(requests: s.requests)

                if !model.monthRequests.isEmpty {
                    CapLabel("Recent requests").padding(.top, 28).padding(.bottom, 12)
                    RecentRequests(requests: Array(s.requests.suffix(6).reversed()))
                }

                CodeSample(tab: $tab, baseURL: model.gatewayBaseURL, copied: $copied)
                    .padding(.top, 24)
            }
            .page()
        }
    }
}

struct CopyButton: View {
    var text: String
    @Binding var copied: String?
    var tag: String

    var body: some View {
        Button {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
            copied = tag
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) { if copied == tag { copied = nil } }
        } label: {
            Label(copied == tag ? "Copied" : "Copy", systemImage: copied == tag ? "checkmark" : "doc.on.doc")
                .labelStyle(.titleOnly)
        }
        .buttonStyle(DButtonStyle(kind: .grey))
    }
}

struct KeyRow: View {
    @Environment(AppModel.self) private var model
    var key: APIKey
    var isNew: Bool

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(key.name).font(D.ui(13.5, .semibold)).foregroundStyle(D.t1)
                    if isNew { Tag(text: "NEW", accent: true).scaleEffect(0.85) }
                }
                Text(key.rulesSummary)
                    .font(D.ui(12)).foregroundStyle(D.t4).lineLimit(2).fixedSize(horizontal: false, vertical: true)
            }
            .frame(width: 270, alignment: .leading)
            Text("dots_sk_••••\(key.last4)").font(D.mono(12.5)).foregroundStyle(D.t3).frame(width: 170, alignment: .leading)
            Text(usage).font(D.ui(12.5)).foregroundStyle(D.t3)
            Spacer()
            Button("Revoke") { model.backend.revokeKey(id: key.id) }
                .buttonStyle(DButtonStyle(kind: .quiet))
        }
        .padding(.horizontal, 16)
        .frame(height: 56)
    }

    private var usage: String {
        if key.requestsThisMonth == 0 { return "Not used yet" }
        let usd = key.usdThisMonth ?? 0
        if key.creditsThisMonth == 0 && usd == 0 { return "\(key.requestsThisMonth) requests, free" }
        var parts: [String] = []
        if key.creditsThisMonth > 0 { parts.append("\(key.creditsThisMonth.grouped) credits") }
        if usd > 0 { parts.append("\(usd.dollars) on your key") }
        return parts.joined(separator: " · ") + " this month"
    }
}

/// This month's requests, split by where they ran.
struct RequestSplit: View {
    var requests: [RequestRecord]

    var body: some View {
        let start = Calendar.current.dateInterval(of: .month, for: .now)?.start ?? .distantPast
        let month = requests.filter { $0.date >= start }
        let total = max(1, month.count)
        let parts: [(Destination, String, Color)] = [
            (.thisMac, "This Mac", D.t1), (.yourPool, "Your pools", D.t2), (.publicPool, "Public pools", D.swirl), (.frontier, "Frontier models", D.t5),
        ]
        VStack(alignment: .leading, spacing: 12) {
            GeometryReader { geo in
                HStack(spacing: 2) {
                    ForEach(parts, id: \.0) { dest, _, color in
                        let n = month.filter { $0.destination == dest }.count
                        if n > 0 {
                            Rectangle().fill(color).frame(width: max(2, (geo.size.width - 4) * CGFloat(n) / CGFloat(total)))
                        }
                    }
                }
                .clipShape(Capsule())
            }
            .frame(height: 10)
            HStack(spacing: 28) {
                ForEach(parts, id: \.0) { dest, label, color in
                    let items = month.filter { $0.destination == dest }
                    let credits = items.reduce(0) { $0 + $1.credits }
                    HStack(spacing: 7) {
                        RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 9, height: 9)
                        Text(label).font(D.ui(12.5, .semibold)).foregroundStyle(D.t1)
                        Text("\(Int((Double(items.count) / Double(total) * 100).rounded()))% · \(credits == 0 ? "free" : "\(credits.grouped) credits")")
                            .font(D.ui(12.5)).foregroundStyle(D.t3)
                    }
                }
            }
        }
    }
}

struct CodeSample: View {
    @Binding var tab: String
    var baseURL: String
    @Binding var copied: String?
    /// Anthropic's SDKs add /v1 themselves
    private var anthropicBase: String { baseURL.hasSuffix("/v1") ? String(baseURL.dropLast(3)) : baseURL }

    private var code: String {
        switch tab {
        case "Python":
            return """
            import os
            from openai import OpenAI

            client = OpenAI(base_url="\(baseURL)", api_key=os.environ["DOTS_API_KEY"])
            reply = client.chat.completions.create(
                model="auto",
                messages=[{"role": "user", "content": "Three risks in this launch plan?"}],
            )
            print(reply.choices[0].message.content)
            """
        case "Anthropic":
            return """
            # Anthropic's SDK, or Claude Code with:
            # ANTHROPIC_BASE_URL=\(anthropicBase) ANTHROPIC_API_KEY=$DOTS_API_KEY claude
            import os
            from anthropic import Anthropic

            client = Anthropic(base_url="\(anthropicBase)", api_key=os.environ["DOTS_API_KEY"])
            reply = client.messages.create(
                model="auto",
                max_tokens=1024,
                messages=[{"role": "user", "content": "Three risks in this launch plan?"}],
            )
            print(reply.content[0].text)
            """
        case "JavaScript":
            return """
            import OpenAI from "openai";

            const client = new OpenAI({ baseURL: "\(baseURL)", apiKey: process.env.DOTS_API_KEY });
            const reply = await client.chat.completions.create({
              model: "auto",
              messages: [{ role: "user", content: "Three risks in this launch plan?" }],
            });
            console.log(reply.choices[0].message.content);
            """
        default:
            return """
            curl \(baseURL)/chat/completions \\
              -H "Authorization: Bearer $DOTS_API_KEY" \\
              -H "Content-Type: application/json" \\
              -d '{"model": "auto", "messages": [{"role": "user", "content": "Hi"}]}'
            """
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 18) {
                ForEach(["curl", "Python", "JavaScript", "Anthropic"], id: \.self) { t in
                    Button { tab = t } label: {
                        VStack(spacing: 6) {
                            Text(t).font(D.ui(12.5, .bold)).foregroundStyle(tab == t ? D.t1 : D.t4)
                            Rectangle().fill(tab == t ? D.t1 : .clear).frame(height: 2)
                        }
                        .fixedSize()
                    }
                    .buttonStyle(.plain)
                }
                Spacer()
                CopyButton(text: code, copied: $copied, tag: "code")
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            Hairline()
            Text(code)
                .font(D.mono(12.5))
                .foregroundStyle(D.t2)
                .lineSpacing(4)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
        }
        .card()
    }
}

/// What sending requests through dots saved this month, against sending them all to one frontier model.
struct SavingsCard: View {
    var requests: [RequestRecord]
    var baseline: String

    var body: some View {
        let counted = requests.filter { $0.baselineUSD != nil }
        let saved = counted.reduce(0) { $0 + $1.savedUSD }
        let free = counted.filter { $0.destination == .thisMac || $0.destination == .yourPool }.count
        let credits = counted.reduce(0) { $0 + $1.credits }
        let usd = counted.reduce(0) { $0 + ($1.usd ?? 0) }
        let measured = counted.filter { $0.measured == true }.count
        HStack(alignment: .top, spacing: 28) {
            VStack(alignment: .leading, spacing: 4) {
                Text(saved.dollars).font(D.ui(28, .bold).monospacedDigit()).foregroundStyle(D.t1).contentTransition(.numericText())
                Text("saved against sending everything to \(baseline)").font(D.ui(12.5)).foregroundStyle(D.t4)
            }
            if counted.isEmpty {
                Text("Nothing saved yet.").font(D.ui(13)).foregroundStyle(D.t3)
                    .help("Send requests through dots, and what they save shows here.")
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    line("\(counted.count.grouped) requests", "\(Int((Double(free) / Double(counted.count) * 100).rounded()))% ran free, on this Mac or in your pools")
                    line("\(credits.grouped) credits", "spent on public pools and the gateway")
                    line(usd.dollars, "billed to your own key")
                    line("\(measured) of \(counted.count)", measured == counted.count ? "with token counts from the model itself"
                         : "with token counts from the model itself; the rest estimated")
                }
            }
            Spacer()
        }
        .padding(16)
        .card()
    }

    private func line(_ value: String, _ label: String) -> some View {
        HStack(spacing: 8) {
            Text(value).font(D.ui(12.5, .semibold).monospacedDigit()).foregroundStyle(D.t1)
            Text(label).font(D.ui(12.5)).foregroundStyle(D.t3)
        }
    }
}

/// The last few requests: where each ran, its tokens, what it cost and what it saved.
struct RecentRequests: View {
    var requests: [RequestRecord]

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(requests.enumerated()), id: \.element.id) { i, r in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 10) {
                        Text(r.date.formatted(date: .omitted, time: .shortened)).font(D.ui(12).monospacedDigit()).foregroundStyle(D.t4)
                            .frame(width: 62, alignment: .leading)
                        Text(r.source).font(D.ui(12.5, .semibold)).foregroundStyle(D.t2).lineLimit(1).frame(width: 130, alignment: .leading)
                        Text("\(r.place) · \(r.model)").font(D.ui(12.5)).foregroundStyle(D.t2).lineLimit(1)
                        Spacer()
                        if let i = r.inputTokens, let o = r.outputTokens {
                            Text("\(i.grouped) in · \(o.grouped) out\(r.measured == true ? "" : " (est.)")").font(D.ui(12).monospacedDigit()).foregroundStyle(D.t4)
                        }
                        Text(cost(r)).font(D.ui(12.5, .semibold).monospacedDigit()).foregroundStyle(D.t1).frame(width: 90, alignment: .trailing)
                        Text(r.baselineUSD == nil ? "" : "saved \(r.savedUSD.dollars)").font(D.ui(12).monospacedDigit()).foregroundStyle(D.t3)
                            .frame(width: 92, alignment: .trailing)
                    }
                    if let f = r.fallback {
                        Text(f).font(D.ui(11.5)).foregroundStyle(D.accentText).padding(.leading, 72)
                    }
                }
                .padding(.horizontal, 16).padding(.vertical, 10)
                if i < requests.count - 1 { Hairline().padding(.horizontal, 16) }
            }
        }
        .card()
    }

    private func cost(_ r: RequestRecord) -> String {
        if let usd = r.usd, usd > 0 { return usd.dollars }
        return r.credits > 0 ? "\(r.credits) credits" : "free"
    }
}
