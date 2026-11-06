import SwiftUI

// MARK: - Cards and stages

extension D {
    static let panelEdge = Color.white.opacity(0.08)
    static let title = Color(hex: 0xEBEBEB)
    static let label = Color(hex: 0x7A7A7A)
    static let rule = Color.white.opacity(0.1)
    static let red = Color(hex: 0xFF453A)
}

extension View {
    /// A card: the one flat platter every group sits on, with a hairline edge. Cards in a row share their height.
    func panel(padding: CGFloat = 20) -> some View {
        self.padding(padding)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(D.card))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(D.panelEdge, lineWidth: 0.5))
    }

    /// A stage: the darker card art sits on, so the art reads as a window.
    func stage(padding: EdgeInsets = EdgeInsets(top: 20, leading: 20, bottom: 12, trailing: 20)) -> some View {
        self.padding(padding)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(D.bg0))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(D.panelEdge, lineWidth: 0.5))
    }
}

/// A card's title row: the title, and a quiet note or control on the right.
struct CardHeader<Accessory: View>: View {
    var title: String
    @ViewBuilder var accessory: Accessory

    var body: some View {
        HStack(alignment: .center) {
            Text(title).font(D.ui(13, .semibold)).foregroundStyle(D.title)
            Spacer(minLength: 8)
            accessory
        }
        .frame(height: 20)
    }
}

extension CardHeader where Accessory == AnyView {
    init(_ title: String, note: String? = nil) {
        self.title = title
        self.accessory = AnyView(Group { if let note { Text(note).font(D.ui(12).monospacedDigit()).foregroundStyle(D.label) } })
    }
}

/// Numbers side by side, split by hairlines.
struct StatRow: View {
    struct Stat: Identifiable {
        var id: String { label }
        var value: String
        var label: String
        var accent = false
    }
    var stats: [Stat]
    var size: CGFloat = 20

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(stats.enumerated()), id: \.element.id) { i, s in
                VStack(alignment: .leading, spacing: 3) {
                    Text(s.value).font(D.ui(size, .semibold).monospacedDigit()).foregroundStyle(s.accent ? D.accentText : D.t1)
                        .contentTransition(.numericText()).lineLimit(1)
                    Text(s.label).font(D.ui(11.5)).foregroundStyle(D.label).lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.leading, i == 0 ? 0 : 16)
                .overlay(alignment: .leading) { if i > 0 { Rectangle().fill(D.rule).frame(width: 0.5) } }
            }
        }
    }
}

/// A hairline across a card.
struct Rule: View {
    var body: some View { Rectangle().fill(D.rule).frame(height: 0.5) }
}

/// Small capitals on a chip: NEW, HOST, UNLOCKER, BOOST.
struct CapsTag: View {
    var text: String
    var accent = false
    var plain = false

    var body: some View {
        Text(text.uppercased())
            .font(D.ui(10, .bold)).tracking(0.6)
            .foregroundStyle(accent ? D.accentText : Color(hex: 0xBDBDBD))
            .padding(.horizontal, plain ? 0 : 6).frame(height: 17)
            .background { if !plain { RoundedRectangle(cornerRadius: 4, style: .continuous).fill(accent ? D.accent.opacity(0.14) : Color(hex: 0x2C2C2C)) } }
    }
}

/// A calendar page: the weekday on an orange strip, the day under it.
struct CalendarTile: View {
    var date: Date
    var width: CGFloat = 40

    var body: some View {
        VStack(spacing: 0) {
            Text(date.formatted(.dateTime.weekday(.abbreviated)).uppercased())
                .font(D.ui(width * 0.21, .heavy)).tracking(0.8).foregroundStyle(D.onAccent)
                .frame(maxWidth: .infinity).padding(.vertical, width * 0.07)
                .background(D.accent)
            Text(date.formatted(.dateTime.day()))
                .font(D.ui(width * 0.47, .semibold).monospacedDigit()).foregroundStyle(D.t1)
                .frame(maxHeight: .infinity)
        }
        .frame(width: width, height: width * 1.1)
        .background(Color(hex: 0x2A2A2A))
        .clipShape(RoundedRectangle(cornerRadius: width * 0.22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: width * 0.22, style: .continuous).strokeBorder(Color.white.opacity(0.12), lineWidth: 0.5))
        .accessibilityHidden(true)
    }
}

/// A round icon button with a tooltip, quiet until the pointer is on it.
struct IconButton: View {
    var symbol: String
    var help: String
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12, weight: .semibold))
                .foregroundStyle(hovering ? D.t1 : D.t4)
                .frame(width: 26, height: 26)
                .background(Circle().fill(hovering ? Color.white.opacity(0.06) : .clear))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help(help)
        .accessibilityLabel(help)
    }
}

/// A quiet text button that turns red under the pointer: for leaving.
struct LeaveButton: View {
    var title: String
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(D.ui(12, .medium))
            .foregroundStyle(hovering ? D.red : D.t4)
            .onHover { hovering = $0 }
    }
}

extension View {
    /// Helper text belongs on hover: a tooltip when there's something to say, nothing otherwise.
    @ViewBuilder func tip(_ text: String?) -> some View {
        if let text, !text.isEmpty { help(Text(text)) } else { self }
    }
}

// MARK: - Dots as pictures

/// A 7 by 7 dot-matrix glyph, for badges.
struct DotGlyph: View {
    var rows: [String]
    var size: CGFloat = 21

    var body: some View {
        Canvas { ctx, sz in
            let cell = sz.width / 7
            for (y, row) in rows.enumerated() {
                for (x, ch) in row.enumerated() where ch == "#" {
                    ctx.fill(Path(ellipseIn: CGRect(x: (CGFloat(x) + 0.1) * cell, y: (CGFloat(y) + 0.1) * cell, width: cell * 0.8, height: cell * 0.8)),
                             with: .foreground)
                }
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    static let sparkle = ["...#...", "...#...", "..###..", "#######", "..###..", "...#...", "...#..."]
    static let clock = ["..###..", ".#...#.", "#..#..#", "#..##.#", "#.....#", ".#...#.", "..###.."]
    static let padlock = ["..###..", ".#...#.", ".#.....", "#######", "###.###", "###.###", "#######"]
    static let sprout = [".##.##.", "###.###", ".#####.", "...#...", "...#...", "...#...", "..###.."]
    static let moon = ["..###..", ".###...", "###....", "###....", "###....", ".###...", "..###.."]
    static let plus = ["...#...", "...#...", "...#...", "#######", "...#...", "...#...", "...#..."]
    static let heart = [".##.##.", "#######", "#######", ".#####.", "..###..", "...#...", "......."]

    static func badge(_ id: String) -> [String] {
        if id.hasPrefix("hours") { return clock }
        if id.hasPrefix("unlock") { return padlock }
        if id.hasPrefix("backer") { return sprout }
        if id.hasPrefix("starter") { return plus }
        if id.hasPrefix("nights") { return moon }
        return sparkle
    }
}

/// A bar of dots on a grid, for counts over time: whole dots only, so nothing is cut in half.
struct DotColumn: View {
    var rows: Int
    var columns: Int = 9
    var pitch: CGFloat = 4
    var color: Color

    var body: some View {
        Canvas { ctx, sz in
            var path = Path()
            for r in 0..<rows {
                for k in 0..<columns {
                    path.addEllipse(in: CGRect(x: CGFloat(k) * pitch + pitch * 0.2, y: sz.height - CGFloat(r + 1) * pitch + pitch * 0.2,
                                               width: pitch * 0.6, height: pitch * 0.6))
                }
            }
            ctx.fill(path, with: .color(color))
        }
        .frame(width: CGFloat(columns) * pitch, height: CGFloat(max(rows, 0)) * pitch)
    }
}

/// A run of dots along a line, lit between two points: a timeline track.
struct DotTrack: View {
    var slots: Int
    var lit: Set<Int>
    var color: Color
    var pitch: CGFloat = 11
    var blinkLast = false

    var body: some View {
        Canvas { ctx, sz in
            var faint = Path(), on = Path()
            for i in 0..<slots {
                let cx = (CGFloat(i) + 0.5) * pitch, cy = sz.height / 2
                if lit.contains(i) {
                    on.addEllipse(in: CGRect(x: cx - 2.6, y: cy - 2.6, width: 5.2, height: 5.2))
                } else {
                    faint.addEllipse(in: CGRect(x: cx - 1.6, y: cy - 1.6, width: 3.2, height: 3.2))
                }
            }
            ctx.fill(faint, with: .color(Color(hex: 0x2A2A2A)))
            ctx.fill(on, with: .color(color))
        }
        .frame(width: CGFloat(slots) * pitch, height: 11)
        .accessibilityHidden(true)
    }
}

/// The model's layers as ticks, each Mac's share in its own tone, yours orange, and a token stepping through
/// them one layer at a time while the pool runs.
struct LayerTicks: View {
    /// Each share of the layers, in order, with its tone
    var shares: [(layers: Int, rgb: UInt32)]
    var live: Bool
    @Environment(\.artClock) private var artClock

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: !live || artClock != nil)) { context in
            let total = max(1, shares.reduce(0) { $0 + $1.layers })
            let at = live && artClock == nil ? Int((context.date.timeIntervalSinceReferenceDate * 30).truncatingRemainder(dividingBy: Double(total + 20))) : -1
            Canvas { ctx, sz in
                let pitch = sz.width / CGFloat(total)
                let tick = max(2, (pitch * 0.6).rounded())
                var i = 0
                for share in shares {
                    var path = Path()
                    for _ in 0..<share.layers {
                        if i != at { path.addRect(CGRect(x: (CGFloat(i) * pitch).rounded(), y: 0, width: tick, height: sz.height)) }
                        i += 1
                    }
                    ctx.fill(path, with: .color(Color(hex: share.rgb)))
                }
                if at >= 0, at < total {
                    ctx.fill(Path(CGRect(x: (CGFloat(at) * pitch).rounded(), y: 0, width: tick, height: sz.height)), with: .color(D.t1))
                }
            }
        }
        .frame(height: 6)
        .accessibilityHidden(true)
    }
}

/// Faces in a row, overlapping, each ringed in the card's colour.
struct AvatarStack: View {
    var faces: [(text: String, isYou: Bool, dim: Bool)]
    var size: CGFloat = 24
    var ring: Color = D.card

    var body: some View {
        HStack(spacing: -6) {
            ForEach(Array(faces.enumerated()), id: \.offset) { i, f in
                Text(f.text)
                    .font(.system(size: f.text.count > 1 ? size * 0.33 : size * 0.42, weight: .bold))
                    .foregroundStyle(f.isYou ? D.onAccent : f.dim ? Color(hex: 0xBDBDBD) : Color(hex: 0xE6E6E6))
                    .frame(width: size, height: size)
                    .background(Circle().fill(f.isYou ? D.accent : f.dim ? Color(hex: 0x2A2A2A) : Color(hex: 0x333333)))
                    .overlay(Circle().strokeBorder(ring, lineWidth: 2).padding(-2))
                    .zIndex(Double(faces.count - i))
            }
        }
        .accessibilityHidden(true)
    }
}

/// The switch: orange when on for anything about lending, quiet otherwise.
struct OrangeSwitch: View {
    @Binding var isOn: Bool
    var label: String
    var width: CGFloat = 34

    var body: some View {
        ZStack(alignment: isOn ? .trailing : .leading) {
            Capsule().fill(isOn ? D.accent : Color.white.opacity(0.18))
            Circle().fill(Color.white).shadow(color: .black.opacity(0.35), radius: 1, y: 1).padding(2)
        }
        .frame(width: width, height: width * 0.59)
        .contentShape(Capsule())
        .onTapGesture { withAnimation(.spring(duration: 0.25)) { isOn.toggle() } }
        .accessibilityElement()
        .accessibilityLabel(label)
        .accessibilityValue(isOn ? "On" : "Off")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { isOn.toggle() }
    }
}

/// A slider on a thin track, filled orange to the value, with an optional mark on the track.
struct ThinSlider: View {
    @Binding var value: Int
    var range: ClosedRange<Int>
    var mark: Int? = nil
    var enabled = true
    var label: String

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let span = CGFloat(max(1, range.upperBound - range.lowerBound))
            let x = CGFloat(value - range.lowerBound) / span * w
            ZStack(alignment: .leading) {
                Capsule().fill(Color.white.opacity(0.14)).frame(height: 4)
                Capsule().fill(D.accent).frame(width: max(4, x), height: 4)
                if let mark {
                    Capsule().fill(D.accentText).frame(width: 2, height: 12)
                        .offset(x: CGFloat(mark - range.lowerBound) / span * w - 1)
                }
                Circle().fill(Color.white)
                    .frame(width: 18, height: 18)
                    .shadow(color: .black.opacity(0.45), radius: 1.5, y: 1)
                    .offset(x: x - 9)
            }
            .frame(height: 24)
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onChanged { g in
                guard enabled else { return }
                let v = range.lowerBound + Int((g.location.x / w * span).rounded())
                value = min(range.upperBound, max(range.lowerBound, v))
            })
        }
        .frame(height: 24)
        .opacity(enabled ? 1 : 0.35)
        .accessibilityElement()
        .accessibilityLabel(label)
        .accessibilityValue("\(value) gigabytes")
        .accessibilityAdjustableAction { dir in
            guard enabled else { return }
            switch dir {
            case .increment: value = min(range.upperBound, value + 1)
            case .decrement: value = max(range.lowerBound, value - 1)
            @unknown default: break
            }
        }
    }
}
