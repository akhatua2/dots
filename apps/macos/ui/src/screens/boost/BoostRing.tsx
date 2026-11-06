import { useCallback } from 'react'

const DOTS = 84

/** A ring of small squares that fills clockwise as memory is pledged. */
export function BoostRing({ filled, label, caption, size = 250 }: { filled: number; label: string; caption: string; size?: number }) {
  const paint = useCallback(
    (cv: HTMLCanvasElement | null) => {
      const ctx = cv?.getContext('2d')
      if (!cv || !ctx) return
      const px = cv.width
      const c = px / 2
      const r = c * 0.86
      const on = Math.round(DOTS * Math.min(1, Math.max(0, filled)))
      ctx.clearRect(0, 0, px, px)
      for (let i = 0; i < DOTS; i++) {
        const a = (i / DOTS) * Math.PI * 2 - Math.PI / 2
        ctx.fillStyle = i < on ? `rgba(235,235,235,${0.45 + 0.55 * (i / DOTS)})` : 'rgba(255,255,255,0.14)'
        ctx.fillRect(c + Math.cos(a) * r - 4, c + Math.sin(a) * r - 4, 8, 8)
      }
      ctx.fillStyle = '#ff5a1f'
      ctx.fillRect(c - 2, c - r - 16, 4, 11)
    },
    [filled],
  )

  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <canvas ref={paint} width={size * 2} height={size * 2} style={{ width: size, height: size }} />
      <div className="absolute text-center">
        <p className="text-[54px] font-semibold leading-none tracking-[-0.03em]">{label}</p>
        <p className="mt-1.5 text-[14px] text-dim">{caption}</p>
      </div>
    </div>
  )
}
