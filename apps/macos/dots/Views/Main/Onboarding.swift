import SwiftUI

/// The first launch: what dots is, what friends call you, and what this Mac lends. It ends where joining always
/// ends, on "You're in." The swirl sits behind it as it does behind every moment between screens.
struct OnboardingView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.isSnapshot) private var isSnapshot
    @Environment(\.artClock) private var artClock
    @State private var step: Step
    @State private var name = ""
    @State private var lendGB: Int?
    @State private var started = Date()
    @State private var finishing = false

    enum Step { case welcome, name, lend }

    /// `step`: where it starts, the welcome but for snapshots
    init(step: Step = .welcome) {
        _step = State(initialValue: step)
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: isSnapshot)) { context in
            let t = artClock.map { _ in 2.0 } ?? context.date.timeIntervalSince(started)
            ZStack {
                D.bg0
                SwirlCanvas(phase: 0.35 + t * 0.02, reveal: 1, quiet: CGSize(width: 0.52, height: 0.42))
                VStack(spacing: 0) {
                    switch step {
                    case .welcome: welcome
                    case .name: nameStep
                    case .lend: lendStep
                    }
                }
                .frame(maxWidth: 560)
                .padding(40)
            }
        }
        .onAppear { if name.isEmpty { name = model.state.me.name } }
    }

    // MARK: Steps

    private var welcome: some View {
        VStack(spacing: 18) {
            Wordmark(size: 40)
            Text("Pool your Mac with friends'.").font(D.display(54)).foregroundStyle(D.t1)
                .multilineTextAlignment(.center).minimumScaleFactor(0.6)
            Text("Lend the memory your Mac isn't using, and ask the models your pools run together.")
                .font(D.ui(17)).foregroundStyle(D.t2).multilineTextAlignment(.center).lineSpacing(3)
            Button("Get Started") { step = .name }
                .buttonStyle(DButtonStyle(kind: .primary, large: true)).keyboardShortcut(.defaultAction)
                .padding(.top, 14)
        }
    }

    private var nameStep: some View {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return VStack(spacing: 18) {
            Text("What should people call you?").font(D.display(40)).foregroundStyle(D.t1)
                .multilineTextAlignment(.center).minimumScaleFactor(0.6)
            Text("Friends see it beside your Mac in the pools you share.")
                .font(D.ui(15)).foregroundStyle(D.t3)
            Field(placeholder: "Your name", text: $name)
                .padding(.horizontal, 14).frame(height: 46)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(D.bg0))
                .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(D.panelEdge, lineWidth: 0.5))
                .frame(maxWidth: 360)
                .onSubmit { if !trimmed.isEmpty { step = .lend } }
            Button("Continue") { step = .lend }
                .buttonStyle(DButtonStyle(kind: .primary, large: true)).keyboardShortcut(.defaultAction)
                .disabled(trimmed.isEmpty)
                .padding(.top, 6)
        }
    }

    private var lendStep: some View {
        let mac = model.state.thisMac
        let most = mac.maxLendGB
        let gb = min(lendGB ?? max(1, most / 2), most)
        let pool = model.firstPool
        return VStack(spacing: 18) {
            if most > 0 {
                Text("How much can your Mac lend?").font(D.display(40)).foregroundStyle(D.t1)
                    .multilineTextAlignment(.center).minimumScaleFactor(0.6)
                Text("\(gb) GB").font(D.ui(30, .semibold).monospacedDigit()).foregroundStyle(D.t1)
                    .contentTransition(.numericText())
                ThinSlider(value: Binding(get: { gb }, set: { lendGB = $0 }), range: 1...max(2, most), label: "Lend")
                    .frame(maxWidth: 360)
                Text(lendNote(mac: mac, pool: pool))
                    .font(D.ui(14)).foregroundStyle(D.t3).multilineTextAlignment(.center).lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 16) {
                    Button("Not Now") { finish(lend: 0) }.buttonStyle(DButtonStyle(kind: .quiet, large: true))
                    Button("Lend \(gb) GB") { finish(lend: gb) }
                        .buttonStyle(DButtonStyle(kind: .primary, large: true)).keyboardShortcut(.defaultAction)
                }
                .disabled(finishing)
                .padding(.top, 6)
            } else {
                Text("Your Mac keeps its memory.").font(D.display(40)).foregroundStyle(D.t1)
                Text("It has none to spare for lending, but you can still ask every pool you join.")
                    .font(D.ui(15)).foregroundStyle(D.t3).multilineTextAlignment(.center)
                Button("Continue") { finish(lend: 0) }
                    .buttonStyle(DButtonStyle(kind: .primary, large: true)).keyboardShortcut(.defaultAction)
                    .disabled(finishing)
            }
            if let error = model.onboardingError {
                Text("dots couldn't reach its cloud: \(error) Check your connection and try again.")
                    .font(D.ui(13)).foregroundStyle(D.t2).multilineTextAlignment(.center)
            }
        }
    }

    private func lendNote(mac: MacDevice, pool: Pool?) -> String {
        let keeps = "\(mac.keptGB) GB always stays with your \(mac.kind), and it lends only while it's plugged in."
        guard let pool else { return keeps }
        return "It starts in \(pool.name), which runs \(pool.modelLabel ?? "its model"). " + keeps
    }

    private func finish(lend gb: Int) {
        finishing = true
        model.finishOnboarding(name: name.trimmingCharacters(in: .whitespacesAndNewlines), lendGB: gb)
        finishing = false
    }
}
