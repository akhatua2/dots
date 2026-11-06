import { useState } from 'react'
import { store } from '../../lib/store'
import type { GatewayKey } from '../../lib/bridge'
import type { Catalog } from '../../lib/pools'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'
import { SectionLabel } from '../../ui/SectionLabel'
import { TextField } from '../../ui/TextField'
import { Toggle } from '../../components/Toggle'

const SCOPE_TITLES: Record<string, string> = { pools: 'Your pools', public: 'Public pools', frontier: 'Frontier models' }

/** "Everything", "Your pools only", or the list, plus the monthly limit when it spends credits. */
function describe(key: GatewayKey, scopeCount: number): string {
  const spends = key.scopes.some((s) => s !== 'pools')
  const reach =
    key.scopes.length === scopeCount
      ? 'Everything'
      : key.scopes.length === 1 && key.scopes[0] === 'pools'
        ? 'Your pools only'
        : key.scopes.map((s) => SCOPE_TITLES[s]).join(', ')
  return spends && key.monthly_limit ? `${reach}, ${key.monthly_limit.toLocaleString('en-US')} credits a month` : reach
}

function usage(key: GatewayKey): string {
  return key.requests === 0 ? 'Not used yet' : `${key.requests.toLocaleString('en-US')} requests, free`
}

export function KeysSection({ keys, catalog }: { keys: GatewayKey[]; catalog: Catalog }) {
  const [creating, setCreating] = useState(false)
  const [secret, setSecret] = useState<string | null>(null)

  return (
    <section className="mt-7">
      <div className="flex items-center justify-between">
        <SectionLabel>Keys</SectionLabel>
        {!creating && (
          <Button variant="primary" onClick={() => setCreating(true)}>
            + Create key
          </Button>
        )}
      </div>

      {creating && (
        <CreateKeyForm
          catalog={catalog}
          onCancel={() => setCreating(false)}
          onCreated={(value) => {
            setCreating(false)
            setSecret(value)
          }}
        />
      )}
      {secret && <NewSecret secret={secret} onDone={() => setSecret(null)} />}

      <Card className="mt-3 divide-y divide-line">
        {keys.map((key) => (
          <div key={key.id} className="grid grid-cols-[1.5fr_1fr_1fr_64px] items-center gap-4 px-[18px] py-3">
            <div className="min-w-0">
              <p className="text-[15.5px] font-semibold leading-tight">{key.name}</p>
              <p className="text-[13px] text-dim">{describe(key, catalog.key_scopes.length)}</p>
            </div>
            <p className="font-mono text-[13px] text-[#cfcfcf]">dots_sk_••••{key.tail}</p>
            <p className="text-[13.5px] text-[#cfcfcf]">{usage(key)}</p>
            <Button variant="ghost" className="justify-self-end" onClick={() => void store.revokeKey(key.id)}>
              Revoke
            </Button>
          </div>
        ))}
        {keys.length === 0 && <p className="px-[18px] py-6 text-center text-[14px] text-dim">No keys yet. Create one for each app you connect.</p>}
      </Card>
    </section>
  )
}

function CreateKeyForm({ catalog, onCancel, onCreated }: { catalog: Catalog; onCancel: () => void; onCreated: (secret: string) => void }) {
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>(catalog.key_scopes.filter((s) => s.default).map((s) => s.id))
  const [limit, setLimit] = useState(String(catalog.default_monthly_limit))
  const spends = scopes.some((id) => id !== 'pools')

  const toggle = (id: string, on: boolean) => setScopes(on ? [...scopes, id] : scopes.filter((s) => s !== id))

  async function create() {
    const secret = await store.createKey(name.trim(), scopes, spends ? Number(limit) || null : null)
    onCreated(secret)
  }

  return (
    <Card className="mt-3 p-5">
      <p className="text-[16px] font-semibold">Create an API key</p>
      <p className="text-[13.5px] text-dim">For any app that speaks the OpenAI API.</p>
      <TextField autoFocus className="mt-4" value={name} onChange={(e) => setName(e.target.value)} placeholder="My scripts" />

      <p className="label mt-5 !text-[10.5px]">What it can reach</p>
      <ul className="mt-2 space-y-3">
        {catalog.key_scopes.map((scope) => (
          <li key={scope.id} className="flex items-center justify-between">
            <div>
              <p className="text-[14.5px] font-medium">{scope.title}</p>
              <p className="text-[13px] text-dim">{scope.body}</p>
            </div>
            <Toggle label={scope.title} on={scopes.includes(scope.id)} onChange={(on) => toggle(scope.id, on)} />
          </li>
        ))}
      </ul>

      {spends && (
        <label className="mt-4 flex items-center justify-between text-[14.5px] font-medium">
          Monthly limit
          <TextField className="!w-28 text-right" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ''))} />
        </label>
      )}

      <div className="mt-5 flex justify-end gap-3">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" onClick={() => void create()}>
          Create key
        </Button>
      </div>
    </Card>
  )
}

/** The one time the full key is visible. */
function NewSecret({ secret, onDone }: { secret: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    await navigator.clipboard.writeText(secret)
    setCopied(true)
  }

  return (
    <Card className="mt-3 border-signal/40 p-5">
      <p className="text-[16px] font-semibold">Your new key</p>
      <p className="text-[13.5px] text-dim">Copy it now. You won't see it again.</p>
      <div className="mt-3 flex items-center gap-2 rounded-lg bg-input p-1.5 pl-3.5">
        <span className="min-w-0 flex-1 select-text truncate font-mono text-[13.5px]">{secret}</span>
        <Button variant={copied ? 'secondary' : 'primary'} onClick={() => void copy()}>
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <div className="mt-4 flex justify-end">
        <Button onClick={onDone}>Done</Button>
      </div>
    </Card>
  )
}
