import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Ask, as a screen of the main window. Laid out like a chat: your question and the answer in a centred
/// column, the question box fixed at the bottom. With nothing asked yet the box sits in the middle.
struct AskScreen: View {
    @Environment(AppModel.self) private var model
    @State private var dropTargeted = false

    private var hasAsked: Bool { model.ask.phase != .idle || !model.ask.answer.isEmpty }

    var body: some View {
        VStack(spacing: 0) {
            if hasAsked {
                conversation
                AskComposer(placeholder: "Ask another question")
                    .padding(.horizontal, 32).padding(.bottom, 24)
                    .frame(maxWidth: Layout.column + 64)
            } else {
                welcome
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .overlay {
            if dropTargeted {
                RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(D.accent, lineWidth: 1.5).padding(12)
            }
        }
        .modifier(FileDrop(enabled: true, targeted: $dropTargeted) { url in model.ask.attach(url) })
    }

    private enum Layout {
        static let column: CGFloat = 680
    }

    /// Before the first question: a line to start from, and the box.
    private var welcome: some View {
        VStack(spacing: 22) {
            Spacer()
            Text("What can the pool help with?")
                .font(D.display(30)).foregroundStyle(D.t1)
            AskComposer(placeholder: "Ask anything. Drop a file to ask about it.")
                .frame(maxWidth: Layout.column)
            Text("Answers run in your pools first, and every one comes with a receipt.")
                .font(D.ui(12.5)).foregroundStyle(D.t4)
            Spacer()
            Spacer()
        }
        .padding(.horizontal, 32)
    }

    /// The question, then the answer with its receipt folded to a line.
    private var conversation: some View {
        ZStack(alignment: .topTrailing) {
            LiveScroll {
                VStack(alignment: .leading, spacing: 22) {
                    HStack {
                        Spacer(minLength: 60)
                        Text(model.ask.asked)
                            .font(D.ui(15)).foregroundStyle(D.t1)
                            .textSelection(.enabled)
                            .padding(.horizontal, 16).padding(.vertical, 10)
                            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(D.raised))
                    }
                    AskBody(compact: true)
                }
                .padding(.horizontal, 32).padding(.top, 56).padding(.bottom, 20)
                .frame(maxWidth: Layout.column + 64, alignment: .leading)
                .frame(maxWidth: .infinity)
            }
            .scrollIndicators(.never)

            Button { model.ask.clear() } label: {
                Label("New question", systemImage: "plus").font(D.ui(12.5, .medium))
            }
            .buttonStyle(.plain).foregroundStyle(D.t3)
            .padding(.horizontal, 32).padding(.top, 16)
        }
    }
}

/// The question box: a paperclip or the attached file, the text, the route, and send.
struct AskComposer: View {
    @Environment(AppModel.self) private var model
    @FocusState private var focused: Bool
    var placeholder: String

    var body: some View {
        @Bindable var ask = model.ask
        VStack(alignment: .leading, spacing: 10) {
            if let attachment = ask.attachment {
                HStack(spacing: 6) {
                    Image(systemName: "doc").font(.system(size: 11, weight: .medium))
                    Text(attachment.name).font(D.ui(12.5, .medium)).lineLimit(1).truncationMode(.middle)
                    Button { ask.attachment = nil } label: { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)) }
                        .buttonStyle(.plain).foregroundStyle(D.t4)
                }
                .foregroundStyle(D.t2)
                .padding(.horizontal, 10).frame(height: 26)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(D.raised))
            }
            TextField(placeholder, text: $ask.input, axis: .vertical)
                .textFieldStyle(.plain)
                .font(D.ui(16)).foregroundStyle(D.t1)
                .lineLimit(1...8)
                .focused($focused)
                .onSubmit(send)
            HStack(spacing: 10) {
                Button(action: pickFile) { Image(systemName: "paperclip").font(.system(size: 13, weight: .medium)) }
                    .buttonStyle(.plain).foregroundStyle(D.t4)
                Spacer()
                RoutePicker()
                SendButton(enabled: canSend, busy: ask.isBusy, action: send)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(D.card))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(Color.white.opacity(focused ? 0.16 : 0.08), lineWidth: 1))
        .onAppear { focused = true }
        .onChange(of: ask.focusToken) { _, _ in focused = true }
    }

    /// Asks, then empties the box so it is ready for the next question. The question stays above as `asked`.
    private func send() {
        guard canSend else { return }
        model.ask.submit(model: model)
        model.ask.input = ""
    }

    private var canSend: Bool {
        !model.ask.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !model.ask.isBusy
    }

    private func pickFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf, .plainText, .text, .sourceCode, .json, .commaSeparatedText]
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { model.ask.attach(url) }
    }
}

/// The round orange send button. It dims when there is nothing to send and shows progress while an answer is on its way.
private struct SendButton: View {
    var enabled: Bool
    var busy: Bool
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle().fill(enabled ? D.accent : D.btn)
                if busy {
                    ProgressView().controlSize(.small).tint(D.t3)
                } else {
                    Image(systemName: "arrow.up").font(.system(size: 13, weight: .bold)).foregroundStyle(enabled ? D.onAccent : D.t5)
                }
            }
            .frame(width: 30, height: 30)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }
}
