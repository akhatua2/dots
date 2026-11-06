import AppKit
import Carbon.HIToolbox
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var hotKey: GlobalHotKey?
    private var mockCloud: MockCloud?

    func applicationWillFinishLaunching(_ notification: Notification) {
        NSApp.appearance = NSAppearance(named: .darkAqua)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count {
            NSApp.setActivationPolicy(.prohibited)
            DispatchQueue.main.async { NSApp.windows.forEach { $0.orderOut(nil) } }
            let dir = URL(fileURLWithPath: args[i + 1])
            Task {
                await Snapshots.render(to: dir)
                exit(0)
            }
            return
        }
        // `dots --mock-cloud 8790` runs only a mock cloud, fresh and never saved, to try API.md with curl
        if let i = args.firstIndex(of: "--mock-cloud") {
            NSApp.setActivationPolicy(.prohibited)
            DispatchQueue.main.async { NSApp.windows.forEach { $0.orderOut(nil) } }
            let port = (i + 1 < args.count ? UInt16(args[i + 1]) : nil) ?? MockCloud.defaultPort
            let cloud = MockCloud(port: port, fresh: true, persists: false)
            do { try cloud.start() } catch { print("The mock cloud couldn't start: \(error)"); exit(1) }
            mockCloud = cloud
            print("A mock cloud on http://127.0.0.1:\(port), with the token \(AppModel.mockToken). Nothing is saved.")
            return
        }
        if args.contains("--selftest") {
            NSApp.setActivationPolicy(.prohibited)
            DispatchQueue.main.async { NSApp.windows.forEach { $0.orderOut(nil) } }
            Task { exit(await SelfTest.run() ? 0 : 1) }
            return
        }

        AppModel.shared.start()
        // `dots --ask "question"` opens the panel and asks it, for reproducing the ask flow without clicking
        if let i = args.firstIndex(of: "--ask"), i + 1 < args.count {
            let question = args[i + 1]
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                MainActor.assumeIsolated {
                    AskPanelController.shared.show()
                    AppModel.shared.ask.input = question
                    AppModel.shared.ask.submit(model: AppModel.shared)
                }
            }
        }
        // ⌥ Space opens the ask panel over whatever you're doing
        hotKey = GlobalHotKey(keyCode: UInt32(kVK_Space), modifiers: UInt32(optionKey)) {
            AskPanelController.shared.toggle()
        }

        // The app lives in the menu bar: it shows in the Dock only while its window is open
        NSApp.setActivationPolicy(.regular)
        NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: nil, queue: .main) { note in
            guard let closing = note.object as? NSWindow, closing.styleMask.contains(.titled) else { return }
            MainActor.assumeIsolated {
                let others = NSApp.windows.filter { $0 !== closing && $0.isVisible && $0.styleMask.contains(.titled) }
                if others.isEmpty { NSApp.setActivationPolicy(.accessory) }
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    /// dots://invite/CODE, from a link a friend sent: shows the invite before you join
    func application(_ application: NSApplication, open urls: [URL]) {
        guard let url = urls.first(where: { $0.scheme?.lowercased() == "dots" }) else { return }
        AppModel.shared.openMainWindow()
        AppModel.shared.openInviteLink(url.absoluteString)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { AppModel.shared.openMainWindow() }
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        AppModel.shared.backend.saveNow()
    }
}

/// A system-wide shortcut, registered with Carbon so it works without Accessibility access.
@MainActor
final class GlobalHotKey {
    private static var actions: [UInt32: () -> Void] = [:]
    private static var handlerInstalled = false
    private var ref: EventHotKeyRef?

    init?(keyCode: UInt32, modifiers: UInt32, id: UInt32 = 1, action: @escaping () -> Void) {
        Self.installHandler()
        Self.actions[id] = action
        var hotKeyRef: EventHotKeyRef?
        let hotKeyID = EventHotKeyID(signature: OSType(0x646F_7473), id: id)   // 'dots'
        guard RegisterEventHotKey(keyCode, modifiers, hotKeyID, GetApplicationEventTarget(), 0, &hotKeyRef) == noErr else { return nil }
        ref = hotKeyRef
    }

    private static func installHandler() {
        guard !handlerInstalled else { return }
        handlerInstalled = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
            var hotKeyID = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID), nil,
                              MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
            let id = hotKeyID.id
            DispatchQueue.main.async { MainActor.assumeIsolated { GlobalHotKey.actions[id]?() } }
            return noErr
        }, 1, &spec, nil, nil)
    }
}
