// Initial-in-a-circle for a Mac / person. `You` is the only orange one.
export function Avatar({
  letter,
  size = 26,
  ring = false,
  className = '',
}: {
  letter: string
  size?: number
  ring?: boolean
  className?: string
}) {
  const you = letter === 'You'
  return (
    <span
      style={{ width: size, height: size, fontSize: Math.max(10, size * 0.4) }}
      className={`inline-grid shrink-0 place-items-center rounded-full font-medium ${
        you ? 'bg-signal px-3 text-[#1a0a03]' : 'bg-surface-2 text-ink'
      } ${ring ? 'ring-1 ring-ink/70' : ''} ${className}`}
    >
      {you ? 'You' : letter}
    </span>
  )
}
