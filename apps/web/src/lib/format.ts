export function cu(n: number): string {
  if (!isFinite(n)) return '0'
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `${(n / 1_000).toFixed(2)}K`
  if (abs >= 1) return n.toFixed(2)
  return n.toFixed(4)
}

export function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 6)}…${id.slice(-4)}` : id
}
