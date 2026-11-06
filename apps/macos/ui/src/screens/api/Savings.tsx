import type { ApiExtra } from '../../lib/pools'
import { Card } from '../../ui/Card'

interface Props {
  amount: string
  against: string
  lines: { strong: string; text: string }[]
}

/** What running on your own Macs saved, against sending everything to a frontier model. */
export function Savings({ amount, against, lines }: Props) {
  return (
    <Card className="grid grid-cols-[0.9fr_1.4fr] items-center gap-6 px-[22px] py-[22px]">
      <div>
        <p className="num text-[44px] font-semibold leading-none tracking-[-0.03em]">{amount}</p>
        <p className="mt-2 text-[14px] text-dim">saved against sending everything to {against}</p>
      </div>
      <ul className="space-y-1.5 text-[14px]">
        {lines.map((line) => (
          <li key={line.strong}>
            <b className="num font-semibold">{line.strong}</b> <span className="text-[#cfcfcf]">{line.text}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function sampleLines(saved: ApiExtra['saved']) {
  return [
    { strong: `${saved.requests} requests`, text: `${saved.free_pct}% ran free, on this Mac or in your pools` },
    { strong: `${saved.credits_spent} credits`, text: 'spent on public pools and the gateway' },
    { strong: saved.billed, text: 'billed to your own key' },
    { strong: saved.with_counts, text: 'with token counts from the model itself' },
  ]
}
