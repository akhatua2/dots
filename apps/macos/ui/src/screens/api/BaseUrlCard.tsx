import { useState } from 'react'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'

/** The address other apps on this Mac call, and whether it is up. */
export function BaseUrlCard({ base, running }: { base: string; running: boolean }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    await navigator.clipboard.writeText(base)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <Card className="mt-5 flex items-center justify-between gap-4 px-[18px] py-3.5">
      <div className="min-w-0">
        <p className="label !text-[10.5px]">Base URL</p>
        <p className="mt-1 truncate font-mono text-[15.5px]">{base}</p>
      </div>
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-2 text-[13.5px] text-[#cfcfcf]">
          <span className={`size-2 rounded-full ${running ? 'bg-signal' : 'bg-faint'}`} />
          {running ? 'Running on this Mac' : 'Not running'}
        </span>
        <Button onClick={() => void copy()}>{copied ? 'Copied' : 'Copy'}</Button>
      </div>
    </Card>
  )
}
