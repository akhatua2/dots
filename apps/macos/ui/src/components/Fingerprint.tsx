// A pool's contribution fingerprint: a polar field of faint dots with a fan of
// bright dashes where Macs have lent, one orange fleck where the last night
// ended. Deterministic per seed, so every pool keeps its own print. The big one
// draws itself in; the small ones (pool icons) are static.
import { memo, useCallback } from 'react'

function hash(s: string, salt = 0): number {
  let h = 2166136261 ^ salt
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return (h >>> 0) / 4294967295
}

interface Opts {
  seed: string
  rings: number
  fan: number // 0..1: how much of the print has been filled in
  draw: number // 0..1: reveal progress (animation)
}

function paint(cv: HTMLCanvasElement, o: Opts) {
  const ctx = cv.getContext('2d')
  if (!ctx) return
  const px = cv.width
  const c = px / 2
  const R = c * 0.88
  const inner = R * 0.2
  const unit = Math.max(1, px / 220)
  ctx.clearRect(0, 0, px, px)
  // Each pool's print points its own way, so the icons are told apart at a glance.
  const center = hash(o.seed, 1) * Math.PI * 2
  const spanBase = 0.5 + o.fan * 0.9
  for (let k = 0; k < o.rings; k++) {
    const f = k / (o.rings - 1)
    const r = inner * 1.25 + (R - inner * 1.25) * f
    const gap = (R - inner) / o.rings
    const m = Math.max(12, Math.floor((2 * Math.PI * r) / (gap * 0.9)))
    const span = spanBase * (0.5 + 0.9 * f) * (0.8 + 0.4 * hash(o.seed, 10 + k))
    for (let j = 0; j < m; j++) {
      const a = (j / m) * 2 * Math.PI
      const x = c + Math.cos(a) * r
      const y = c + Math.sin(a) * r
      let d = a - center
      d = Math.atan2(Math.sin(d), Math.cos(d))
      const inFan = Math.abs(d) < span / 2
      // progress of the reveal: sweeps out from the fan's edge, ring by ring.
      const reveal = (f * 0.6 + (Math.abs(d) / Math.PI) * 0.6) <= o.draw * 1.3
      if (inFan && reveal) {
        const edge = 1 - Math.abs(d) / (span / 2)
        // Solid dashes through the body of the fan; only its rim frays.
        const frayed = edge < 0.22 && hash(o.seed, k * 977 + j) > edge * 4
        const gap = j % 4 === 3
        if (!frayed && !gap) {
          // Alternate rows brighten and dim, brightest through the middle rings.
          const band = (k % 2 === 0 ? 1 : 0.58) * (0.5 + 0.5 * Math.sin(Math.min(1, f * 1.15) * Math.PI))
          const v = Math.round(255 * Math.min(1, band))
          ctx.fillStyle = `rgb(${v},${v},${v})`
          ctx.fillRect(x - unit * 1.8, y - unit * 0.55, unit * 3.6, unit * 1.1)
          continue
        }
      }
      ctx.fillStyle = 'rgba(255,255,255,0.11)'
      ctx.fillRect(x - unit * 0.5, y - unit * 0.5, unit, unit)
    }
  }
  // orange flecks where the last night ended, and a thin radial line to the edge
  const end = center + spanBase * 0.5
  ctx.strokeStyle = 'rgba(255,90,31,0.55)'
  ctx.lineWidth = unit * 0.5
  ctx.beginPath()
  ctx.moveTo(c + Math.cos(end) * inner * 1.2, c + Math.sin(end) * inner * 1.2)
  ctx.lineTo(c + Math.cos(end) * R * 0.98, c + Math.sin(end) * R * 0.98)
  ctx.stroke()
  ctx.fillStyle = '#ff5a1f'
  ctx.fillRect(c + Math.cos(end) * R * 0.98 - unit * 1.2, c + Math.sin(end) * R * 0.98 - unit * 1.2, unit * 2.4, unit * 2.4)
  // compass ticks
  ctx.fillStyle = 'rgba(255,255,255,0.35)'
  for (const a of [0, 1, 2, 3]) {
    const t = (a * Math.PI) / 2
    ctx.fillRect(c + Math.cos(t) * (R + unit * 4) - unit * 0.5, c + Math.sin(t) * (R + unit * 4) - unit * 0.5, unit, unit)
  }
}

export const Fingerprint = memo(function Fingerprint({
  seed,
  size = 300,
  rings = 26,
  fan = 0.6,
  animate = false,
  className = '',
}: {
  seed: string
  size?: number
  rings?: number
  fan?: number
  animate?: boolean
  className?: string
}) {
  const ref = useCallback(
    (cv: HTMLCanvasElement | null) => {
      if (!cv) return
      if (!animate) {
        paint(cv, { seed, rings, fan, draw: 1 })
        return
      }
      let raf = 0
      const t0 = performance.now()
      const tick = (now: number) => {
        const p = Math.min(1, (now - t0) / 1800)
        paint(cv, { seed, rings, fan, draw: 1 - Math.pow(1 - p, 3) })
        if (p < 1) raf = requestAnimationFrame(tick)
      }
      raf = requestAnimationFrame(tick)
      return () => cancelAnimationFrame(raf)
    },
    [seed, rings, fan, animate],
  )
  return (
    <canvas
      ref={ref}
      width={size * 2}
      height={size * 2}
      style={{ width: size, height: size }}
      className={`shrink-0 ${className}`}
    />
  )
})

/** The small square pool icon: a black rounded tile holding a tiny fingerprint. */
export function PoolMark({ seed, size = 40 }: { seed: string; size?: number }) {
  return (
    <span
      style={{ width: size, height: size, borderRadius: size * 0.26 }}
      className="inline-grid shrink-0 place-items-center overflow-hidden bg-[#0a0a0a]"
    >
      <Fingerprint seed={seed} size={size * 1.05} rings={size > 36 ? 14 : 9} fan={0.35 + hash(seed, 3) * 0.5} />
    </span>
  )
}
