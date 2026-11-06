import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct AskPanelView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.isSnapshot) private var isSnapshot
    @Environment(\.openSettings) private var openSettings
    @FocusState private var focused: Bool
    @State private var dropTargeted = false
    /// The answer's own height, measured inside the scroll view. The panel is sized from this, never the other
    /// way round: sizing the scroll view to fit while the window sized itself to the scroll view looped forever.
    @State private var bodyHeight: CGFloat = 0

    var body: some View {
        @Bindable var ask = model.ask
        VStack(alignment: .leading, spacing: 0) {
            inputRow(ask: ask)
            if ask.phase != .idle {
                Hairline()
                LiveScroll {
                    VStack(alignment: .leading, spacing: 14) {
                        AskBody()
                    }
                    .padding(18)
                    .frame(width: 720, alignment: .leading)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { bodyHeight = $0 }
                }
                .scrollIndicators(.never)
                .frame(height: isSnapshot ? nil : min(max(bodyHeight, 40), 560))
            }
            Hairline()
            HStack(spacing: 14) {
                hint("return", "ask")
                hint("esc", "close")
                Spacer()
                Button { model.showSettings(openSettings) } label: { Image(systemName: "gearshape").font(.system(size: 11)) }
                    .buttonStyle(.plain).foregroundStyle(D.t5)
                    .help("Settings")
            }
            .padding(.horizontal, 18)
            .help("Drop a file on the panel to ask about it")
            .frame(height: 34)
        }
        .frame(width: 720)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Color(hex: 0x1F1F1F, opacity: 0.985)))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
            .strokeBorder(dropTargeted ? D.accent : Color.white.opacity(0.1), lineWidth: dropTargeted ? 1.5 : 1))
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .fixedSize(horizontal: false, vertical: true)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height in
            if !isSnapshot { AskPanelController.shared.fit(height: height) }
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .modifier(FileDrop(enabled: !isSnapshot, targeted: $dropTargeted) { url in model.ask.attach(url) })
        .onAppear { focused = true }
        .onChange(of: ask.focusToken) { _, _ in focused = true }
        .preferredColorScheme(.dark)
    }

    private func inputRow(ask: AskController) -> some View {
        @Bindable var ask = ask
        return HStack(spacing: 10) {
            Group {
                if ask.isBusy {
                    ProgressView().controlSize(.small).tint(D.accent)
                } else {
                    Circle().fill(D.accent).frame(width: 11, height: 11)
                }
            }
            .frame(width: 18)

            if let a = ask.attachment {
                HStack(spacing: 6) {
                    Image(systemName: "doc").font(.system(size: 11, weight: .medium))
                    Text(a.name).font(D.ui(12.5, .medium)).lineLimit(1).truncationMode(.middle)
                        .frame(maxWidth: 170, alignment: .leading).fixedSize()
                    Button { ask.attachment = nil } label: { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)) }
                        .buttonStyle(.plain).foregroundStyle(D.t4)
                }
                .foregroundStyle(D.t2)
                .padding(.horizontal, 10).frame(height: 28)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(D.raised))
                .fixedSize()
            } else {
                Button(action: pickFile) { Image(systemName: "paperclip").font(.system(size: 13, weight: .medium)) }
                    .buttonStyle(.plain).foregroundStyle(D.t4).help("Ask about a file")
            }

            if isSnapshot {
                Text(ask.input.isEmpty ? "Ask anything" : ask.input)
                    .font(D.ui(18)).foregroundStyle(ask.input.isEmpty ? D.t5 : D.t1).lineLimit(1)
                Spacer(minLength: 0)
            } else {
                TextField("Ask anything", text: $ask.input)
                    .textFieldStyle(.plain)
                    .font(D.ui(18))
                    .foregroundStyle(D.t1)
                    .focused($focused)
                    .onSubmit { model.ask.submit(model: model) }
            }

            RoutePicker()
        }
        .padding(.horizontal, 18)
        .frame(height: 60)
    }

    private func hint(_ key: String, _ label: String) -> some View {
        HStack(spacing: 5) {
            Text(key).font(D.ui(10.5, .semibold)).foregroundStyle(D.t3)
                .padding(.horizontal, 5).frame(height: 17)
                .background(RoundedRectangle(cornerRadius: 4).fill(D.raised))
            Text(label).font(D.ui(11.5)).foregroundStyle(D.t5)
        }
    }

    private func pickFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf, .plainText, .text, .sourceCode, .json, .commaSeparatedText]
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { model.ask.attach(url) }
        AskPanelController.shared.show()
    }
}

/// "Auto", or a pool or model you pick yourself.
struct RoutePicker: View {
    @Environment(AppModel.self) private var model
    @Environment(\.isSnapshot) private var isSnapshot

    var body: some View {
        if isSnapshot { pill } else { menu }
    }

    private var pill: some View {
        HStack(spacing: 5) {
            Text(label).font(D.ui(12.5, .semibold)).lineLimit(1)
            Image(systemName: "chevron.down").font(.system(size: 8, weight: .heavy))
        }
        .foregroundStyle(D.accentText)
        .padding(.horizontal, 11).frame(height: 26)
        .background(Capsule().fill(D.accentTint))
    }

    private var menu: some View {
        let s = model.state
        let own = s.myPools.filter { !$0.isPublic && $0.model != nil }
        let pub = s.publicPools.filter { $0.model != nil }.sorted { ($0.model?.minGB ?? 0) > ($1.model?.minGB ?? 0) }.prefix(3)
        return Menu {
            Button("Auto: dots picks") { model.ask.forcedID = nil }
            Section("Your pools, free") {
                ForEach(own) { p in Button("\(p.name) · \(p.model!.name)") { model.ask.forcedID = "pool:\(p.id)" } }
            }
            Section("Public pools") {
                ForEach(Array(pub)) { p in Button("\(p.name) · \(p.model!.name) · \(p.model!.credits) credits") { model.ask.forcedID = "pool:\(p.id)" } }
            }
            Section("Frontier, paid in credits") {
                ForEach(FrontierModel.all) { f in Button("\(f.name) · \(f.credits) credits") { model.ask.forcedID = "frontier:\(f.id)" } }
            }
        } label: {
            pill
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .fixedSize()
    }

    private var label: String {
        guard let id = model.ask.forcedID else { return "Auto" }
        if id.hasPrefix("frontier:"), let f = FrontierModel.all.first(where: { "frontier:\($0.id)" == id }) { return f.name }
        if id.hasPrefix("pool:"), let p = model.state.pool(String(id.dropFirst(5))) { return p.name }
        return "Auto"
    }
}

/// Everything under the question: routing, the answer, its receipt, and the second opinion.
struct AskBody: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let ask = model.ask
        if ask.phase == .routing, let decision = ask.decision {
            RoutingRow(decision: decision)
        }
        if !ask.answer.isEmpty {
            Text(ask.answer)
                .font(D.ui(15))
                .foregroundStyle(D.t2)
                .lineSpacing(3.5)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        switch ask.phase {
        case .needsConsent(let why):
            ConsentCard(message: why)
        case .failed(let message):
            IconLine(symbol: "exclamationmark.triangle", text: message, color: D.t2, size: 13)
        default:
            EmptyView()
        }
        if let receipt = ask.receipt, ask.phase != .routing {
            ReceiptView(receipt: receipt)
        }
        if let second = ask.second {
            SecondOpinionView(second: second)
        }
    }
}

struct RoutingRow: View {
    var decision: RouteDecision
    var body: some View {
        HStack(spacing: 10) {
            Text("Choosing where to run it").font(D.ui(13)).foregroundStyle(D.t4)
            ForEach(decision.candidates) { c in
                let picked = c == decision.chosen
                Text(c.place == "Anthropic" ? c.model : c.place)
                    .font(D.ui(12.5, .medium))
                    .foregroundStyle(picked ? D.t1 : D.t4)
                    .padding(.horizontal, 10).frame(height: 26)
                    .background(Capsule().fill(D.raised))
                    .overlay(Capsule().strokeBorder(picked ? D.t1 : .clear, lineWidth: 1.5))
            }
        }
    }
}

struct ConsentCard: View {
    @Environment(AppModel.self) private var model
    var message: String
    var body: some View {
        let frontier = model.ask.decision?.candidates.first { $0.destination == .frontier }
        VStack(alignment: .leading, spacing: 10) {
            IconLine(symbol: "lock", text: message, color: D.t1, size: 13.5, weight: .medium)
            if let frontier {
                HStack {
                    Text("You can send it to \(frontier.model) instead, for \(frontier.credits) credits. It would leave your pools.")
                        .font(D.ui(12.5)).foregroundStyle(D.t3)
                    Spacer()
                    HoverAccentButton(title: "Send to \(frontier.model)") { model.ask.sendAnyway(model: model) }
                }
            }
        }
        .padding(14)
        .card(fill: D.deep)
    }
}

struct ReceiptView: View {
    var receipt: Receipt

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                if receipt.macs.isEmpty {
                    Image(systemName: icon).font(.system(size: 14, weight: .medium)).foregroundStyle(D.t2)
                        .frame(width: 32, height: 32).background(RoundedRectangle(cornerRadius: 9).fill(D.raised))
                } else {
                    MacsServing(macs: receipt.macs, asleepID: receipt.asleepMacID, serving: !receipt.finished)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text("Ran on \(receipt.place) · \(receipt.model)").font(D.ui(14, .semibold)).foregroundStyle(D.t1).lineLimit(1)
                    Text(subline).font(D.ui(12)).foregroundStyle(D.t4)
                }
                Spacer()
                HStack(spacing: 6) {
                    if receipt.paidWithKey {
                        Tag(text: receipt.finished ? "\(receipt.usd.dollars) on your key" : "On your key", symbol: "key")
                    } else if receipt.credits == 0 {
                        Tag(text: "Free for you")
                    } else {
                        Tag(text: "\(receipt.credits) credits", symbol: "smallcircle.filled.circle")
                    }
                    if receipt.isPrivate { Tag(text: "Private", symbol: "lock") }
                }
                .fixedSize()
            }
            .padding(14)
            Hairline().padding(.horizontal, 14)
            VStack(alignment: .leading, spacing: 9) {
                CapLabel("Why this route")
                ForEach(receipt.reasons, id: \.self) { r in
                    IconLine(symbol: r.symbol, text: r.text, color: D.t3, size: 13)
                }
                if let note = receipt.fallbackNote {
                    IconLine(symbol: "arrow.uturn.right", text: note, color: D.t1, size: 13)
                }
                if let note = receipt.handoffNote {
                    IconLine(symbol: "arrow.left.arrow.right", text: note, color: D.t1, size: 13)
                }
                if receipt.isSimulated {
                    IconLine(symbol: "circle.dashed", text: "Simulated by the mock cloud: the pool and the words are stand-ins.", color: D.t4, size: 12.5)
                }
            }
            .padding(14)
        }
        .card(fill: D.deep)
    }

    private var icon: String {
        switch receipt.destination {
        case .thisMac: "desktopcomputer"
        case .frontier: "sparkle"
        case .publicPool: "globe"
        case .yourPool: "network"
        }
    }

    private var subline: String {
        var parts: [String] = []
        if !receipt.macs.isEmpty {
            let active = receipt.macs.filter { $0.id != receipt.asleepMacID }.count
            parts.append("\(active) Macs")
        }
        if receipt.tokPerSec > 0 { parts.append("\(Int(receipt.tokPerSec.rounded())) tok/s") }
        if let i = receipt.inputTokens, let o = receipt.outputTokens { parts.append("\(i.grouped) in · \(o.grouped) out") }
        if receipt.saved > 0, !receipt.paidWithKey { parts.append("saved \(receipt.saved) credits") }
        return parts.joined(separator: " · ")
    }
}

/// The Macs working on an answer. Rings pulse while they serve; one that falls asleep dims and gets a moon.
struct MacsServing: View {
    var macs: [ReceiptMac]
    var asleepID: String?
    var serving: Bool

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 20, paused: !serving)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            HStack(spacing: 6) {
                ForEach(Array(macs.enumerated()), id: \.element.id) { i, mac in
                    let asleep = mac.id == asleepID
                    let pulse = serving && !asleep ? 0.3 + 0.7 * (0.5 + 0.5 * sin(2 * .pi * (t * 1.6 - Double(i) / 4))) : 0
                    Avatar(text: mac.initial, size: 26, isYou: mac.initial == "You", dimmed: asleep)
                        .overlay(Circle().strokeBorder(Color.white.opacity(pulse * 0.9), lineWidth: 1.5).padding(-3))
                        .overlay(alignment: .topTrailing) {
                            if asleep {
                                Image(systemName: "moon.fill").font(.system(size: 8)).foregroundStyle(D.t2)
                                    .padding(2.5).background(Circle().fill(D.deep)).offset(x: 4, y: -4)
                            }
                        }
                        .help(asleep ? "\(mac.name)'s Mac is asleep" : "\(mac.name)'s Mac")
                }
            }
        }
    }
}

struct SecondOpinionView: View {
    @Environment(AppModel.self) private var model
    var second: AskController.SecondOpinion

    var body: some View {
        let c = second.candidate
        switch second.phase {
        case .idle, .failed:
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Want a second opinion?").font(D.ui(12)).foregroundStyle(D.t4)
                        HStack(spacing: 10) {
                            Text(c.model).font(D.ui(14, .semibold)).foregroundStyle(D.t1)
                            IconLine(symbol: "smallcircle.filled.circle", text: "\(c.credits) credits", color: D.t2, size: 12.5)
                            if c.listCredits > c.credits {
                                Text("\(Int((Pricing.terms.discount * 100).rounded()))% off with credits")
                                    .font(D.ui(12)).foregroundStyle(D.t4)
                            }
                        }
                        IconLine(symbol: "arrow.up.right.square", text: "Leaves your pools and goes to \(c.place)", color: D.t4, size: 12)
                    }
                    Spacer()
                    HoverAccentButton(title: "Ask \(c.model.components(separatedBy: " ").first ?? c.model)", large: true) {
                        model.ask.askSecondOpinion(model: model)
                    }
                }
                if case .failed(let message) = second.phase {
                    Text(message).font(D.ui(12)).foregroundStyle(D.t3)
                }
            }
            .padding(14)
            .card(fill: D.bg2, stroke: D.line)
        default:
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 6) {
                    Text(c.model).font(D.ui(13.5, .bold)).foregroundStyle(D.t1)
                    Text("via dots").font(D.ui(12.5)).foregroundStyle(D.t4)
                }
                Text(second.text.isEmpty ? " " : second.text)
                    .font(D.ui(15)).foregroundStyle(D.t2).lineSpacing(3.5).textSelection(.enabled)
                if let receipt = second.receipt, second.phase == .done {
                    HStack(spacing: 14) {
                        IconLine(symbol: "arrow.up.right.square", text: "Ran at \(receipt.place) · \(receipt.model)", color: D.t1, size: 12.5, weight: .semibold)
                        IconLine(symbol: receipt.paidWithKey ? "key" : "smallcircle.filled.circle",
                                 text: receipt.paidWithKey ? "\(receipt.usd.dollars) on your key" : "\(receipt.credits) credits · saved \(receipt.saved)",
                                 color: D.t3, size: 12.5)
                        if receipt.isSimulated { Tag(text: "Simulated") }
                        Spacer()
                        Text("Balance \(Int(model.state.credits).grouped)").font(D.ui(12.5, .semibold)).foregroundStyle(D.t2)
                    }
                    .padding(12)
                    .card(fill: D.deep)
                }
            }
        }
    }
}

/// Takes a file dropped on the panel. Left off in snapshots, where the drop target draws as a placeholder.
private struct FileDrop: ViewModifier {
    var enabled: Bool
    @Binding var targeted: Bool
    var perform: @MainActor (URL) -> Void

    func body(content: Content) -> some View {
        if enabled {
            content.onDrop(of: [.fileURL], isTargeted: $targeted) { providers in
                guard let provider = providers.first else { return false }
                _ = provider.loadObject(ofClass: URL.self) { url, _ in
                    guard let url else { return }
                    DispatchQueue.main.async { MainActor.assumeIsolated { perform(url) } }
                }
                return true
            }
        } else {
            content
        }
    }
}
