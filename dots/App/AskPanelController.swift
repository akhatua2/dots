import AppKit
import SwiftUI

/// A borderless panel that can take keyboard focus without bringing the whole app forward,
/// like Spotlight. It grows downward from a fixed top edge as the answer comes in.
final class AskPanel: NSPanel {
    var anchorTop: CGFloat?

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    override func setFrame(_ frameRect: NSRect, display flag: Bool) {
        var r = frameRect
        if let top = anchorTop { r.origin.y = top - r.height }
        super.setFrame(r, display: flag)
    }

    override func cancelOperation(_ sender: Any?) {
        AskPanelController.shared.hide()
    }
}

@MainActor
final class AskPanelController {
    static let shared = AskPanelController()
    private var panel: AskPanel?

    var isVisible: Bool { panel?.isVisible == true }

    func toggle() { isVisible ? hide() : show() }

    func show() {
        let panel = self.panel ?? makePanel()
        place(panel)
        panel.makeKeyAndOrderFront(nil)
        AppModel.shared.ask.focusToken += 1
    }

    func hide() {
        panel?.orderOut(nil)
    }

    private func makePanel() -> AskPanel {
        let panel = AskPanel(contentRect: NSRect(x: 0, y: 0, width: 720, height: 72),
                             styleMask: [.nonactivatingPanel, .borderless, .fullSizeContentView],
                             backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = true
        panel.animationBehavior = .utilityWindow
        let host = NSHostingController(rootView: AskPanelView().environment(AppModel.shared))
        host.sizingOptions = [.preferredContentSize]
        host.view.wantsLayer = true
        host.view.layer?.backgroundColor = .clear
        panel.contentViewController = host
        // Clicking anywhere else puts it away
        NotificationCenter.default.addObserver(forName: NSWindow.didResignKeyNotification, object: panel, queue: .main) { _ in
            MainActor.assumeIsolated { AskPanelController.shared.hide() }
        }
        self.panel = panel
        return panel
    }

    /// Centered on the screen with the pointer, a quarter of the way down.
    private func place(_ panel: AskPanel) {
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) } ?? NSScreen.main
        guard let visible = screen?.visibleFrame else { return }
        let width: CGFloat = 720
        let top = visible.maxY - visible.height * 0.22
        panel.anchorTop = top
        let height = panel.frame.height
        panel.setFrame(NSRect(x: visible.midX - width / 2, y: top - height, width: width, height: height), display: true)
    }
}
