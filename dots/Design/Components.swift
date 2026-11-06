import SwiftUI

// MARK: - Surfaces

struct CardBackground: ViewModifier {
    var radius: CGFloat = 16
    var fill: Color = D.card
    var stroke: Color = Color.white.opacity(0.08)

    func body(content: Content) -> some View {
        content
            .background(RoundedRectangle(cornerRadius: radius, style: .continuous).fill(fill))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).strokeBorder(stroke, lineWidth: 0.5))
    }
}

extension View {
    func card(radius: CGFloat = 16, fill: Color = D.card, stroke: Color = Color.white.opacity(0.08)) -> some View {
        modifier(CardBackground(radius: radius, fill: fill, stroke: stroke))
    }
}

struct Hairline: View {
    var body: some View { Rectangle().fill(D.line).frame(height: 1) }
}

// MARK: - Type helpers

struct CapLabel: View {
    var text: String
    var color: Color = D.t4
    init(_ text: String, color: Color = D.t4) { self.text = text; self.color = color }
    var body: some View {
        Text(text.uppercased())
            .font(D.ui(11, .bold))
            .tracking(0.6)
            .foregroundStyle(color)
    }
}

/// A small icon and a line of text, the way stats sit on cards and receipts.
struct IconLine: View {
    var symbol: String
    var text: String
    var color: Color = D.t3
    var iconColor: Color? = nil
    var size: CGFloat = 12.5
    var weight: Font.Weight = .regular

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: symbol)
                .font(.system(size: size - 1, weight: .medium))
                .foregroundStyle(iconColor ?? color)
                .frame(width: size + 2)
            Text(text).font(D.ui(size, weight)).foregroundStyle(color).lineLimit(1)
        }
    }
}

/// The live dot: orange means live, grey means not.
struct LiveDot: View {
    var live: Bool
    var size: CGFloat = 8
    var body: some View {
        Circle()
            .fill(live ? D.accent : Color.clear)
            .overlay(Circle().strokeBorder(live ? Color.clear : D.t4, lineWidth: 1.5))
            .frame(width: size, height: size)
    }
}

// MARK: - Buttons

enum DButtonKind { case primary, grey, quiet, outline }

struct DButtonStyle: ButtonStyle {
    var kind: DButtonKind = .grey
    var large = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(D.ui(large ? 13.5 : 12.5, .semibold))
            .foregroundStyle(foreground)
            .padding(.horizontal, large ? 16 : 12)
            .frame(height: large ? 34 : 28)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(background))
            .overlay {
                if kind == .outline {
                    RoundedRectangle(cornerRadius: 7, style: .continuous).strokeBorder(D.line, lineWidth: 1)
                }
            }
            .opacity(configuration.isPressed ? 0.78 : 1)
            .contentShape(Rectangle())
    }

    private var foreground: Color {
        switch kind {
        case .primary: D.onAccent
        case .grey, .outline: D.t1
        case .quiet: D.t3
        }
    }

    private var background: Color {
        switch kind {
        case .primary: D.accent
        case .grey: D.btn
        case .quiet, .outline: .clear
        }
    }
}

/// A grey button that turns orange while the pointer is on it: the one main action,
/// shown only for the thing you're about to do.
struct HoverAccentButton: View {
    var title: String
    var symbol: String? = nil
    var large = false
    var alwaysAccent = false
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let symbol { Image(systemName: symbol).font(.system(size: 11, weight: .bold)) }
                Text(title)
            }
        }
        .buttonStyle(DButtonStyle(kind: (hovering || alwaysAccent) ? .primary : .grey, large: large))
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.12), value: hovering)
    }
}

// MARK: - Controls

/// Switches: the master lending switch is orange; the rest are quiet, light when on.
struct DSwitch: View {
    @Binding var isOn: Bool
    var accent = false
    var label: String = ""

    var body: some View {
        ZStack(alignment: isOn ? .trailing : .leading) {
            Capsule().fill(isOn ? (accent ? D.accent : D.t2) : D.btn)
            Circle()
                .fill(isOn && !accent ? D.bg0 : Color.white)
                .padding(2)
        }
        .frame(width: accent ? 40 : 34, height: accent ? 24 : 20)
        .contentShape(Capsule())
        .onTapGesture { withAnimation(.spring(duration: 0.25)) { isOn.toggle() } }
        .accessibilityElement()
        .accessibilityLabel(label)
        .accessibilityValue(isOn ? "On" : "Off")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { isOn.toggle() }
    }
}

/// How much memory to lend. The right-hand stretch is the share that always stays with the Mac.
struct LendSlider: View {
    @Binding var value: Int
    var maxLend: Int
    var memory: Int
    var marker: (gb: Int, label: String)? = nil

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let px = w / CGFloat(max(memory, 1))
            ZStack(alignment: .leading) {
                Capsule().fill(D.raised).frame(height: 6)
                HatchStrip()
                    .frame(width: CGFloat(memory - maxLend) * px, height: 6)
                    .clipShape(Capsule())
                    .offset(x: CGFloat(maxLend) * px)
                Capsule().fill(D.t2).frame(width: max(6, CGFloat(value) * px), height: 6)
                if let marker, marker.gb <= memory {
                    Rectangle().fill(value >= marker.gb ? D.accent : D.t2)
                        .frame(width: 2, height: 16)
                        .offset(x: CGFloat(marker.gb) * px - 1)
                }
                Circle().fill(Color.white)
                    .frame(width: 16, height: 16)
                    .shadow(color: .black.opacity(0.5), radius: 2, y: 1)
                    .offset(x: CGFloat(value) * px - 8)
            }
            .frame(height: 20)
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onChanged { g in
                value = min(maxLend, max(0, Int((g.location.x / px).rounded())))
            })
        }
        .frame(height: 20)
        .accessibilityElement()
        .accessibilityLabel("Lend up to")
        .accessibilityValue("\(value) gigabytes")
        .accessibilityAdjustableAction { dir in
            switch dir {
            case .increment: value = min(maxLend, value + 1)
            case .decrement: value = max(0, value - 1)
            @unknown default: break
            }
        }
    }
}

struct HatchStrip: View {
    var body: some View {
        Canvas { ctx, sz in
            ctx.fill(Path(CGRect(origin: .zero, size: sz)), with: .color(D.raised))
            var p = Path()
            var x: CGFloat = -sz.height
            while x < sz.width + sz.height {
                p.move(to: CGPoint(x: x, y: sz.height))
                p.addLine(to: CGPoint(x: x + sz.height, y: 0))
                x += 6
            }
            ctx.stroke(p, with: .color(Color(hex: 0x3A3A3A)), lineWidth: 2.2)
        }
    }
}

/// A round avatar with an initial; yours is the orange one.
struct Avatar: View {
    var text: String
    var size: CGFloat = 24
    var isYou = false
    var dimmed = false

    var body: some View {
        Text(text)
            .font(.system(size: text.count > 1 ? size * 0.34 : size * 0.44, weight: .semibold))
            .foregroundStyle(isYou ? D.onAccent : D.t1)
            .frame(width: size, height: size)
            .background(Circle().fill(isYou ? D.accent : D.btn))
            .opacity(dimmed ? 0.45 : 1)
    }
}

/// Filter chips; the selected one is the light, filled one.
struct Chip: View {
    var title: String
    var selected = false
    var action: () -> Void = {}

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(D.ui(12.5, .medium))
                .foregroundStyle(selected ? D.bg0 : D.t2)
                .padding(.horizontal, 11)
                .frame(height: 26)
                .background(Capsule().fill(selected ? D.t1 : D.raised))
        }
        .buttonStyle(.plain)
    }
}

/// Little static tags on receipts: "Free for you", "Private".
struct Tag: View {
    var text: String
    var symbol: String? = nil
    var accent = false

    var body: some View {
        HStack(spacing: 5) {
            if let symbol { Image(systemName: symbol).font(.system(size: 10, weight: .semibold)) }
            Text(text).font(D.ui(12, .medium))
        }
        .foregroundStyle(accent ? D.accentText : D.t2)
        .padding(.horizontal, 10)
        .frame(height: 24)
        .background(Capsule().fill(accent ? D.accentTint : D.raised))
    }
}

/// A plain text field on a dark well.
struct Field: View {
    var placeholder: String
    @Binding var text: String
    var mono = false
    var secure = false
    @Environment(\.isSnapshot) private var isSnapshot

    var body: some View {
        Group {
            if isSnapshot {
                Text(text.isEmpty ? placeholder : (secure ? String(repeating: "•", count: 12) : text))
                    .foregroundStyle(text.isEmpty ? D.t5 : D.t1)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else if secure {
                SecureField(placeholder, text: $text)
            } else {
                TextField(placeholder, text: $text)
            }
        }
        .textFieldStyle(.plain)
        .font(mono ? D.mono(13) : D.ui(13.5))
        .foregroundStyle(D.t1)
        .padding(.horizontal, 12)
        .frame(height: 36)
        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(D.bg0))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Color.white.opacity(0.12), lineWidth: 1))
    }
}

extension EnvironmentValues {
    /// True while views are drawn into images for `--snapshot`, where live text fields don't draw.
    @Entry var isSnapshot = false
}
