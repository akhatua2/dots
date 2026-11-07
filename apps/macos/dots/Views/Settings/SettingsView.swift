import SwiftUI

struct SettingsView: View {
    /// True when shown as a screen of the main window, false in a window of its own.
    var inline = false
    @Environment(AppModel.self) private var model
    @State private var confirmReset = false
    @State private var cloudAddress = ""
    @Environment(\.isSnapshot) private var isSnapshot

    var body: some View {
        @Bindable var model = model
        LiveScroll {
            VStack(alignment: .leading, spacing: 22) {
                if inline {
                    PageHeader(title: "Settings", subtitle: "Where prompts run, what they may cost, and this Mac's API") { EmptyView() }
                }
                section("Real models", "With an endpoint set, that route's prompts run on a real model. Without one, dots simulates the replies and says so.") {
                    EndpointEditor(title: "This Mac", detail: "LM Studio or Ollama. Everyday prompts run here first, privately and for free.",
                                   config: $model.settings.poolEndpoint,
                                   presets: [("LM Studio", "http://localhost:1234/v1"), ("Ollama", "http://localhost:11434/v1")])
                    EndpointEditor(title: "Your frontier key", detail: "Anthropic, or any OpenAI-compatible provider, billed to your key.",
                                   config: $model.settings.frontierEndpoint,
                                   presets: [("Anthropic", "https://api.anthropic.com/v1")])
                }

                section("How dots picks a route", "Privacy first, then your own pools, then the network or a frontier model.") {
                    VStack(spacing: 12) {
                        row("Keep every prompt private", detail: "Only pools you share with people you know") {
                            DSwitch(isOn: $model.settings.rules.privateByDefault, label: "Keep every prompt private")
                        }
                        row("Most credits for one prompt", detail: "Anything costing more asks you first") {
                            HStack(spacing: 8) {
                                Chip(title: "−") { model.settings.rules.maxCreditsPerAnswer = max(0, model.settings.rules.maxCreditsPerAnswer - 5) }
                                Text("\(model.settings.rules.maxCreditsPerAnswer)").font(D.ui(13.5, .semibold).monospacedDigit()).foregroundStyle(D.t1)
                                    .frame(minWidth: 30)
                                Chip(title: "+") { model.settings.rules.maxCreditsPerAnswer = min(200, model.settings.rules.maxCreditsPerAnswer + 5) }
                            }
                        }
                        row("Prefer", detail: "When more than one place could run it") {
                            HStack(spacing: 6) {
                                ForEach(RouterRules.Prefer.allCases) { p in
                                    Chip(title: p.title, selected: model.settings.rules.prefer == p) { model.settings.rules.prefer = p }
                                }
                            }
                        }
                        row("Pay for Claude with", detail: model.settings.payFrontierWith == .credits
                            ? "Credits through the dots gateway, then your key" : "Your key, then credits through the gateway") {
                            HStack(spacing: 6) {
                                ForEach(PayWith.allCases) { p in
                                    Chip(title: p.title, selected: model.settings.payFrontierWith == p) { model.settings.payFrontierWith = p }
                                }
                            }
                        }
                        row("Measure savings against", detail: "The model you'd otherwise send everything to") {
                            HStack(spacing: 6) {
                                ForEach(FrontierModel.all) { f in
                                    Chip(title: f.name.replacingOccurrences(of: "Claude ", with: ""), selected: model.settings.savingsBaseline == f.id) {
                                        model.settings.savingsBaseline = f.id
                                    }
                                }
                            }
                        }
                        row("Ask from anywhere", detail: "Works over any app") {
                            Text("⌥ Space").font(D.ui(13, .semibold)).foregroundStyle(D.t2)
                        }
                    }
                }

                section("API for your apps", "A server on this Mac that speaks OpenAI's API and Anthropic's. Only this Mac can reach it.") {
                    VStack(spacing: 12) {
                        row("Run the local API", detail: model.gatewayState) {
                            DSwitch(isOn: $model.settings.gatewayEnabled, label: "Run the local API")
                        }
                        row("Port", detail: model.gatewayBaseURL) {
                            Group {
                                if isSnapshot {
                                    Text(String(model.settings.gatewayPort))
                                } else {
                                    TextField("", value: $model.settings.gatewayPort, format: .number.grouping(.never)).textFieldStyle(.plain)
                                }
                            }
                            .multilineTextAlignment(.trailing).font(D.mono(13)).foregroundStyle(D.t1).frame(width: 70, alignment: .trailing)
                        }
                    }
                }

                section("dots cloud", "Pools, credits, badges and weekly notes. Until the real cloud is up, a mock runs on this Mac.") {
                    VStack(spacing: 12) {
                        row("Use the mock cloud", detail: model.backend.connection) {
                            DSwitch(isOn: $model.settings.cloud.useMock, label: "Use the mock cloud")
                        }
                        if !model.settings.cloud.useMock {
                            HStack(spacing: 8) {
                                Field(placeholder: "The cloud's address, https://…", text: $cloudAddress, mono: true)
                                    .onSubmit { model.settings.cloud.address = cloudAddress }
                                Button("Connect") { model.settings.cloud.address = cloudAddress }.buttonStyle(DButtonStyle(kind: .grey))
                            }
                        }
                        if model.backend.isMock {
                            row("Weekly note", detail: "Comes once a week") {
                                Button("Send one now") { model.backend.sendNoteNow() }.buttonStyle(DButtonStyle(kind: .grey))
                            }
                            row("Speed", detail: "How fast lending time passes, for demos") {
                                HStack(spacing: 6) {
                                    ForEach([1.0, 10, 60], id: \.self) { speed in
                                        Chip(title: "\(Int(speed))×", selected: model.settings.demoSpeed == speed) { model.settings.demoSpeed = speed }
                                    }
                                }
                            }
                            row("Start over", detail: "Back to the sample network, with your real Mac in it") {
                                Button("Reset…") { confirmReset = true }.buttonStyle(DButtonStyle(kind: .grey))
                            }
                        }
                    }
                }
            }
            .modifier(SettingsMargins(inline: inline))
        }
        .frame(width: inline ? nil : 560, height: inline || isSnapshot ? nil : 640)
        .background(D.bg2)
        .preferredColorScheme(.dark)
        .onAppear { cloudAddress = model.settings.cloud.address }
        .confirmationDialog("Reset the sample network?", isPresented: $confirmReset) {
            Button("Reset", role: .destructive) {
                model.backend.reset()
                model.route = .explore
                model.refreshLending()
            }
        } message: {
            Text("Your pools, credits, badges and keys go back to the start. Your settings and endpoint keys stay.")
        }
    }

    private func section<Content: View>(_ title: String, _ detail: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(D.ui(15, .bold)).foregroundStyle(D.t1).help(detail)
            }
            content()
                .padding(14)
                .card()
        }
    }

    private func row<Trailing: View>(_ title: String, detail: String, @ViewBuilder trailing: () -> Trailing) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(D.ui(13.5, .medium)).foregroundStyle(D.t2).help(detail)
            }
            Spacer()
            trailing()
        }
    }
}

/// One endpoint: where it is, which model, the key (kept in the Keychain), and a connection test.
struct EndpointEditor: View {
    var title: String
    var detail: String
    @Binding var config: EndpointConfig
    var presets: [(String, String)]
    @State private var key = ""
    @State private var test: String?
    @State private var models: [String] = []
    @State private var workspaces: [OpenAIClient.Workspace] = []
    @State private var askForWorkspace = false
    @State private var consoleLink = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(D.ui(13.5, .semibold)).foregroundStyle(D.t1).help(detail)
                }
                Spacer()
                DSwitch(isOn: $config.enabled, label: "Use \(title) endpoint")
            }
            if config.enabled {
                HStack(spacing: 6) {
                    ForEach(presets, id: \.0) { name, url in
                        Chip(title: name, selected: config.baseURL == url) {
                            config.baseURL = url
                            if config.label == "Local" || config.label.isEmpty { config.label = name }
                        }
                    }
                }
                Field(placeholder: "https://…/v1", text: $config.baseURL, mono: true)
                HStack(spacing: 8) {
                    Field(placeholder: "Model, like Qwen3.5-9B", text: $config.model, mono: true)
                    if !models.isEmpty {
                        Menu("Pick") {
                            ForEach(models, id: \.self) { m in Button(m) { config.model = m } }
                        }
                        .menuStyle(.button).fixedSize()
                    }
                }
                HStack(spacing: 8) {
                    Field(placeholder: "API key, kept in your Keychain", text: $key, secure: true)
                        .onSubmit { Keychain.set(key, for: config.keychainAccount) }
                        .onChange(of: key) { _, value in Keychain.set(value, for: config.keychainAccount) }
                    Button("Test") { runTest() }.buttonStyle(DButtonStyle(kind: .grey))
                }
                if config.isAnthropic || config.workspace != nil || askForWorkspace {
                    HStack(spacing: 8) {
                        Field(placeholder: "Workspace ID, if your key works in more than one", text: workspaceID, mono: true)
                        if !workspaces.isEmpty {
                            Menu("Pick") {
                                ForEach(workspaces, id: \.id) { w in
                                    Button("\(w.name ?? w.id)  \(w.id)") { config.workspaceID = w.id; runTest() }
                                }
                            }
                            .menuStyle(.button).fixedSize()
                        }
                    }
                }
                if let test { Text(test).font(D.ui(12)).foregroundStyle(D.t3).fixedSize(horizontal: false, vertical: true) }
                if consoleLink, let page = URL(string: "https://platform.claude.com/settings/workspaces") {
                    Link("Open the Workspaces page", destination: page).font(D.ui(12, .medium)).foregroundStyle(D.accentText)
                }
                if !config.isLocal && key.trimmingCharacters(in: .whitespaces).isEmpty {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Image(systemName: "key").font(.system(size: 11, weight: .medium))
                        Text("Add your \(config.placeName) API key to run real prompts.")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .font(D.ui(12)).foregroundStyle(D.accentText)
                    .help("Until then, this route's replies are simulated.")
                }
                if config.isLocal {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Image(systemName: "desktopcomputer").font(.system(size: 11, weight: .medium))
                        Text("On this Mac\(config.serverName.map { ", in \($0)" } ?? "").")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .font(D.ui(12)).foregroundStyle(D.t3)
                    .help("What runs here stays private and costs no credits.")
                }
            }
        }
        .onAppear { key = Keychain.get(config.keychainAccount) ?? "" }
    }

    private var workspaceID: Binding<String> {
        Binding(get: { config.workspaceID ?? "" }, set: { config.workspaceID = $0 })
    }

    private func runTest() {
        test = "Checking…"
        consoleLink = false
        let config = config
        Task { @MainActor in
            do {
                let list = try await OpenAIClient.models(config)
                models = list
                test = list.isEmpty ? "Connected." : "Connected. \(list.count) models: \(list.prefix(3).joined(separator: ", "))\(list.count > 3 ? "…" : "")"
            } catch let error as TransportError where error.needsWorkspace || error.badWorkspace {
                askForWorkspace = true
                await offerWorkspaces(config, after: error)
            } catch {
                test = error.localizedDescription
            }
        }
    }

    /// The key works in more than one workspace: offer the ones it can use, or point to the Console's list.
    private func offerWorkspaces(_ config: EndpointConfig, after error: TransportError) async {
        workspaces = (try? await OpenAIClient.workspaces(config)) ?? []
        let lead = error.needsWorkspace ? "This key works in more than one workspace." : (error.errorDescription ?? "")
        if workspaces.isEmpty {
            test = "\(lead) Copy the ID of the one dots should use from the Workspaces page in the Claude Console."
            consoleLink = true
        } else {
            test = "\(lead) Pick the one dots should use."
        }
    }
}

/// In the main window Settings is a page like the others; in a window of its own it keeps a plain margin.
private struct SettingsMargins: ViewModifier {
    var inline: Bool

    func body(content: Content) -> some View {
        if inline { content.page() } else { content.padding(24) }
    }
}
