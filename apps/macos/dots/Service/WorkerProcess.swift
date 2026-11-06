import Foundation

/// Runs this Mac's real dots worker while the lending rules say lend, and stops it when they don't.
///
/// The worker is the engine's own process (`dots worker <ticket> --max-ram <GB>`): it joins the hub over iroh
/// and holds its share of the model's layers. dots finds it in this order:
///   1. `DOTS_WORKER`, a whole command line, for development (for example `…/python -m dots.cli worker`);
///   2. a `dots-worker` binary inside the app;
///   3. `~/.dots/worker/dots-worker`, where the hub's download puts it.
/// Without one, lending is reported to the cloud but no work is done, and `isAvailable` says so.
@MainActor
final class WorkerProcess {
    private var process: Process?
    private var runningWith: (ticket: String, gb: Int)?

    /// The command to start a worker, without its arguments, or nil when there is none on this Mac.
    let command: [String]?

    init(environment: [String: String] = ProcessInfo.processInfo.environment) {
        command = Self.findCommand(environment)
    }

    var isAvailable: Bool { command != nil }
    var isRunning: Bool { process?.isRunning == true }

    /// Brings the worker in line with the rules: running with this ticket and limit, or stopped.
    func reconcile(lend: Bool, gb: Int, ticket: String?) {
        guard lend, gb > 0, let ticket, let command else { stop(); return }
        if isRunning, let current = runningWith, current.ticket == ticket, current.gb == gb { return }
        stop()
        start(command: command, ticket: ticket, gb: gb)
    }

    func stop() {
        if let process, process.isRunning { process.terminate() }
        process = nil
        runningWith = nil
    }

    private func start(command: [String], ticket: String, gb: Int) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: command[0])
        p.arguments = Array(command.dropFirst()) + [ticket, "--max-ram", String(gb)]
        // Run from the worker's own home so it never picks up a .env from some other working directory.
        let home = ProcessInfo.processInfo.environment["DOTS_HOME"] ?? NSHomeDirectory() + "/.dots"
        try? FileManager.default.createDirectory(atPath: home, withIntermediateDirectories: true)
        p.currentDirectoryURL = URL(fileURLWithPath: home)
        p.standardOutput = FileHandle.nullDevice
        p.standardError = Self.logFile(in: home)
        do {
            try p.run()
            process = p
            runningWith = (ticket, gb)
        } catch {
            process = nil
            runningWith = nil
        }
    }

    private static func findCommand(_ environment: [String: String]) -> [String]? {
        if let line = environment["DOTS_WORKER"], !line.isEmpty {
            return line.split(separator: " ").map(String.init)
        }
        let candidates: [String?] = [
            Bundle.main.url(forResource: "dots-worker", withExtension: nil)?.path,
            NSHomeDirectory() + "/.dots/worker/dots-worker",
        ]
        let found = candidates.compactMap { $0 }.first { FileManager.default.isExecutableFile(atPath: $0) }
        return found.map { [$0] }
    }

    private static func logFile(in home: String) -> FileHandle {
        let path = home + "/worker.log"
        FileManager.default.createFile(atPath: path, contents: nil)
        return FileHandle(forWritingAtPath: path) ?? FileHandle.nullDevice
    }
}
