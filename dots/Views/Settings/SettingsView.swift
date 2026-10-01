import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmReset = false

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                section("Real answers", "With an endpoint set, that route's questions get real answers. Without one, dots simulates them and says so.") {
                    EndpointEditor(title: "Local model", detail: "LM Studio or Ollama on this Mac. It answers everyday questions, privately and for free.",
                                   config: $model.settings.poolEndpoint,
                                   presets: [("LM Studio", "http://localhost:1234/v1"), ("Ollama", "http://localhost:11434/v1")])
                    EndpointEditor(title: "Frontier models", detail: "Anthropic, or any OpenAI-compatible provider. Hard questions and second opinions go here.",
                                   config: $model.settings.frontierEndpoint,
                                   presets: [("Anthropic", "https://api.anthropic.com/v1")])
                }

                section("How dots picks a route", "Privacy first, then your own pools, then the network or a frontier model.") {
                    VStack(spacing: 12) {
                        row("Keep every question private", detail: "Only pools you share with people you know") {
                            DSwitch(isOn: $model.settings.rules.privateByDefault, label: "Keep every question private")
                        }
                        row("Most credits for one answer", detail: "Anything costing more asks you first") {
                            Stepper(value: $model.settings.rules.maxCreditsPerAnswer, in: 0...200, step: 5) {
                                Text("\(model.settings.rules.maxCreditsPerAnswer)").font(D.ui(13.5, .semibold).monospacedDigit()).foregroundStyle(D.t1)
                            }
                            .fixedSize()
                        }
                        row("Prefer", detail: "When more than one place could answer") {
                            HStack(spacing: 6) {
                                ForEach(RouterRules.Prefer.allCases) { p in
                                    Chip(title: p.title, selected: model.settings.rules.prefer == p) { model.settings.rules.prefer = p }
                                }
                            }
                        }
                    }
                }

                section("API for your apps", "A server on this Mac that speaks the OpenAI API. Only this Mac can reach it.") {
                    VStack(spacing: 12) {
                        row("Run the local API", detail: model.gatewayState) {
                            DSwitch(isOn: $model.settings.gatewayEnabled, label: "Run the local API")
                        }
                        row("Port", detail: model.gatewayBaseURL) {
                            TextField("", value: $model.settings.gatewayPort, format: .number.grouping(.never))
                                .textFieldStyle(.plain).multilineTextAlignment(.trailing)
                                .font(D.mono(13)).foregroundStyle(D.t1).frame(width: 70)
                        }
                    }
                }

                section("Simulation", "The pools, Macs and credits are simulated on this Mac until dots connects to a real hub.") {
                    VStack(spacing: 12) {
                        row("Speed", detail: "How fast credits and sleeping Macs move, for demos") {
                            HStack(spacing: 6) {
                                ForEach([1.0, 10, 60], id: \.self) { speed in
                                    Chip(title: "\(Int(speed))×", selected: model.settings.demoSpeed == speed) { model.settings.demoSpeed = speed }
                                }
                            }
                        }
                        row("Start over", detail: "Back to the sample network, with your real Mac in it") {
                            Button("Reset…") { confirmReset = true }.buttonStyle(DButtonStyle(kind: .grey))
                        }
                        row("Ask from anywhere", detail: "Works over any app") {
                            Text("⌥ Space").font(D.ui(13, .semibold)).foregroundStyle(D.t2)
                        }
                    }
                }
            }
            .padding(24)
        }
        .frame(width: 560, height: 640)
        .background(D.bg2)
        .preferredColorScheme(.dark)
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
                Text(title).font(D.ui(15, .bold)).foregroundStyle(D.t1)
                Text(detail).font(D.ui(12.5)).foregroundStyle(D.t4)
            }
            content()
                .padding(14)
                .card()
        }
    }

    private func row<Trailing: View>(_ title: String, detail: String, @ViewBuilder trailing: () -> Trailing) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(D.ui(13.5, .medium)).foregroundStyle(D.t2)
                Text(detail).font(D.ui(12)).foregroundStyle(D.t4).lineLimit(1)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(D.ui(13.5, .semibold)).foregroundStyle(D.t1)
                    Text(detail).font(D.ui(12)).foregroundStyle(D.t4)
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
                    Field(placeholder: "Model, like qwen3-235b", text: $config.model, mono: true)
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
                if let test { Text(test).font(D.ui(12)).foregroundStyle(D.t3) }
                if !config.isLocal && key.trimmingCharacters(in: .whitespaces).isEmpty {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Image(systemName: "key").font(.system(size: 11, weight: .medium))
                        Text("Add your \(config.placeName) API key to get real answers. Until then, answers on this route are simulated.")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .font(D.ui(12)).foregroundStyle(D.accentText)
                }
                if config.isLocal {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Image(systemName: "desktopcomputer").font(.system(size: 11, weight: .medium))
                        Text("On this Mac\(config.serverName.map { ", in \($0)" } ?? ""). It can answer any question, stays private, and costs no credits.")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .font(D.ui(12)).foregroundStyle(D.t3)
                }
            }
        }
        .onAppear { key = Keychain.get(config.keychainAccount) ?? "" }
    }

    private func runTest() {
        test = "Checking…"
        let config = config
        Task { @MainActor in
            do {
                let list = try await OpenAIClient.models(config)
                models = list
                test = list.isEmpty ? "Connected." : "Connected. \(list.count) models: \(list.prefix(3).joined(separator: ", "))\(list.count > 3 ? "…" : "")"
            } catch {
                test = error.localizedDescription
            }
        }
    }
}
