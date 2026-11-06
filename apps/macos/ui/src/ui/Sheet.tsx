import type { ReactNode } from 'react'

/**
 * A focused task (accepting an invite, joining a boost) shown in the main
 * window's own content area, never in a separate window or dialog. The footer
 * stays pinned so the actions are always in the same place.
 */
export function Sheet({ children, footer, width = 520 }: { children: ReactNode; footer: ReactNode; width?: number }) {
  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto px-8 py-10" style={{ maxWidth: width }}>
          {children}
        </div>
      </div>
      <div className="border-t border-line">
        <div className="mx-auto flex items-center justify-end gap-3 px-8 py-4" style={{ maxWidth: width }}>
          {footer}
        </div>
      </div>
    </div>
  )
}
