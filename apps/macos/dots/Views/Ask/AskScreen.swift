import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Ask, as a screen of the main window, laid out like a chat app: the open conversation in a centred
/// column and the question box fixed below it. Your chats are listed in the sidebar. Each question is sent with the
/// conversation before it, so a follow-up has its context.
struct AskScreen: View {
    @Environment(AppModel.self) private var model
    @State private var dropTargeted = false

    private var hasConversation: Bool { model.chats.current != nil || model.ask.phase != .idle }

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                if hasConversation {
                    Conversation()
                    AskComposer(placeholder: "Reply")
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
            .modifier(FileDrop(enabled: true, targeted: $dropTargeted) { url in model.ask.attach(url, maxPixels: model.settings.imageMaxPixels) })
        }
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
}

private enum Layout {
    static let column: CGFloat = 680
}

/// The open conversation: every finished turn, then the one being answered now.
private struct Conversation: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let ask = model.ask
        let live = ask.phase != .idle
        LiveScroll {
            VStack(alignment: .leading, spacing: 26) {
                ForEach(model.chats.current?.turns ?? []) { turn in
                    // The turn being shown live below is drawn there, with its second opinion.
                    if !(live && turn.id == ask.recordedTurnID) {
                        QuestionBubble(text: turn.question, attachment: turn.attachment)
                        VStack(alignment: .leading, spacing: 8) {
                            if let name = turn.picture {
                                PictureAnswer(image: model.chats.picture(name))
                            } else {
                                MarkdownText(text: turn.answer)
                            }
                            AnswerActions(turn: turn)
                        }
                    }
                }
                if live {
                    QuestionBubble(text: ask.asked, attachment: ask.attachment?.name)
                    VStack(alignment: .leading, spacing: 8) {
                        if let picture = ask.picture {
                            PictureAnswer(image: picture.image, step: picture.step, of: picture.of, done: picture.done)
                            if case .failed(let why) = ask.phase {
                                Text(why).font(D.ui(13)).foregroundStyle(D.t3)
                            }
                        } else {
                            AskBody(compact: true)
                        }
                        if ask.phase == .done, let turn = model.chats.current?.turns.first(where: { $0.id == ask.recordedTurnID }) {
                            AnswerActions(turn: turn)
                        }
                    }
                }
                Color.clear.frame(height: 1).id("end")
            }
            .padding(.horizontal, 32).padding(.top, 56).padding(.bottom, 20)
            .frame(maxWidth: Layout.column + 64, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .defaultScrollAnchor(.bottom)
        .scrollIndicators(.never)
    }
}

private struct QuestionBubble: View {
    var text: String
    var attachment: String?

    var body: some View {
        HStack {
            Spacer(minLength: 60)
            VStack(alignment: .trailing, spacing: 4) {
                if let attachment {
                    Label(attachment, systemImage: Pictures.isPicture(URL(fileURLWithPath: attachment)) ? "photo" : "doc")
                        .font(D.ui(11.5)).foregroundStyle(D.t4)
                }
                Text(text).font(D.ui(15)).foregroundStyle(D.t1).textSelection(.enabled)
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(D.raised))
        }
    }
}

/// The question box: a paperclip or the attached file or picture, the text, the route, and send.
struct AskComposer: View {
    @Environment(AppModel.self) private var model
    @FocusState private var focused: Bool
    var placeholder: String

    var body: some View {
        @Bindable var ask = model.ask
        VStack(alignment: .leading, spacing: 10) {
            if let attachment = ask.attachment {
                AttachmentChip(attachment: attachment, height: 30) { ask.attachment = nil }
            }
            TextField(placeholder, text: $ask.input, axis: .vertical)
                .textFieldStyle(.plain)
                .font(D.ui(14)).foregroundStyle(D.t1)
                .lineLimit(1...8)
                .focused($focused)
                .onSubmit(send)
            HStack(spacing: 10) {
                Button(action: pickFile) { Image(systemName: "paperclip").font(.system(size: 13, weight: .medium)) }
                    .buttonStyle(.plain).foregroundStyle(D.t4)
                Spacer()
                LastRun()
                if Drawing.wantsPicture(ask.input, hasPicture: false) && ask.attachment?.picture == nil {
                    PictureSizePicker()
                }
                DraftAheadToggle()
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
        panel.allowedContentTypes = AskController.attachable
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { model.ask.attach(url, maxPixels: model.settings.imageMaxPixels) }
    }
}

/// Lets a pool's draft model guess ahead. It only means something for answers from a pool, so it hides
/// when the route is a frontier model.
private struct DraftAheadToggle: View {
    @Environment(AppModel.self) private var model
    @State private var hovered = false

    var body: some View {
        let on = model.settings.draftAhead
        if !(model.ask.forcedID?.hasPrefix("frontier:") ?? false) {
            Button { model.settings.draftAhead.toggle() } label: {
                HStack(spacing: 5) {
                    Circle().fill(on ? D.accent : D.btn).frame(width: 6, height: 6)
                    Text("Draft ahead").font(D.ui(12.5, .medium))
                }
                .foregroundStyle(on || hovered ? D.t2 : D.t4)
                .padding(.horizontal, 8).frame(height: 26)
                .background(Capsule().fill(hovered ? D.raised : .clear))
                .contentShape(Capsule())
            }
            .buttonStyle(.plain)
            .onHover { hovered = $0 }
            .help(on ? "A small model guesses the next words and the pool checks them in batches: faster when a pool spans Macs. Click to turn off."
                     : "Off: the pool writes one word at a time. Click to let a small model guess ahead.")
            .accessibilityLabel("Draft ahead")
            .accessibilityValue(on ? "on" : "off")
        }
    }
}

/// Where the last answer ran, how fast, and what it cost, beside the route picker. Hover for why.
private struct LastRun: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let ask = model.ask
        let last = model.chats.current?.turns.last?.receipt
        if let receipt = ask.isBusy ? (ask.receipt ?? last) : last {
            HStack(spacing: 5) {
                Image(systemName: receipt.symbol).font(.system(size: 10, weight: .medium))
                Text(receipt.summary).font(D.ui(12)).lineLimit(1)
            }
            .foregroundStyle(D.t5)
            .help(receipt.why)
        }
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
