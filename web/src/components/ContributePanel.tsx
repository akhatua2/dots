import { useState } from 'react'
import type { Conn } from '../lib/config'

export function ContributePanel({ conn, onClose }: { conn: Conn; onClose: () => void }) {
  const [step, setStep] = useState<'idle' | 'downloading' | 'done' | 'error'>('idle')

  async function download() {
    setStep('downloading')
    try {
      // Fetch the binary from the hub (token-gated, no public hosting needed).
      const res = await fetch(`${conn.api}/v1/download/worker`, {
        headers: {
          Authorization: `Bearer ${conn.token}`,
          'ngrok-skip-browser-warning': 'true',
        },
      })
      if (!res.ok) throw new Error(`hub returned ${res.status}`)
      const blob = await res.blob()

      // 1. Binary
      const binUrl = URL.createObjectURL(blob)
      const binLink = document.createElement('a')
      binLink.href = binUrl
      binLink.download = 'dots-worker'
      binLink.click()
      URL.revokeObjectURL(binUrl)

      // 2. Token config — binary reads hub.env from its own directory.
      const cfg = `DOTS_JOIN_TOKEN=${conn.token}\n`
      const cfgBlob = new Blob([cfg], { type: 'text/plain' })
      const cfgUrl = URL.createObjectURL(cfgBlob)
      const cfgLink = document.createElement('a')
      cfgLink.href = cfgUrl
      cfgLink.download = 'hub.env'
      cfgLink.click()
      URL.revokeObjectURL(cfgUrl)

      setStep('done')
    } catch {
      setStep('error')
    }
  }

  return (
    <aside className="flex w-[300px] shrink-0 flex-col border-l border-line">
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <span className="label">Contribute</span>
        <button onClick={onClose} className="font-mono text-[13px] text-dim hover:text-ink">
          ✕
        </button>
      </div>

      <div className="flex flex-1 flex-col items-center justify-center gap-8 px-8 py-12 text-center">
        {step === 'idle' && (
          <>
            <div className="space-y-1.5">
              <p className="font-sans text-[15px] font-medium text-ink">Pool your Mac</p>
              <p className="font-sans text-[13px] leading-relaxed text-dim">
                Share idle compute, earn credits, appear on the map.
              </p>
            </div>
            <button
              onClick={download}
              className="rounded-full bg-signal px-7 py-2.5 font-sans text-[14px] text-bg transition-opacity hover:opacity-90"
            >
              Download for Mac
            </button>
            <p className="font-mono text-[11px] text-faint">macOS · Apple Silicon</p>
          </>
        )}

        {step === 'downloading' && (
          <p className="font-sans text-[14px] text-dim">Downloading…</p>
        )}

        {step === 'error' && (
          <div className="space-y-3 text-center">
            <p className="font-sans text-[14px] text-warn">Download failed.</p>
            <button
              onClick={() => setStep('idle')}
              className="font-mono text-[11px] text-dim hover:text-ink"
            >
              ← try again
            </button>
          </div>
        )}

        {step === 'done' && (
          <div className="space-y-5">
            <p className="font-sans text-[15px] font-medium text-ink">Two files downloaded</p>
            <ol className="space-y-3 text-left">
              <li className="flex gap-3">
                <span className="label mt-0.5 shrink-0">01</span>
                <p className="font-sans text-[13px] leading-relaxed text-dim">
                  Keep both files in the same folder (your Downloads is fine).
                </p>
              </li>
              <li className="flex gap-3">
                <span className="label mt-0.5 shrink-0">02</span>
                <p className="font-sans text-[13px] leading-relaxed text-dim">
                  Double-click <strong className="text-ink">dots-worker</strong>.
                </p>
              </li>
              <li className="flex gap-3">
                <span className="label mt-0.5 shrink-0">03</span>
                <p className="font-sans text-[13px] leading-relaxed text-dim">
                  If Mac asks for permission, right-click it and choose{' '}
                  <strong className="text-ink">Open</strong>.
                </p>
              </li>
            </ol>
            <button
              onClick={() => setStep('idle')}
              className="font-mono text-[11px] text-dim hover:text-ink"
            >
              ← download again
            </button>
          </div>
        )}
      </div>
    </aside>
  )
}
