import type { BridgeState } from '../../lib/store'
import { BaseUrlCard } from './BaseUrlCard'
import { KeysSection } from './KeysSection'
import { RecentRequests } from './RecentRequests'
import { RequestSplit } from './RequestSplit'
import { Savings, sampleLines } from './Savings'

export function ApiPage({ s }: { s: BridgeState }) {
  const { gateway, catalog } = s
  if (!gateway || !catalog) return <p className="p-10 text-dim">Starting the local API</p>

  // Until real requests arrive, show the design's sample traffic so the page is never empty.
  const sample = gateway.requests === 0 ? gateway.sample : undefined
  const poolsShare = gateway.requests > 0 ? 100 : 0
  const split = sample?.split ?? [
    { label: 'This Mac', pct: 0, note: 'free', shade: '#f0f0f0' },
    { label: 'Your pools', pct: poolsShare, note: 'free', shade: '#d0d0d0' },
    { label: 'Public pools', pct: 0, note: '0 credits', shade: '#8c8c8c' },
    { label: 'Frontier models', pct: 0, note: '0 credits', shade: '#5e5e5e' },
  ]
  const recent = sample?.recent ?? gateway.recent

  return (
    <div className="px-[34px] pb-10 pt-[38px]">
      <h1 className="text-[25px] font-bold leading-[1.15] tracking-[-0.025em]">API</h1>
      <BaseUrlCard base={gateway.base} running={gateway.running} />
      <KeysSection keys={gateway.keys} catalog={catalog} />

      <section className="mt-7">
        <h2 className="label !text-[12px]">Saved this month</h2>
        <div className="mt-3">
          {sample ? (
            <Savings amount={sample.saved.amount} against={gateway.compare_label} lines={sampleLines(sample.saved)} />
          ) : (
            <Savings
              amount={`$${gateway.saved_usd.toFixed(2)}`}
              against={gateway.compare_label}
              lines={[
                { strong: `${gateway.requests} requests`, text: `${gateway.free_pct}% ran free, on this Mac or in your pools` },
                { strong: `${gateway.requests} of ${gateway.requests}`, text: 'with token counts from the model itself' },
              ]}
            />
          )}
        </div>
      </section>

      <RequestSplit split={split} />
      <RecentRequests rows={recent} />
    </div>
  )
}
