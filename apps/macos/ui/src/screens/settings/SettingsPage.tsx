import type { ReactNode } from 'react'
import { store, type BridgeState } from '../../lib/store'
import { Toggle } from '../../components/Toggle'
import { Card } from '../../ui/Card'
import { SectionLabel } from '../../ui/SectionLabel'

export function SettingsPage({ s }: { s: BridgeState }) {
  const lend = s.lend
  const glass = s.local.glass ?? s.config?.glass ?? true

  return (
    <div className="max-w-[640px] px-[34px] pb-10 pt-[38px]">
      <h1 className="text-[25px] font-bold leading-[1.15] tracking-[-0.025em]">Settings</h1>

      <SectionLabel className="mt-7">Look</SectionLabel>
      <Card className="mt-3 divide-y divide-line">
        <Row title="Glass" body="Let the desktop show through the sidebar and panels.">
          <Toggle label="Glass" on={glass} onChange={(on) => store.setGlass(on)} />
        </Row>
      </Card>

      {lend && (
        <>
          <SectionLabel className="mt-7">Lending</SectionLabel>
          <Card className="mt-3 divide-y divide-line">
            <Row title="Lend this Mac" body="Closing the window keeps lending going from the menu bar.">
              <Toggle variant="accent" label="Lend this Mac" on={lend.enabled} onChange={(on) => void store.lendSet({ enabled: on })} />
            </Row>
            <Row title="Only when plugged in" body="Pause on battery.">
              <Toggle label="Only when plugged in" on={lend.plugged_only} onChange={(on) => void store.lendSet({ plugged_only: on })} />
            </Row>
            <Row title="Pause while I'm using it" body="Resume when you step away.">
              <Toggle label="Pause while in use" on={lend.pause_in_use} onChange={(on) => void store.lendSet({ pause_in_use: on })} />
            </Row>
          </Card>
          <p className="mt-3 text-[13px] text-dim">
            {lend.reserve_gb} GB of this Mac's {lend.device.gb} GB always stays yours.
          </p>
        </>
      )}
    </div>
  )
}

function Row({ title, body, children }: { title: string; body: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6 px-[18px] py-3.5">
      <div>
        <p className="text-[15px] font-medium">{title}</p>
        <p className="text-[13px] text-dim">{body}</p>
      </div>
      {children}
    </div>
  )
}
