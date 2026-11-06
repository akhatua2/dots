import SwiftUI

@main
struct DotsApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel.shared

    var body: some Scene {
        Window("dots", id: "main") {
            MainWindow()
                .environment(model)
                .frame(minWidth: 1_040, minHeight: 680)
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentMinSize)
        .defaultSize(width: 1_180, height: 860)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandMenu("Pools") {
                Button("Explore") { model.open(.explore) }.keyboardShortcut("1")
                Button("API") { model.open(.api) }.keyboardShortcut("2")
                Button("Profile") { model.open(.profile) }.keyboardShortcut("3")
                Divider()
                Button("Start a Pool…") { model.sheet = .startPool }.keyboardShortcut("n")
                Button("Ask…") { AskPanelController.shared.show() }.keyboardShortcut("k")
            }
        }

        MenuBarExtra {
            MenuBarPanel()
                .environment(model)
        } label: {
            MenuBarLabel(status: model.lendingStatus)
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .environment(model)
        }
    }
}

/// The menu bar dot: a ring until your Mac lends, orange while it lends, filled while it works.
struct MenuBarLabel: View {
    var status: LendingStatus
    var body: some View {
        Image(nsImage: MenuBarIcons.image(for: status))
            .accessibilityLabel("dots, \(status.headline)")
    }
}

enum MenuBarIcons {
    static func image(for status: LendingStatus) -> NSImage {
        switch status {
        case .lending(_, working: true): filled
        case .lending(_, working: false): ring
        default: idle
        }
    }

    private static func draw(template: Bool, _ body: @escaping (NSRect) -> Void) -> NSImage {
        let image = NSImage(size: NSSize(width: 18, height: 18), flipped: false) { rect in body(rect); return true }
        image.isTemplate = template
        return image
    }

    /// Orange and filled: your Mac is working on someone's question.
    static let filled = draw(template: false) { rect in
        D.nsAccent.setFill()
        NSBezierPath(ovalIn: rect.insetBy(dx: 4, dy: 4)).fill()
    }

    /// Orange ring: lending, waiting for work.
    static let ring = draw(template: false) { rect in
        D.nsAccent.setStroke()
        let path = NSBezierPath(ovalIn: rect.insetBy(dx: 4.75, dy: 4.75))
        path.lineWidth = 1.5
        path.stroke()
        D.nsAccent.setFill()
        NSBezierPath(ovalIn: rect.insetBy(dx: 7.5, dy: 7.5)).fill()
    }

    /// A plain ring in the menu bar's own color: not lending.
    static let idle = draw(template: true) { rect in
        NSColor.black.setStroke()
        let path = NSBezierPath(ovalIn: rect.insetBy(dx: 4.75, dy: 4.75))
        path.lineWidth = 1.5
        path.stroke()
    }
}
