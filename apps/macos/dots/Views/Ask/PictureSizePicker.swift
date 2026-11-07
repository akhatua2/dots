import SwiftUI

/// Shown when the question asks for a picture: how big to draw it. Bigger is sharper, and slower to draw.
struct PictureSizePicker: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let side = model.settings.pictureSide
        Menu {
            Section("Picture size") {
                ForEach(Drawing.sides, id: \.self) { s in
                    Button {
                        model.settings.pictureSide = s
                    } label: {
                        if s == side { Label("\(s) × \(s)", systemImage: "checkmark") } else { Text("\(s) × \(s)") }
                    }
                }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "photo").font(.system(size: 10.5, weight: .medium))
                Text("\(side) × \(side)").font(D.ui(12.5, .medium).monospacedDigit()).lineLimit(1)
                Image(systemName: "chevron.down").font(.system(size: 7.5, weight: .bold))
            }
            .foregroundStyle(D.t3)
            .frame(height: 26)
            .contentShape(Rectangle())
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .fixedSize()
        .help("How big to draw the picture. Bigger is sharper, and slower to draw.")
    }
}
