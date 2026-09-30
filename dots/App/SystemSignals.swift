import AppKit
import CoreGraphics
import IOKit
import IOKit.ps
import Observation

/// Facts about the Mac dots is running on.
enum ThisMac {
    static var memoryGB: Int {
        Int((Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824).rounded())
    }

    /// "MacBook Pro (14-inch, M3, Nov 2023)" on Apple Silicon, from the platform expert.
    static var productName: String {
        let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOPlatformExpertDevice"))
        defer { IOObjectRelease(service) }
        if service != 0,
           let data = IORegistryEntryCreateCFProperty(service, "product-name" as CFString, kCFAllocatorDefault, 0)?
               .takeRetainedValue() as? Data,
           let name = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .controlCharacters.union(.whitespaces)),
           !name.isEmpty {
            return name
        }
        return Host.current().localizedName ?? "Mac"
    }

    /// "MacBook Pro", "Mac Studio", ...
    static var kind: String {
        let name = productName
        for k in ["MacBook Pro", "MacBook Air", "Mac Studio", "Mac mini", "Mac Pro", "iMac"] where name.contains(k) {
            return k
        }
        return "Mac"
    }

    static var isAppleSilicon: Bool {
        var value: Int32 = 0
        var size = MemoryLayout<Int32>.size
        return sysctlbyname("hw.optional.arm64", &value, &size, nil, 0) == 0 && value == 1
    }

    static var ownerFirstName: String {
        NSFullUserName().split(separator: " ").first.map { $0.prefix(1).uppercased() + $0.dropFirst() } ?? "You"
    }
}

/// Whether the Mac is on power and whether someone is using it, polled every few seconds.
@MainActor
@Observable
final class SignalMonitor {
    var onPower = true
    var idleSeconds: Double = 0
    /// "Using it" means any keyboard, mouse or trackpad input in the last two minutes.
    var inUse: Bool { idleSeconds < 120 }

    private var timer: Timer?

    func start() {
        poll()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.poll() }
        }
    }

    func poll() {
        let power = Self.readOnPower()
        if power != onPower { onPower = power }
        idleSeconds = Self.readIdleSeconds()
    }

    static func readOnPower() -> Bool {
        guard let info = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
              let type = IOPSGetProvidingPowerSourceType(info)?.takeUnretainedValue() as String? else { return true }
        return type == kIOPSACPowerValue
    }

    static func readIdleSeconds() -> Double {
        // kCGAnyInputEventType: the time since any input at all
        guard let anyInput = CGEventType(rawValue: ~0) else { return 0 }
        return CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: anyInput)
    }
}
