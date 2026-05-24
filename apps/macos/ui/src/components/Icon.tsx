// Renders a raw SVG string (imported via ?raw, e.g. Lucide icons). The icon
// strokes in currentColor; size it with a `[&>svg]:size-…` class on className.
export function Icon({ svg, className }: { svg: string; className?: string }) {
  return <span className={className} aria-hidden dangerouslySetInnerHTML={{ __html: svg }} />
}
