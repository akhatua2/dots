// The dithered mark that stands for a pool: a rounded square of coarse pixels,
// rippled outward from a centre, ordered-dithered across black / grey / white.
// Deterministic per seed so each pool keeps its own pattern.
import { memo, useCallback } from 'react'

const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]

function hash(s: string, salt = 0): number {
  let h = 2166136261 ^ salt
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return (h >>> 0) / 4294967295
}

function draw(cv: HTMLCanvasElement, seed: string, cells: number) {
  const ctx = cv.getContext('2d')
  if (!ctx) return
  const px = cv.width
  ctx.fillStyle = '#050505'
  ctx.fillRect(0, 0, px, px)
  const cell = px / cells
  // Ripple centre drifts off-axis per seed; two ripple frequencies interfere.
  const cx = (hash(seed, 1) - 0.5) * 0.7
  const cy = (hash(seed, 2) - 0.5) * 0.7
  const f1 = 5 + hash(seed, 3) * 7
  const f2 = 2 + hash(seed, 4) * 4
  const ph = hash(seed, 5) * 6.28
  for (let j = 0; j < cells; j++) {
    for (let i = 0; i < cells; i++) {
      const x = ((i + 0.5) / cells) * 2 - 1 - cx
      const y = ((j + 0.5) / cells) * 2 - 1 - cy
      const r = Math.hypot(x, y)
      const a = Math.atan2(y, x)
      const v = 0.5 + 0.25 * Math.sin(r * f1 + ph) + 0.25 * Math.sin(r * f2 + a * 2 - ph)
      const t = (BAYER[(j % 4) * 4 + (i % 4)] + 0.5) / 16
      if (v > t + 0.12) ctx.fillStyle = '#d9d9d9'
      else if (v > t - 0.2) ctx.fillStyle = '#5c5c5c'
      else continue
      ctx.fillRect(i * cell, j * cell, cell, cell)
    }
  }
}

export const Swirl = memo(function Swirl({
  seed,
  size = 44,
  cells = 26,
  className = '',
}: {
  seed: string
  size?: number
  cells?: number
  className?: string
}) {
  const ref = useCallback(
    (cv: HTMLCanvasElement | null) => {
      if (cv) draw(cv, seed, cells)
    },
    [seed, cells],
  )
  return (
    <canvas
      ref={ref}
      width={cells * 4}
      height={cells * 4}
      style={{ width: size, height: size, imageRendering: 'pixelated', borderRadius: size * 0.25 }}
      className={`shrink-0 ${className}`}
    />
  )
})
