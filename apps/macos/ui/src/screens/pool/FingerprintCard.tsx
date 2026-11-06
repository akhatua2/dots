import { useRef, useState } from 'react'
import type { Pool } from '../../lib/pools'
import { Fingerprint } from '../../components/Fingerprint'
import { Icon } from '../../components/Icon'
import { Stat } from '../../ui/Stat'

/** The pool's contribution fingerprint: one night per ring, drawn live. */
export function FingerprintCard({ pool, live }: { pool: Pool; live: boolean }) {
  const detail = pool.detail
  const box = useRef<HTMLDivElement>(null)
  const [drawn, setDrawn] = useState(0) // bumping this redraws the animation

  function savePng() {
    const canvas = box.current?.querySelector('canvas')
    if (!canvas) return
    const link = document.createElement('a')
    link.download = `${pool.id}-fingerprint.png`
    link.href = canvas.toDataURL('image/png')
    link.click()
  }

  return (
    <section className="flex flex-col rounded-[20px] border border-line bg-[#0c0c0c] p-5">
      <div ref={box} className="relative grid flex-1 place-items-center">
        <Fingerprint key={drawn} seed={pool.id} size={340} rings={30} fan={0.55 + (detail?.nights ?? 0) / 40} animate />
        <div className="absolute text-center">
          <p className="text-[40px] font-light leading-none tracking-[-0.02em]">{detail?.nights ?? 0}</p>
          <p className="mt-2 text-[10px] font-semibold tracking-[0.28em] text-dim">NIGHTS</p>
        </div>
      </div>

      {detail && (
        <div className="mt-4 flex border-t border-line pt-4">
          <Stat value={String(detail.runs_tonight)} label="runs tonight" />
          <Stat value={detail.tokens} label="tokens" />
          <Stat value={String(detail.tps_live)} label="tokens a second" />
        </div>
      )}

      <div className="mt-3 flex items-center justify-between text-[13px] text-dim">
        <span className="flex items-center gap-2">
          <span className={`size-1.5 rounded-full ${live ? 'bg-signal' : 'bg-faint'}`} />
          No. {detail?.live_no ?? 1} · {live ? 'live' : 'paused'}
        </span>
        <span className="flex items-center gap-3">
          <button onClick={savePng} aria-label="Save fingerprint" className="hover:text-ink">
            <Icon name="download" size={16} />
          </button>
          <button onClick={() => setDrawn((n) => n + 1)} aria-label="Replay" className="hover:text-ink">
            <Icon name="replay" size={16} />
          </button>
        </span>
      </div>
    </section>
  )
}
