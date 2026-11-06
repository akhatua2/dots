import AppKit
import SwiftUI

/// What's attached to the question: a small picture, or a document's name, with a way to take it off.
struct AttachmentChip: View {
    var attachment: AskController.Attachment
    var height: CGFloat
    var remove: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            if let data = attachment.picture, let image = NSImage(data: data) {
                Image(nsImage: image).resizable().scaledToFill()
                    .frame(width: height - 8, height: height - 8)
                    .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            } else {
                Image(systemName: "doc").font(.system(size: 11, weight: .medium))
            }
            Text(attachment.name).font(D.ui(12.5, .medium)).lineLimit(1).truncationMode(.middle)
                .frame(maxWidth: 170, alignment: .leading).fixedSize()
            Button(action: remove) { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)) }
                .buttonStyle(.plain).foregroundStyle(D.t4)
        }
        .foregroundStyle(D.t2)
        .padding(.leading, attachment.picture == nil ? 10 : 4).padding(.trailing, 10).frame(height: height)
        .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(D.raised))
        .fixedSize()
    }
}
