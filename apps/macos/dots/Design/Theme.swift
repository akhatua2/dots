import AppKit
import SwiftUI

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: opacity)
    }
}

extension NSColor {
    convenience init(hex: UInt32, alpha: CGFloat = 1) {
        self.init(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
                  green: CGFloat((hex >> 8) & 0xFF) / 255,
                  blue: CGFloat(hex & 0xFF) / 255,
                  alpha: alpha)
    }
}

/// The dots design tokens: neutral graphite, and one electric orange that only ever marks
/// you, what is live, and the one main action on screen.
enum D {
    // Surfaces, darkest to lightest
    static let bg0 = Color(hex: 0x0E0E0E)
    static let bg1 = Color(hex: 0x141414)       // sidebar
    static let bg2 = Color(hex: 0x191919)       // content
    static let deep = Color(hex: 0x161616)      // receipts and wells
    static let card = Color(hex: 0x1F1F1F)
    static let raised = Color(hex: 0x262626)
    static let sel = Color(hex: 0x2A2A2A)
    static let btn = Color(hex: 0x303030)
    static let line = Color.white.opacity(0.08)
    static let edge = Color.white.opacity(0.05)

    // Type, brightest to faintest
    static let t1 = Color(hex: 0xF4F4F4)
    static let t2 = Color(hex: 0xD6D6D6)
    static let t3 = Color(hex: 0xA6A6A6)
    static let t4 = Color(hex: 0x7D7D7D)
    static let t5 = Color(hex: 0x5E5E5E)

    // The one accent
    static let accent = Color(hex: 0xFF5A1F)
    static let accentText = Color(hex: 0xFF8A5C)
    static let accentTint = Color(hex: 0xFF5A1F, opacity: 0.16)
    static let onAccent = Color(hex: 0x0E0E0E)

    // The dithered swirl, always neutral
    static let swirl = Color(hex: 0x8C8C8C)
    static let emblem = Color(hex: 0xBDBDBD)

    static let nsAccent = NSColor(hex: 0xFF5A1F)
    static let nsBg0 = NSColor(hex: 0x0E0E0E)
    static let nsCard = NSColor(hex: 0x1F1F1F)

    // Fonts: the Mac's own SF Pro for the interface, Helvetica Neue Bold for the big moments
    static func ui(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font { .system(size: size, weight: weight) }
    static func mono(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font { .system(size: size, weight: weight, design: .monospaced) }
    static func display(_ size: CGFloat) -> Font { .custom("HelveticaNeue-Bold", size: size) }
}

extension Int {
    /// 1.counted("Mac") -> "1 Mac", 2406.counted("Mac") -> "2,406 Macs"
    func counted(_ noun: String, plural: String? = nil) -> String {
        "\(grouped) \(self == 1 ? noun : plural ?? noun + "s")"
    }

    /// 12406 -> "12,406"
    var grouped: String { formatted(.number.grouping(.automatic)) }
    /// 2400 -> "2.4k"
    var compact: String {
        if self >= 1000 {
            let k = Double(self) / 1000
            return (k >= 10 ? String(format: "%.0fk", k) : String(format: "%.1fk", k)).replacingOccurrences(of: ".0k", with: "k")
        }
        return "\(self)"
    }
}
