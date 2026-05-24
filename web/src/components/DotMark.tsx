// The dots wordmark glyph: a 3×3 matrix of nodes with a lit amber cluster —
// the brand motif (a mesh of compute nodes) rendered as literal dots.
export function DotMark({ size = 18, lit = [0, 4, 8, 5] }: { size?: number; lit?: number[] }) {
  const cells = Array.from({ length: 9 }, (_, i) => i)
  const gap = size / 2
  const r = size / 9
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${gap * 2} ${gap * 2}`}
      aria-hidden
      className="shrink-0"
    >
      {cells.map((i) => {
        const cx = (i % 3) * gap + r
        const cy = Math.floor(i / 3) * gap + r
        const isLit = lit.includes(i)
        return (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={r}
            fill={isLit ? 'var(--color-signal)' : 'var(--color-faint)'}
          />
        )
      })}
    </svg>
  )
}
