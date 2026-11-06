// Model marks in the dots style: an abstract glyph per model family, drawn on
// the same black rounded square as the pool icons and ordered-dithered across
// black / grey / white so it reads as one family with them. The glyphs are our
// own simplified shapes, not the vendors' trademarks.
import { memo, useCallback } from 'react'

const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]

export type Family = 'qwen' | 'deepseek' | 'kimi' | 'llama' | 'claude' | 'generic'

export function familyOf(model: string): Family {
  const m = model.toLowerCase()
  if (m.includes('qwen')) return 'qwen'
  if (m.includes('deepseek')) return 'deepseek'
  if (m.includes('kimi')) return 'kimi'
  if (m.includes('llama')) return 'llama'
  if (m.includes('claude')) return 'claude'
  return 'generic'
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))
// Soft-edged coverage from a signed distance (negative = inside).
const cov = (d: number, soft = 0.07) => clamp01(0.5 - d / soft)
const ring = (d: number, r: number, w: number) => Math.abs(d - r) - w
const hex = (x: number, y: number, r: number) => {
  const ax = Math.abs(x)
  const ay = Math.abs(y)
  return Math.max(ay, ax * 0.866 + ay * 0.5) - r
}
const seg = (px: number, py: number, ax: number, ay: number, bx: number, by: number) => {
  const dx = bx - ax
  const dy = by - ay
  const t = clamp01(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy))
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

// Each glyph returns coverage 0..1 at (x, y) in [-1, 1].
const GLYPH: Record<Family, (x: number, y: number) => number> = {
  // A hexagon ring with a tail through its lower right corner (a Q).
  qwen: (x, y) => {
    const h = cov(Math.abs(hex(x, y, 0.62)) - 0.13)
    const tail = cov(seg(x, y, 0.1, 0.1, 0.72, 0.72) - 0.12)
    return Math.max(h, tail)
  },
  // A whale: round body, lifted tail fin, one dark eye.
  deepseek: (x, y) => {
    const body = cov(Math.hypot((x + 0.12) / 0.72, (y - 0.12) / 0.46) - 1)
    const fin = Math.max(
      cov(seg(x, y, 0.5, 0.0, 0.8, -0.38) - 0.1),
      cov(seg(x, y, 0.5, 0.0, 0.86, 0.08) - 0.1),
    )
    const eye = cov(Math.hypot(x + 0.42, y - 0.02) - 0.07)
    return clamp01(Math.max(body, fin) - eye * 0.95)
  },
  // A crescent moon with a small star dot.
  kimi: (x, y) => {
    const moon = cov(Math.max(Math.hypot(x, y) - 0.7, -(Math.hypot(x - 0.3, y + 0.12) - 0.58)))
    const dot = cov(Math.hypot(x - 0.5, y + 0.5) - 0.1)
    return Math.max(moon, dot)
  },
  // Two linked loops.
  llama: (x, y) => {
    const a = cov(ring(Math.hypot(x + 0.38, y), 0.34, 0.11))
    const b = cov(ring(Math.hypot(x - 0.38, y), 0.34, 0.11))
    return Math.max(a, b)
  },
  // An eight-ray burst.
  claude: (x, y) => {
    let best = 0
    for (let i = 0; i < 8; i++) {
      const a = (i * Math.PI) / 4
      const len = i % 2 === 0 ? 0.78 : 0.56
      best = Math.max(best, cov(seg(x, y, 0, 0, Math.cos(a) * len, Math.sin(a) * len) - 0.075))
    }
    return Math.max(best, cov(Math.hypot(x, y) - 0.14))
  },
  generic: (x, y) => cov(Math.abs(Math.hypot(x, y) - 0.5) - 0.12),
}

function draw(cv: HTMLCanvasElement, family: Family, cells: number) {
  const ctx = cv.getContext('2d')
  if (!ctx) return
  const px = cv.width
  const cell = px / cells
  ctx.fillStyle = '#050505'
  ctx.fillRect(0, 0, px, px)
  const g = GLYPH[family]
  for (let j = 0; j < cells; j++) {
    for (let i = 0; i < cells; i++) {
      const x = ((i + 0.5) / cells) * 2 - 1
      const y = ((j + 0.5) / cells) * 2 - 1
      const inside = g(x, y)
      // Light from above: the glyph is brightest at the top and dithers darker below.
      const tone = inside * (0.98 - 0.32 * ((y + 1) / 2))
      const t = (BAYER[(j % 4) * 4 + (i % 4)] + 0.5) / 16
      if (tone > t * 0.9 + 0.04) ctx.fillStyle = '#e4e4e4'
      else if (inside > 0.2 && tone > t * 0.9 - 0.2) ctx.fillStyle = '#6a6a6a'
      else if (inside < 0.05 && ((i + j) % 4 === 0) && Math.hypot(x, y) < 0.95) ctx.fillStyle = '#1c1c1c'
      else continue
      ctx.fillRect(i * cell, j * cell, cell, cell)
    }
  }
}

export const ModelMark = memo(function ModelMark({
  model,
  size = 20,
  className = '',
}: {
  model: string
  size?: number
  className?: string
}) {
  const family = familyOf(model)
  const cells = size >= 40 ? 28 : size >= 26 ? 22 : 16
  const ref = useCallback(
    (cv: HTMLCanvasElement | null) => {
      if (cv) draw(cv, family, cells)
    },
    [family, cells],
  )
  return (
    <canvas
      ref={ref}
      width={cells * 4}
      height={cells * 4}
      role="img"
      aria-label={model}
      style={{ width: size, height: size, imageRendering: 'pixelated', borderRadius: size * 0.25 }}
      className={`shrink-0 ${className}`}
    />
  )
})
