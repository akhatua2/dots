import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Draw, laid out like Ask: a drawing is a conversation. What you asked for on the right, the picture beneath it,
/// then the next thing you said and the picture it changed into, with the box fixed below. Your drawings are
/// listed in the sidebar.
struct DrawScreen: View {
    @Environment(AppModel.self) private var model
    @State private var dropTargeted = false

    private var hasDrawing: Bool { !model.draw.thread.isEmpty || model.draw.phase != .idle }

    var body: some View {
        VStack(spacing: 0) {
            if hasDrawing {
                DrawConversation()
                DrawComposer(placeholder: model.draw.start == nil ? "Describe a picture" : "Describe a change")
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
        .modifier(FileDrop(enabled: true, targeted: $dropTargeted) { url in
            model.draw.attach(url, maxPixels: model.settings.imageMaxPixels)
        })
    }

    /// Before the first picture: a line to start from, and the box.
    private var welcome: some View {
        VStack(spacing: 22) {
            Spacer()
            Text("What should the pool draw?").font(D.display(30)).foregroundStyle(D.t1)
            DrawComposer(placeholder: model.draw.start == nil ? "Describe a picture. Drop a photo to change it." : "Describe a change")
                .frame(maxWidth: Layout.column)
            Text("A Mac in your pools draws it. Your photos stay in your pools, and your pictures on this Mac.")
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

/// The open drawing: every picture and what made it, then the one being drawn now.
private struct DrawConversation: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let draw = model.draw
        LiveScroll {
            VStack(alignment: .leading, spacing: 26) {
                ForEach(draw.thread) { picture in
                    QuestionBubble(text: picture.prompt, attachment: picture.photo)
                    VStack(alignment: .leading, spacing: 8) {
                        PictureAnswer(image: draw.image(picture))
                        DrawActions(picture: picture)
                    }
                }
                if draw.isDrawing || draw.phase != .idle {
                    QuestionBubble(text: draw.asked, attachment: draw.askedPhoto)
                    if let progress = draw.progress {
                        PictureAnswer(image: progress.image, progress: progress)
                    }
                    if case .failed(let why) = draw.phase {
                        Text(why).font(D.ui(13)).foregroundStyle(D.t3)
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

/// Under a picture: change this one (rather than the latest), copy it, save it, and where it was drawn.
private struct DrawActions: View {
    @Environment(AppModel.self) private var model
    var picture: DrawnPicture
    @State private var copied = false

    var body: some View {
        HStack(spacing: 2) {
            IconButton(symbol: "wand.and.stars", help: "Change this picture") {
                model.draw.change(picture, maxPixels: model.settings.imageMaxPixels)
            }
            if picture.photo == nil {
                IconButton(symbol: "arrow.clockwise", help: "Draw it again") { model.draw.drawAgain(picture, model: model) }
            }
            IconButton(symbol: copied ? "checkmark" : "doc.on.doc", help: copied ? "Copied" : "Copy", action: copy)
            IconButton(symbol: "square.and.arrow.down", help: "Save the picture", action: save)
            if let receipt = picture.receipt {
                let drawnFrom = picture.description.map { "\nDrawn from: \($0)" } ?? ""
                IconButton(symbol: receipt.symbol, help: receipt.sentence + drawnFrom) {}
            }
        }
        .padding(.leading, -4)
    }

    private func copy() {
        guard let png = model.draw.image(picture), let image = NSImage(data: png) else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.writeObjects([image])
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
    }

    private func save() {
        guard let png = model.draw.image(picture) else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.png]
        panel.nameFieldStringValue = String(picture.prompt.prefix(40)).replacingOccurrences(of: "/", with: "-") + ".png"
        if panel.runModal() == .OK, let url = panel.url { try? png.write(to: url) }
    }
}

/// The box: what the next picture changes, if anything, the words, and send.
private struct DrawComposer: View {
    @Environment(AppModel.self) private var model
    @FocusState private var focused: Bool
    var placeholder: String

    var body: some View {
        @Bindable var draw = model.draw
        VStack(alignment: .leading, spacing: 10) {
            if let start = draw.start {
                HStack(spacing: 10) {
                    StartChip(start: start) { draw.start = nil }
                    if start.drawn != nil {
                        Button("Draw something new") { draw.start = nil }
                            .buttonStyle(.plain).font(D.ui(12.5, .medium)).foregroundStyle(D.t4)
                    }
                }
            }
            TextField(placeholder, text: $draw.prompt, axis: .vertical)
                .textFieldStyle(.plain)
                .font(D.ui(14)).foregroundStyle(D.t1)
                .lineLimit(1...8)
                .focused($focused)
                .onSubmit(send)
            HStack(spacing: 10) {
                Button(action: pickPhoto) { Image(systemName: "photo.badge.plus").font(.system(size: 13, weight: .medium)) }
                    .buttonStyle(.plain).foregroundStyle(D.t4).help("Change a photo of yours")
                Spacer()
                if draw.start == nil {
                    PictureSizePicker()
                }
                SendButton(enabled: canSend, busy: draw.isDrawing, action: send)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(D.card))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(Color.white.opacity(focused ? 0.16 : 0.08), lineWidth: 1))
        .onAppear { focused = true }
    }

    private var canSend: Bool {
        !model.draw.prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !model.draw.isDrawing
    }

    private func send() {
        guard canSend else { return }
        model.draw.draw(model: model)
    }

    private func pickPhoto() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { model.draw.attach(url, maxPixels: model.settings.imageMaxPixels) }
    }
}

/// What the next words change: a small picture and what it is, with a way to draw something new instead.
private struct StartChip: View {
    var start: DrawController.Start
    var remove: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            if let image = NSImage(data: start.sent) {
                Image(nsImage: image).resizable().scaledToFill().frame(width: 22, height: 22)
                    .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            }
            Text(start.drawn == nil ? "Changing \(start.name)" : "Changing the picture above")
                .font(D.ui(12.5, .medium)).lineLimit(1).truncationMode(.middle)
            Button(action: remove) { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)) }
                .buttonStyle(.plain).foregroundStyle(D.t4).help("Draw something new instead")
        }
        .foregroundStyle(D.t2)
        .padding(.leading, 4).padding(.trailing, 10).frame(height: 30)
        .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(D.raised))
        .fixedSize()
    }
}
