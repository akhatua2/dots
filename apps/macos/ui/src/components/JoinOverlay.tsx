// The moment a Mac joins a pool: the screen fills with the pool's halftone,
// dense at the edges and open in the middle, and your dot takes its place.
import type { JoinMoment } from '../lib/store'

const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]
const CELL = 9

function paint(cv: HTMLCanvasElement | null) {
  if (!cv) return
  const dpr = window.devicePixelRatio || 1
  const w = window.innerWidth
  const h = window.innerHeight
  cv.width = w * dpr
  cv.height = h * dpr
  const ctx = cv.getContext('2d')
  if (!ctx) return
  ctx.scale(dpr, dpr)
  ctx.fillStyle = '#0c0c0c'
  ctx.fillRect(0, 0, w, h)
  const cols = Math.ceil(w / CELL)
  const rows = Math.ceil(h / CELL)
  const ox = w * 0.5
  const oy = h * 0.5
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const x = i * CELL + CELL / 2 - ox
      const y = j * CELL + CELL / 2 - oy
      const r = Math.hypot(x / w, y / h) * 2
      const a = Math.atan2(y, x)
      // Open black middle with a few stray dots; a grey checkerboard takes over
      // toward the edges, its boundary rippled.
      const ramp = Math.min(1, Math.max(0, (r + 0.05 * Math.sin(a * 3 + r * 7) - 0.62) / 0.55))
      const d = 0.05 + ramp * 0.5
      const t = (BAYER[(j % 4) * 4 + (i % 4)] + 0.5) / 16
      if (d > t) {
        ctx.fillStyle = ramp > 0.2 ? '#7d7d7d' : '#9a9a9a'
        ctx.fillRect(i * CELL + 1, j * CELL + 1, CELL - 2, CELL - 2)
      }
    }
  }
}

export function JoinOverlay({ j, device }: { j: JoinMoment; device: string }) {
  return (
    <div className="fade fixed inset-0 z-50 grid place-items-center overflow-hidden bg-[#0c0c0c]">
      <canvas ref={paint} className="absolute inset-0 size-full" />
      <div key={j.stage} className="rise relative text-center">
        {j.stage === 'in' ? (
          <>
            <h1 className="text-[78px] font-bold leading-none tracking-[-0.035em]">You’re in.</h1>
            <p className="mt-3 text-[16px] text-[#a3a3a3]">
              Your {device} is Mac {j.n} in {j.poolName}.
            </p>
          </>
        ) : (
          <>
            <p className="mb-4 text-[11px] font-semibold tracking-[0.32em] text-[#a3a3a3]">UNLOCKED</p>
            <h1 className="text-[78px] font-bold leading-none tracking-[-0.035em]">{j.model}</h1>
            <p className="mt-3 text-[16px] text-[#a3a3a3]">
              {j.params ? `${j.params} parameters. ` : ''}Your Mac took {j.poolName} to {j.capacity} GB.
            </p>
          </>
        )}
      </div>
      <span className="absolute bottom-[29%] right-[26%] flex items-center gap-2 text-[12px] font-semibold text-signal">
        <span className="size-2.5 bg-signal shadow-[0_0_10px_2px_rgba(255,90,31,0.6)]" /> you
      </span>
    </div>
  )
}
