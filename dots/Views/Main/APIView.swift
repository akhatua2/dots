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
                PageHeader(title: "API", subtitle: "One endpoint for every model you can reach. Works with anything that speaks the OpenAI API.") {
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
                    CapLabel("Keys")
                    Spacer()
                    (Text("Use model ") + Text("\"auto\"").font(D.mono(12, .medium)).foregroundColor(D.t2)
                     + Text(" and dots picks where each request runs."))
                        .font(D.ui(12)).foregroundStyle(D.t4)
                }
                .padding(.top, 26).padding(.bottom, 10)

                VStack(spacing: 0) {
                    ForEach(Array(s.apiKeys.enumerated()), id: \.element.id) { i, key in
                        KeyRow(key: key, isNew: model.revealedKey?.id == key.id || key.createdAt > Date().addingTimeInterval(-600))
                        if i < s.apiKeys.count - 1 { Hairline().padding(.horizontal, 16) }
                    }
                    if s.apiKeys.isEmpty {
                        Text("No keys yet. Make one for each app, so you can see and revoke them one by one.")
                            .font(D.ui(13)).foregroundStyle(D.t4).padding(16)
                    }
                }
                .card()

                CapLabel("Where your requests ran this month").padding(.top, 28).padding(.bottom, 12)
                RequestSplit(requests: s.requests)

                CodeSample(tab: $tab, baseURL: model.gatewayBaseURL, copied: $copied)
                    .padding(.top, 24)
            }
            .padding(.horizontal, 32)
            .padding(.top, 34)
            .padding(.bottom, 40)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
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
                Text(key.reach.summary + (key.monthlyLimit.map { ", up to \($0) credits a month" } ?? ""))
                    .font(D.ui(12)).foregroundStyle(D.t4)
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
        if key.creditsThisMonth == 0 { return "\(key.requestsThisMonth) requests, free" }
        return "\(key.creditsThisMonth.grouped) credits this month"
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
            (.yourPool, "Your pools", D.t2), (.publicPool, "Public pools", D.swirl), (.frontier, "Frontier models", D.t5),
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
                ForEach(["curl", "Python", "JavaScript"], id: \.self) { t in
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
