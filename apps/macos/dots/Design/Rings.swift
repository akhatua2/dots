import SwiftUI

/// mulberry32: the small seeded generator the design canvas uses, so the same pool always draws the same art.
struct Mulberry32 {
    private var a: UInt32
    init(seed: UInt32) { a = seed }
    mutating func next() -> Double {
        a = a &+ 0x6D2B_79F5
        var t = (a ^ (a >> 15)) &* (1 | a)
        t = (t &+ ((t ^ (t >> 7)) &* (61 | t))) ^ t
        return Double(t ^ (t >> 14)) / 4_294_967_296
    }
}

/// A stable hash (Swift's own hashValue changes every launch).
func stableHash(_ text: String) -> UInt32 {
    text.unicodeScalars.reduce(UInt32(2_166_136_261)) { ($0 ^ $1.value) &* 16_777_619 }
}

/// A pool's nights, the way its art draws them: a seed for every 10 runs, owned by a Mac in proportion to the
/// memory it lends, at the hour it ran. Made the same way every time from the pool, so the art only changes when
/// the pool does. Nights run from noon to noon; the last one is tonight.
struct PoolHistory {
    struct Seed {
        var owner: Int
        var hour: Double
    }
    /// Mac ids, in the order they joined; "crowd" stands for a public pool's strangers
    var owners: [String]
    var seeds: [Seed]
    var nights: [Range<Int>]
    var you: Int?
    /// The night the pool reached the model it runs, and the seed where it passed a million tokens
    var levelNight: Int?
    var milestone: Int?

    /// How a pool's day goes: when its Macs are busiest, how spread out, and how its nights grow.
    struct Rhythm {
        var peak: Double
        var spread: Double
        var base: Double
        var grow: Double
    }

    static func rhythm(_ pool: Pool) -> Rhythm {
        let h = stableHash(pool.id)
        return switch pool.id {
        case "late-night-lab": Rhythm(peak: 1, spread: 4, base: 14, grow: 3.2)
        case "studio-9": Rhythm(peak: 14, spread: 5, base: 9, grow: 0.25)
        case "home": Rhythm(peak: 20.5, spread: 3, base: 5, grow: 0.2)
        case "open-science": Rhythm(peak: 12, spread: 12, base: 16, grow: 0.3)
        case "indie-hackers": Rhythm(peak: 17, spread: 7, base: 14, grow: 0.25)
        case "student-union": Rhythm(peak: 13, spread: 5, base: 40, grow: 8)
        default: Rhythm(peak: Double(h % 24), spread: 3 + Double(h / 24 % 6), base: 4 + Double(min(pool.totalMacs, 30)) / 2, grow: 0.3)
        }
    }

    /// How far into its night an hour is, counting from noon.
    static func intoNight(_ hour: Double) -> Double { (hour - 12 + 24).truncatingRemainder(dividingBy: 24) }

    static func make(_ pool: Pool, thisMacID: String, now: Date = .now) -> PoolHistory {
        let r = rhythm(pool)
        let cal = Calendar.current
        let tonight = now.nightStart
        let age = cal.dateComponents([.day], from: pool.createdAt.nightStart, to: tonight).day ?? 0
        let count = min(60, max(1, age + 1))
        let first = cal.date(byAdding: .day, value: -(count - 1), to: tonight) ?? tonight
        var owners: [(id: String, gb: Int, from: Int, lendingNow: Bool)] = pool.macs
            .sorted { $0.joinedAt < $1.joinedAt }
            .map { m in
                let joined = cal.dateComponents([.day], from: first, to: m.joinedAt.nightStart).day ?? 0
                return (m.macID, max(1, m.lendGB), max(0, joined), m.state == .lending)
            }
        if pool.crowdGB > 0 { owners.insert(("crowd", pool.crowdGB, 0, pool.crowdLending > 0), at: 0) }

        let seed = stableHash(pool.id)
        var rnd = Mulberry32(seed: seed &* 7_919)
        var hr = Mulberry32(seed: seed &* 104_729 &+ 1)
        var seeds: [Seed] = []
        var nights: [Range<Int>] = []
        for d in 0..<count {
            let last = d == count - 1
            let on = owners.indices.filter { owners[$0].from <= d && (!last || owners[$0].lendingNow) }
            let total = on.reduce(0) { $0 + owners[$1].gb }
            let n = max(1, Int(((r.base + r.grow * Double(d)) * (0.8 + rnd.next() * 0.4)).rounded()))
            var night: [Seed] = []
            if total > 0 {
                for _ in 0..<n {
                    var x = rnd.next() * Double(total)
                    var o = on[0]
                    for i in on {
                        if x < Double(owners[i].gb) { o = i; break }
                        x -= Double(owners[i].gb)
                    }
                    let hour = r.peak + ((hr.next() + hr.next() + hr.next()) / 3 - 0.5) * 2 * r.spread
                    night.append(Seed(owner: o, hour: (hour.truncatingRemainder(dividingBy: 24) + 24).truncatingRemainder(dividingBy: 24)))
                }
            }
            // In the order they ran, so the newest seeds are the last ones
            night.sort { intoNight($0.hour) < intoNight($1.hour) }
            nights.append(seeds.count..<(seeds.count + night.count))
            seeds += night
        }

        var levelNight: Int?
        if let model = pool.model {
            levelNight = (0..<count).first { d in owners.filter { $0.from <= d }.reduce(0) { $0 + $1.gb } >= model.minGB }
        }
        let million = 1_000_000 / (10 * Runs.tokensPerRun)
        return PoolHistory(owners: owners.map(\.id), seeds: seeds, nights: nights,
                           you: owners.firstIndex { $0.id == thisMacID }, levelNight: levelNight,
                           milestone: seeds.count > million ? million : nil)
    }

    /// The seeds that have run by `now`: every night's, and tonight's up to this hour.
    func visible(now: Date) -> Int {
        guard let tonight = nights.last else { return 0 }
        let h = Double(Calendar.current.component(.hour, from: now)) + Double(Calendar.current.component(.minute, from: now)) / 60
        let upTo = Self.intoNight(h)
        return tonight.lowerBound + seeds[tonight].prefix { Self.intoNight($0.hour) <= upTo }.count
    }

    /// Tonight's runs so far, ten a seed.
}

/// Rects on a pixel grid, gathered by colour and filled once each.
private struct PixelPainter {
    var cp: CGFloat
    var paths: [UInt64: Path] = [:]

    mutating func dot(_ k: Int, _ j: Int, _ rgb: UInt32, alpha: Double = 1, size: Int = 1) {
        let key = UInt64(rgb) << 8 | UInt64(alpha * 255)
        paths[key, default: Path()].addRect(CGRect(x: CGFloat(k) * cp, y: CGFloat(j) * cp, width: cp * CGFloat(size), height: cp * CGFloat(size)))
    }

    /// A round dot of radius `rad` cells at (x, y), as grid cells.
    mutating func disc(_ x: Double, _ y: Double, _ rad: Double, _ rgb: UInt32) {
        guard rad > 0.75 else { dot(Int(floor(x)), Int(floor(y)), rgb); return }
        for j in Int(floor(y - rad))...Int(floor(y + rad)) {
            for k in Int(floor(x - rad))...Int(floor(x + rad)) {
                let dx = Double(k) + 0.5 - x, dy = Double(j) + 0.5 - y
                if dx * dx + dy * dy <= rad * rad { dot(k, j, rgb) }
            }
        }
    }

    func fill(_ ctx: GraphicsContext) {
        for (key, path) in paths {
            ctx.fill(path, with: .color(Color(hex: UInt32(key >> 8), opacity: Double(key & 0xFF) / 255)))
        }
    }
}

/// The rings: a ring for each night, the first at the heart, a clock around it with midnight at the top.
/// Each hour's runs sit evenly along its arc, a dot for every 10. Tones brighten toward tonight; your Mac is orange.
struct RingsArt: View {
    var history: PoolHistory
    var size: CGFloat
    /// The grid: cells across the art
    var cells: Int
    /// Minis draw only the last few nights
    var lastNights: Int? = nil
    var hole = 0.26
    var dot: Double? = nil
    var pitch: Double? = nil
    var guides = true
    var live = false
    /// An owner to light up (a member's avatar is under the pointer)
    var focus: Int? = nil
    /// When set, a replay that started then: the nights draw again, first to last, over four seconds
    var replayStart: Date? = nil
    /// The orange hand at the hour it is now
    var showNow = true
    @Environment(\.artClock) private var artClock

    var body: some View {
        if live && artClock == nil {
            TimelineView(.animation(minimumInterval: 1 / 15)) { context in
                let replay = replayStart.map { context.date.timeIntervalSince($0) / 4.2 }.flatMap { $0 < 1 ? max(0, $0) : nil }
                canvas(now: context.date, t: context.date.timeIntervalSinceReferenceDate, replay: replay)
            }
        } else {
            canvas(now: artClock ?? .now, t: 0, replay: nil)
        }
    }

    private func canvas(now: Date, t: Double, replay: Double?) -> some View {
        Canvas(rendersAsynchronously: false) { ctx, sz in
            draw(ctx, now: now, t: t, replay: replay)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    private func draw(_ ctx: GraphicsContext, now: Date, t: Double, replay: Double?) {
        let h = history
        var p = PixelPainter(cp: size / CGFloat(cells))
        let c = Double(cells) / 2
        let count = h.nights.count
        let first = lastNights.map { max(0, count - $0) } ?? 0
        let shown = max(1, count - first)
        let R = Double(cells) * 0.44
        let r0 = R * hole
        let bw = (R - r0) / Double(shown)
        let visible = h.visible(now: now)
        let upto = replay.map { Int(Double(visible) * min(1, max(0, $0))) } ?? visible
        let tones: [UInt32] = [0x6A6A6A, 0x8A8A8A, 0xAAAAAA, 0xCACACA, 0xEDEDED]
        let dotR = dot ?? max(0.5, min(bw * 0.2, 1.5))
        let pitch = pitch ?? max(1.2, dotR * 2.5)

        if guides {
            for d in 0...shown {
                let r = r0 + Double(d) * bw
                let n = max(24, Int((2 * .pi * r / 3).rounded()))
                for q in 0..<n {
                    let a = Double(q) / Double(n) * 2 * .pi
                    p.dot(Int(floor(c + r * cos(a))), Int(floor(c + r * sin(a))), 0x171717)
                }
            }
            for hour in 0..<24 {
                let a = Double(hour) / 24 * 2 * .pi - .pi / 2
                let major = hour % 6 == 0
                for e in 0..<(hour == 0 ? 3 : major ? 2 : 1) {
                    let r = R + 2 + Double(e)
                    p.dot(Int(floor(c + r * cos(a))), Int(floor(c + r * sin(a))), hour == 0 ? 0x9A9A9A : major ? 0x5A5A5A : 0x2E2E2E)
                }
            }
            // The level the pool reached, as a circle after that night
            if let ln = h.levelNight, ln >= first, ln + 1 < count, h.nights[ln + 1].lowerBound <= upto {
                let r = r0 + Double(ln + 1 - first) * bw
                let n = max(24, Int((2 * .pi * r / 2).rounded()))
                for q in 0..<n {
                    let a = Double(q) / Double(n) * 2 * .pi
                    p.dot(Int(floor(c + r * cos(a))), Int(floor(c + r * sin(a))), 0x7A7A7A)
                }
            }
        }

        var mile: (Double, Double)?
        for d in first..<count {
            let g = d - first
            let rMid = r0 + (Double(g) + 0.5) * bw
            var bins = Array(repeating: [Int](), count: 24)
            for i in h.nights[d] where i < upto {
                bins[Int(h.seeds[i].hour) % 24].append(i)
            }
            let tone = tones[min(4, Int((shown > 1 ? Double(g) / Double(shown - 1) : 1) * 4.999))]
            for b in 0..<24 {
                let items = bins[b]
                let n = items.count
                guard n > 0 else { continue }
                // One row along the hour's arc; a busy hour closes its dots up, and only a very busy one takes two rows
                let perRow = max(1, Int(rMid * (2 * .pi / 24) * 0.9 / pitch))
                let rows = n > perRow * 2 && bw > pitch * 2.2 ? 2 : 1
                let per = Int((Double(n) / Double(rows)).rounded(.up))
                for (k, i) in items.enumerated() {
                    let s = h.seeds[i]
                    let row = k / per
                    let inRow = min(per, n - row * per)
                    let frac = 0.05 + 0.9 * ((Double(k % per) + 0.5) / Double(inRow))
                    let a = (Double(b) + frac) / 24 * 2 * .pi - .pi / 2
                    let rr = rMid + (Double(row) - Double(rows - 1) / 2) * pitch
                    var rad = dotR
                    if live, i >= upto - 8 { rad *= 0.8 + 0.6 * (0.5 + 0.5 * sin(t * 2.4 - Double(upto - i) * 0.9)) }
                    let isYou = s.owner == h.you
                    let rgb: UInt32
                    if let focus, s.owner != focus { rgb = isYou ? 0x5A230E : 0x262626 }
                    else if isYou { rgb = 0xFF5A1F }
                    else if focus != nil { rgb = 0xF4F4F4 }
                    else { rgb = tone }
                    let x = c + rr * cos(a), y = c + rr * sin(a)
                    if i == h.milestone { mile = (x, y) }
                    p.disc(x, y, rad, rgb)
                }
            }
        }
        if guides, let mile {
            let arm = max(1, Int((dotR * 1.6).rounded()))
            let k = Int(floor(mile.0)), j = Int(floor(mile.1))
            for q in -arm...arm {
                p.dot(k + q, j, 0xFFFFFF)
                p.dot(k, j + q, 0xFFFFFF)
            }
        }
        if showNow {
            let cal = Calendar.current
            let hour = Double(cal.component(.hour, from: now)) + Double(cal.component(.minute, from: now)) / 60
            let a = hour / 24 * 2 * .pi - .pi / 2
            var r = r0
            while r <= R + 4 {
                p.dot(Int(floor(c + r * cos(a))), Int(floor(c + r * sin(a))), 0xFF5A1F, alpha: 0.5)
                r += 2
            }
            let tip = R + 5.5
            p.dot(Int(floor(c + tip * cos(a))) - 1, Int(floor(c + tip * sin(a))) - 1, 0xFF5A1F, size: 3)
        }
        ctx.fill(Path(CGRect(x: 0, y: 0, width: size, height: size)), with: .color(D.bg0))
        p.fill(ctx)
    }
}

/// A pool's little mark in lists: the rings of its last six nights.
struct MiniRings: View {
    @Environment(AppModel.self) private var model
    var pool: Pool
    var size: CGFloat = 28
    var live = false

    var body: some View {
        RingsArt(history: PoolHistory.make(pool, thisMacID: model.state.thisMacID), size: size, cells: Int(size),
                 lastNights: 6, hole: 0.3, dot: 0.5, pitch: 1.3, guides: false, live: live, showNow: false)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.28, style: .continuous))
    }
}

/// Your contribution fingerprint: one groove from your Mac's first night at the heart to tonight at the edge, a dot for every 5 runs,
/// each night in the tone of the pool it lent to, with a short pause between nights. The pools it lends to now are orange.
struct RecordArt: View {
    var nights: [NightLent]
    var currentPoolIDs: Set<String> = []
    var size: CGFloat
    var live = false
    @Environment(\.artClock) private var artClock

    /// Grey tones for the pools, in the order your Mac first lent to them
    static let tones: [UInt32] = [0x8A8A8A, 0xCFCFCF, 0xA6A6A6, 0x6E6E6E, 0xBDBDBD, 0x9A9A9A]

    static func tone(for poolID: String, in nights: [NightLent], current: Set<String>) -> UInt32 {
        if current.contains(poolID) { return 0xFF5A1F }
        var order: [String] = []
        for n in nights where !current.contains(n.poolID) && !order.contains(n.poolID) { order.append(n.poolID) }
        return tones[(order.firstIndex(of: poolID) ?? 0) % tones.count]
    }

    var body: some View {
        if live && artClock == nil {
            TimelineView(.animation(minimumInterval: 1 / 15)) { context in
                canvas(t: context.date.timeIntervalSinceReferenceDate)
            }
        } else {
            canvas(t: 0)
        }
    }

    private func canvas(t: Double) -> some View {
        Canvas(rendersAsynchronously: false) { ctx, sz in
            let w = Double(sz.width)
            let c = w / 2
            let R = w * 0.44, r0 = w * 0.12
            let pause = 3
            let dots = nights.map { max(1, $0.runs / 5) }
            let count = dots.reduce(0) { $0 + $1 + pause }
            guard count > 0 else { return }
            let area = Double.pi * (R * R - r0 * r0) / Double(count)
            let s = (area * 3.2).squareRoot()
            let step = area / s
            let b = s / (2 * .pi)
            let rad = w / 420
            var tonePaths: [UInt32: Path] = [:]
            var th = 0.0
            for (i, night) in nights.enumerated() {
                let rgb = Self.tone(for: night.poolID, in: nights, current: currentPoolIDs)
                let n = dots[i]
                let tonight = i == nights.count - 1
                for k in 0..<(n + pause) {
                    let r = r0 + b * th
                    if k < n {
                        var rr = rad
                        if live, tonight, k >= n - 14 { rr *= 0.8 + 0.6 * (0.5 + 0.5 * sin(t * 2.4 - Double(n - k) * 0.6)) }
                        tonePaths[rgb, default: Path()].addEllipse(in: CGRect(x: c + r * sin(th) - rr, y: c - r * cos(th) - rr, width: 2 * rr, height: 2 * rr))
                    }
                    th += step / r
                }
            }
            for (rgb, path) in tonePaths { ctx.fill(path, with: .color(Color(hex: rgb))) }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

extension EnvironmentValues {
    /// A fixed time for the art, when drawn as a still for `--snapshot`
    @Entry var artClock: Date? = nil
}
