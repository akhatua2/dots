import { DotMark } from './DotMark'

export function NoInvite() {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="text-center">
        <div className="flex justify-center">
          <DotMark size={36} lit={[0, 1, 4, 5, 8]} />
        </div>
        <p className="mt-5 font-sans text-[15px] text-dim">Open your invite link</p>
      </div>
    </div>
  )
}
