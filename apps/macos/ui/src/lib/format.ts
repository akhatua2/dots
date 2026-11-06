/** 1245 -> "1,245" */
export const fmt = (n: number) => Math.round(n).toLocaleString('en-US')

/** "Late Night Lab" -> "L" */
export const initial = (name: string) => (name[0] ?? '?').toUpperCase()

/** "mlx-community/Qwen2.5-0.5B-Instruct-4bit" -> "Qwen2.5-0.5B" */
export function shortModel(id: string): string {
  const tail = id.split('/').pop() || id
  return tail.replace(/-(4bit|8bit|Instruct)/gi, '')
}

/** 1 -> "1 Mac", 3 -> "3 Macs" */
export const macs = (n: number) => `${n} ${n === 1 ? 'Mac' : 'Macs'}`

/** "Maya", "Jun" -> "Maya and Jun are here" */
export function whoIsHere(names: string[]): string {
  if (names.length === 0) return ''
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
  return `${list} ${names.length === 1 ? 'is' : 'are'} here`
}
