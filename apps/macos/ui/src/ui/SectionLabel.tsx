import type { ReactNode } from 'react'

/** The small heading above a group, e.g. "Keys" or "Where your requests ran". */
export function SectionLabel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <h2 className={`label !text-[12px] ${className}`}>{children}</h2>
}
