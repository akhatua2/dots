import Foundation

/// Runs this Mac's real dots workers while the lending rules say lend, and stops them when they don't.
///
/// A worker is the engine's own process (`dots worker <ticket> --pool <pool> --max-ram <GB>`): it joins the hub
/// over iroh and holds its share of that pool's model. A Mac lending to several pools runs one for each, held to
/// that pool's share, so the pools never compete for the same memory. dots finds the worker in this order:
///   1. `DOTS_WORKER`, a whole command line, for development (for example `…/python -m dots.cli worker`);
///   2. a `dots-worker` binary inside the app;
///   3. `~/.dots/worker/dots-worker`, where the hub's download puts it.
/// Without one, lending is reported to the cloud but no work is done, and `isAvailable` says so.

@MainActor
final class WorkerProcess {
    /// What one worker runs with: the hub, the pool's name there, and the memory it may use
    private struct Plan: Equatable {
        var ticket: String
        var pool: String
        var gb: Int
    }

    /// The running workers, by their pool at the hub
    private var running: [String: (process: Process, plan: Plan)] = [:]
    /// What each pool's worker should run with; a worker that exits while it is still wanted starts again
    private var wanted: [String: Plan] = [:]

    /// The command to start a worker, without its arguments, or nil when there is none on this Mac.
    let command: [String]?

    init(environment: [String: String] = ProcessInfo.processInfo.environment) {
        command = Self.findCommand(environment)
        home = environment["DOTS_HOME"] ?? NSHomeDirectory() + "/.dots"
        Self.stopLeftovers(in: home)
    }

    /// Where the workers keep their identities, logs and pid files.
    private let home: String

    var isAvailable: Bool { command != nil }

    /// The account this Mac's workers carry, so the hub knows which Macs are whose: read from the workers' home, or
    /// made there the way the engine makes it ("acc-" and 32 hex digits) when no worker has run yet.
    var account: String {
        let path = home + "/account"
        if let text = try? String(contentsOfFile: path, encoding: .utf8) {
            let existing = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !existing.isEmpty { return existing }
        }
        let made = "acc-" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        try? FileManager.default.createDirectory(atPath: home, withIntermediateDirectories: true)
        try? (made + "\n").write(toFile: path, atomically: true, encoding: .utf8)
        return made
    }
    var isRunning: Bool { running.values.contains { $0.process.isRunning } }

    /// Brings the workers in line with the rules: one for each share, running with its pool and memory, or none.
    func reconcile(lend: Bool, shares: [LendingShare], ticket: String?) {
        guard lend, let ticket, command != nil else { stop(); return }
        let plans = shares.filter { $0.gb > 0 }.map { Plan(ticket: ticket, pool: $0.hubPool ?? $0.poolID, gb: $0.gb) }
        wanted = Dictionary(plans.map { ($0.pool, $0) }, uniquingKeysWith: { first, _ in first })
        for (pool, worker) in running where wanted[pool] != worker.plan {
            stop(pool: pool)
        }
        for plan in wanted.values where running[plan.pool] == nil {
            start(plan)
        }
    }

    func stop() {
        wanted = [:]  // first, so the exits below aren't taken for crashes
        for pool in running.keys { stop(pool: pool) }
    }

    private func stop(pool: String) {
        guard let worker = running.removeValue(forKey: pool) else { return }
        if worker.process.isRunning { worker.process.terminate() }
        try? FileManager.default.removeItem(atPath: pidFile(pool))
    }

    /// The default pool's files keep the names a Mac lending to one pool always had
    private func pidFile(_ pool: String) -> String { home + "/" + Self.fileName("worker", pool, "pid") }

    private static func fileName(_ stem: String, _ pool: String, _ ext: String) -> String {
        guard !pool.isEmpty else { return "\(stem).\(ext)" }
        let safe = String(pool.map { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" ? $0 : "_" })
        return "\(stem)-\(safe).\(ext)"
    }

    /// A worker exited while dots still wanted it lending (it crashed, or something else stopped it): start it
    /// again after a pause, so a worker that keeps failing doesn't spin.
    private func exited(_ p: Process, plan: Plan) {
        guard running[plan.pool]?.process === p else { return }
        running[plan.pool] = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.restartDelay) { [weak self] in
            MainActor.assumeIsolated {
                guard let self, self.running[plan.pool] == nil, self.wanted[plan.pool] == plan else { return }
                self.start(plan)
            }
        }
    }

    private static let restartDelay: TimeInterval = 3

    /// Workers a previous run of dots started and couldn't stop (it was killed, or crashed) would lend on their
    /// own. Stop them, but only the pids that are still dots workers.
    private static func stopLeftovers(in home: String) {
        let files = (try? FileManager.default.contentsOfDirectory(atPath: home)) ?? []
        for name in files where name.hasPrefix("worker") && name.hasSuffix(".pid") {
            let pidFile = home + "/" + name
            defer { try? FileManager.default.removeItem(atPath: pidFile) }
            guard let text = try? String(contentsOfFile: pidFile, encoding: .utf8),
                  let pid = pid_t(text.trimmingCharacters(in: .whitespacesAndNewlines)) else { continue }
            let probe = Process()
            probe.executableURL = URL(fileURLWithPath: "/bin/ps")
            probe.arguments = ["-o", "command=", "-p", String(pid)]
            let out = Pipe()
            probe.standardOutput = out
            guard (try? probe.run()) != nil else { continue }
            probe.waitUntilExit()
            let command = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            if command.contains("dots") && command.contains("worker") { kill(pid, SIGTERM) }
        }
    }

    private func start(_ plan: Plan) {
        guard let command else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: command[0])
        p.arguments = Array(command.dropFirst()) + [plan.ticket, "--pool", plan.pool, "--max-ram", String(plan.gb)]
        // Run from the worker's own home so it never picks up a .env from some other working directory.
        try? FileManager.default.createDirectory(atPath: home, withIntermediateDirectories: true)
        p.currentDirectoryURL = URL(fileURLWithPath: home)
        p.standardOutput = FileHandle.nullDevice
        p.standardError = Self.logFile(home + "/" + Self.fileName("worker", plan.pool, "log"))
        p.terminationHandler = { [weak self] ended in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.exited(ended, plan: plan) } }
        }
        do {
            try p.run()
            running[plan.pool] = (p, plan)
            try? String(p.processIdentifier).write(toFile: pidFile(plan.pool), atomically: true, encoding: .utf8)
        } catch {
            running[plan.pool] = nil
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

    private static func logFile(_ path: String) -> FileHandle {
        FileManager.default.createFile(atPath: path, contents: nil)
        return FileHandle(forWritingAtPath: path) ?? FileHandle.nullDevice
    }
}
