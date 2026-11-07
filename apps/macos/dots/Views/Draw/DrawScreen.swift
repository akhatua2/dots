import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Draw: a picture from a few words, then changes to it, one after another. The picture is the screen; the box
/// below it says what to draw, or what to change; your recent pictures run along the bottom.
struct DrawScreen: View {
    @Environment(AppModel.self) private var model
    @State private var dropTargeted = false

    var body: some View {
        let draw = model.draw
        VStack(spacing: 18) {
            Spacer(minLength: 24)
            stage
            if case .failed(let why) = draw.phase {
                Text(why).font(D.ui(13)).foregroundStyle(D.t3)
            }
            Spacer(minLength: 12)
            DrawComposer()
                .frame(maxWidth: Layout.column)
            if !draw.pictures.isEmpty {
                RecentPictures().frame(maxWidth: Layout.column)
            }
        }
        .padding(.horizontal, 32).padding(.bottom, 24)
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

    /// The picture being drawn, or the one picked, or a first line to start from
    @ViewBuilder private var stage: some View {
        let draw = model.draw
        if let progress = draw.progress {
            PictureAnswer(image: progress.image, progress: progress, maxWidth: Layout.picture)
        } else if let shown = draw.shown {
            VStack(alignment: .leading, spacing: 8) {
                PictureAnswer(image: draw.image(shown), maxWidth: Layout.picture)
                PictureActions(picture: shown)
            }
            .frame(maxWidth: Layout.picture)
        } else {
            VStack(spacing: 10) {
                Text("What should the pool draw?").font(D.display(30)).foregroundStyle(D.t1)
                Text("Describe a picture, or drop a photo to change it. Photos stay in your pools.")
                    .font(D.ui(13)).foregroundStyle(D.t4)
            }
        }
    }
}

private enum Layout {
    static let column: CGFloat = 680
    static let picture: CGFloat = 520
}

/// Under a picture: the words that made it, and what you can do with it.
private struct PictureActions: View {
    @Environment(AppModel.self) private var model
    var picture: DrawnPicture
    @State private var copied = false

    var body: some View {
        HStack(spacing: 10) {
            Text(picture.prompt).font(D.ui(13)).foregroundStyle(D.t3).lineLimit(2)
            Spacer(minLength: 12)
            Button("Change it") { model.draw.change(picture, maxPixels: model.settings.imageMaxPixels) }
                .buttonStyle(DButtonStyle(kind: .grey))
            IconButton(symbol: copied ? "checkmark" : "doc.on.doc", help: copied ? "Copied" : "Copy", action: copy)
            IconButton(symbol: "square.and.arrow.down", help: "Save the picture", action: save)
            IconButton(symbol: "trash", help: "Delete the picture") { model.draw.delete(picture) }
        }
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

/// The box: what the next picture changes, if anything, the words, and Draw.
private struct DrawComposer: View {
    @Environment(AppModel.self) private var model
    @FocusState private var focused: Bool

    var body: some View {
        @Bindable var draw = model.draw
        VStack(alignment: .leading, spacing: 10) {
            if let start = draw.start {
                StartChip(start: start) { draw.start = nil }
            }
            TextField(draw.start == nil ? "Describe a picture" : "Describe a change", text: $draw.prompt, axis: .vertical)
                .textFieldStyle(.plain)
                .font(D.ui(14)).foregroundStyle(D.t1)
                .lineLimit(1...6)
                .focused($focused)
                .onSubmit(send)
            HStack(spacing: 10) {
                Button(action: pickPhoto) { Image(systemName: "photo.badge.plus").font(.system(size: 13, weight: .medium)) }
                    .buttonStyle(.plain).foregroundStyle(D.t4).help("Change a photo")
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

/// What the next picture changes: a small picture and its name, with a way to start fresh instead.
private struct StartChip: View {
    var start: DrawController.Start
    var remove: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            if let image = NSImage(data: start.sent) {
                Image(nsImage: image).resizable().scaledToFill().frame(width: 22, height: 22)
                    .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            }
            Text(start.drawn == nil ? "Changing \(start.name)" : "Changing this picture")
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

/// Your pictures, newest first, small along the bottom. The one shown large is outlined.
private struct RecentPictures: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                ForEach(model.draw.pictures) { picture in
                    Button { model.draw.show(picture) } label: {
                        Group {
                            if let png = model.draw.image(picture), let image = NSImage(data: png) {
                                Image(nsImage: image).resizable().scaledToFill()
                            } else {
                                D.raised
                            }
                        }
                        .frame(width: 52, height: 52)
                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .strokeBorder(model.draw.shownID == picture.id ? D.t2 : .clear, lineWidth: 1.5))
                    }
                    .buttonStyle(.plain)
                    .help(picture.prompt)
                }
            }
        }
        .scrollIndicators(.never)
    }
}
