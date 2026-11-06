import type { HTMLAttributes } from 'react'

/** A bordered surface. The border carries the structure, so there is no shadow. */
export function Card({ className = '', ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div {...rest} className={`rounded-2xl border border-line bg-card ${className}`} />
}
