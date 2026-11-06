/** A number over a caption, used in rows separated by hairlines. */
export function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="min-w-0 flex-1 px-4 first:pl-0">
      <p className="num text-[22px] font-semibold leading-tight tracking-[-0.02em]">{value}</p>
      <p className="text-[13px] text-dim">{label}</p>
    </div>
  )
}
