import AppKit
import SwiftUI

/// A borderless panel that can take keyboard focus without bringing the whole app forward,
/// like Spotlight. It grows downward from a fixed top edge as the answer comes in.
final class AskPanel: NSPanel {
    var anchorTop: CGFloat?

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

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
        // SwiftUI doesn't size this window. The view reports its height and fit(height:) resizes the panel
        // outside the layout pass: letting the hosting view resize the window from inside layout, while the
        // answer streamed in and the Macs' rings animated, recursed until the stack ran out.
        let host = NSHostingController(rootView: AskPanelView().environment(AppModel.shared))
        host.sizingOptions = []
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

    /// Grows or shrinks the panel to its content, keeping the top edge fixed. Called from the view's
    /// geometry, so the resize is deferred to the next turn of the run loop.
    func fit(height: CGFloat) {
        let height = (height * 2).rounded() / 2
        guard let panel, abs(panel.frame.height - height) > 0.5 else { return }
        DispatchQueue.main.async { [weak panel] in
            guard let panel, abs(panel.frame.height - height) > 0.5 else { return }
            var frame = panel.frame
            let top = panel.anchorTop ?? frame.maxY
            frame.origin.y = top - height
            frame.size.height = height
            panel.setFrame(frame, display: true, animate: false)
            panel.invalidateShadow()
        }
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
