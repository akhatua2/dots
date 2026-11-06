// Pill switch. `accent` (orange) is the master on/off; `light` is the quiet
// secondary switch — a pale track with a dark knob when on.
export function Toggle({
  on,
  onChange,
  variant = 'light',
  label,
}: {
  on: boolean
  onChange: (v: boolean) => void
  variant?: 'accent' | 'light'
  label: string
}) {
  const track = on ? (variant === 'accent' ? 'bg-signal' : 'bg-[#d0d0d0]') : 'bg-[#3a3a3a]'
  const knob = on ? (variant === 'accent' ? 'bg-[#f3f3f3]' : 'bg-[#141414]') : 'bg-[#f3f3f3]'
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={`relative h-[24px] w-[40px] shrink-0 rounded-full transition-colors duration-150 ${track}`}
    >
      <span
        className={`absolute top-[3px] size-[18px] rounded-full transition-all duration-150 ${knob} ${
          on ? 'left-[19px]' : 'left-[3px]'
        }`}
      />
    </button>
  )
}
