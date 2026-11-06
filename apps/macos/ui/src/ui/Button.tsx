import type { ButtonHTMLAttributes } from 'react'

type Variant = 'primary' | 'secondary' | 'outline' | 'ghost'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-signal text-[#1a0a03] hover:brightness-110',
  secondary: 'bg-btn text-ink hover:bg-[#3a3a3a]',
  outline: 'border border-signal/60 bg-signal-soft text-signal-text hover:bg-signal/20',
  ghost: 'text-dim hover:text-ink',
}

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
}

/** The app's one button. Orange (`primary`) is reserved for the main action on a screen. */
export function Button({ variant = 'secondary', className = '', ...rest }: Props) {
  const padding = variant === 'ghost' ? '' : 'px-3.5 py-[7px]'
  return (
    <button
      {...rest}
      className={`rounded-lg text-[14px] font-semibold transition-colors disabled:opacity-40 ${padding} ${VARIANTS[variant]} ${className}`}
    />
  )
}
