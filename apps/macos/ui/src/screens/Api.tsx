import { useState } from 'react'
import { store, type BridgeState } from '../lib/store'
import type { Catalog } from '../lib/pools'
import { Icon } from '../components/Icon'
import { Toggle } from '../components/Toggle'

type Lang = 'curl' | 'Python' | 'JavaScript'

function snippet(lang: Lang, base: string): string {
  const body = `{"model": "auto", "messages": [{"role": "user", "content": "Hi"}]}`
  if (lang === 'curl')
    return `curl ${base}/chat/completions \\\n  -H "Authorization: Bearer $DOTS_API_KEY" \\\n  -d '${body}'`
  if (lang === 'Python')
    return `from openai import OpenAI\n\nclient = OpenAI(base_url="${base}", api_key=os.environ["DOTS_API_KEY"])\nreply = client.chat.completions.create(\n    model="auto",\n    messages=[{"role": "user", "content": "Hi"}],\n)`
  return `import OpenAI from "openai"\n\nconst client = new OpenAI({ baseURL: "${base}", apiKey: process.env.DOTS_API_KEY })\nconst reply = await client.chat.completions.create({\n  model: "auto",\n  messages: [{ role: "user", content: "Hi" }],\n})`
}

export function Api({ s }: { s: BridgeState }) {
  const c = s.catalog
  if (!c) return <p className="p-10 text-dim">Loading</p>
  const base = s.config?.api_base ?? ''
  const [lang, setLang] = useState<Lang>('curl')
  const [creating, setCreating] = useState(false)
  const [secret, setSecret] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  function copy(id: string, text: string) {
    void navigator.clipboard.writeText(text)
    setCopied(id)
    setTimeout(() => setCopied((c) => (c === id ? null : c)), 1600)
  }

  return (
    <div className="px-[34px] pb-9 pt-[38px]">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-[25px] font-bold leading-[1.15] tracking-[-0.025em]">API</h1>
          <p className="mt-1 text-[13.5px] text-dim">
            One endpoint for every model you can reach. Works with anything that speaks the OpenAI API.
          </p>
        </div>
        {c.key_scopes.length > 0 && (
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-2 rounded-[10px] bg-signal px-3.5 py-2 text-[14px] font-semibold text-[#1a0a03]"
        >
          <Icon name="plus" size={16} strokeWidth={2.4} /> Create key
        </button>
        )}
      </div>

      <div className="card mt-5 flex items-center justify-between px-[18px] py-[14px]">
        <div className="min-w-0">
          <p className="label !text-[10.5px]">Base URL</p>
          <p className="mt-1 truncate font-mono text-[15.5px]">{base || '—'}</p>
        </div>
        <button onClick={() => copy('base', base)} className="rounded-lg bg-btn px-4 py-2 text-[14px] font-medium">
          {copied === 'base' ? 'Copied' : 'Copy'}
        </button>
      </div>

      <div className="mt-[22px] flex items-baseline justify-between">
        <p className="label">Keys</p>
        <p className="text-[13.5px] text-dim">
          Use model <span className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[12.5px] text-ink">&quot;auto&quot;</span>{' '}
          and dots picks where each request runs.
        </p>
      </div>
      <div className="card mt-3 divide-y divide-line">
        {s.local.keys.map((k) => (
          <div key={k.id} className="grid grid-cols-[1.4fr_1fr_1fr_56px] items-center gap-4 px-[18px] py-[11px]">
            <div className="min-w-0">
              <p className="flex items-center gap-2.5 text-[16px] font-semibold">
                {k.name}
                {k.isNew && (
                  <span className="rounded-md bg-signal-soft px-2 py-0.5 text-[11px] font-bold tracking-wide text-signal-text">
                    NEW
                  </span>
                )}
              </p>
              <p className="text-[13.5px] text-dim">
                {k.scope}
                {k.meta ? ` · ${k.meta}` : ''}
              </p>
            </div>
            <p className="font-mono text-[13.5px] text-[#cfcfcf]">dots_sk_••••{k.tail}</p>
            <p className="text-[14px] text-[#cfcfcf]">{k.usage}</p>
            {k.id === 'join' ? (
              <span />
            ) : (
              <button onClick={() => store.revokeKey(k.id)} className="text-[14px] text-dim hover:text-ink">
                Revoke
              </button>
            )}
          </div>
        ))}
        {s.local.keys.length === 0 && <p className="px-5 py-6 text-center text-dim">No keys yet.</p>}
      </div>

      {c.request_split.length > 0 && <p className="label mt-[22px] !text-[12px]">Where your requests ran this month</p>}
      <div className="mt-3 flex h-2.5 gap-0.5 overflow-hidden rounded-full">
        {c.request_split.map((r) => (
          <span key={r.label} style={{ width: `${r.pct}%`, background: r.shade }} />
        ))}
      </div>
      <div className="mt-3 flex gap-10 text-[14px]">
        {c.request_split.map((r) => (
          <span key={r.label} className="flex items-center gap-2">
            <span className="size-2.5 rounded-sm" style={{ background: r.shade }} />
            <b className="font-semibold">{r.label}</b>
            <span className="text-dim">
              · {r.pct}% · {r.note}
            </span>
          </span>
        ))}
      </div>

      <div className="card mt-[22px] overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="flex gap-6 text-[14.5px]">
            {(['curl', 'Python', 'JavaScript'] as Lang[]).map((l) => (
              <button
                key={l}
                onClick={() => setLang(l)}
                className={`border-b-2 pb-1.5 pt-1 ${lang === l ? 'border-ink text-ink' : 'border-transparent text-dim'}`}
              >
                {l}
              </button>
            ))}
          </div>
          <button
            onClick={() => copy('code', snippet(lang, base))}
            className="rounded-lg bg-btn px-3.5 py-1.5 text-[13.5px] font-medium"
          >
            {copied === 'code' ? 'Copied' : 'Copy'}
          </button>
        </div>
        <pre className="select-text overflow-x-auto px-[18px] py-[14px] font-mono text-[13.5px] leading-[26px] text-[#cfcfcf]">
          {snippet(lang, base)}
        </pre>
      </div>

      {creating && (
        <CreateKey
          scopes={c.key_scopes}
          defaultLimit={c.default_monthly_limit}
          onClose={() => setCreating(false)}
          onCreated={(key) => {
            setCreating(false)
            setSecret(key)
          }}
        />
      )}
      {secret && (
        <Modal onClose={() => setSecret(null)}>
          <h2 className="text-[19px] font-bold">Your new key</h2>
          <p className="mt-1 text-[14px] text-dim">Copy it now. You won&apos;t see it again.</p>
          <div className="mt-4 flex items-center gap-2 rounded-xl bg-input p-2 pl-4">
            <span className="min-w-0 flex-1 select-text truncate font-mono text-[14px]">{secret}</span>
            <button
              onClick={() => copy('secret', secret)}
              className={`rounded-lg px-4 py-2 text-[14px] font-semibold ${
                copied === 'secret' ? 'bg-btn text-ink' : 'bg-signal text-[#1a0a03]'
              }`}
            >
              {copied === 'secret' ? 'Copied' : 'Copy'}
            </button>
          </div>
          <div className="mt-6 flex justify-end">
            <button onClick={() => setSecret(null)} className="rounded-lg bg-btn px-5 py-2 text-[14px] font-medium">
              Done
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function Modal({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div onClick={onClose} className="fade fixed inset-0 z-40 grid place-items-center bg-black/55">
      <div
        onClick={(e) => e.stopPropagation()}
        className="rise w-[440px] rounded-2xl border border-line-2 bg-[#1e1e1e] p-6 shadow-2xl"
      >
        {children}
      </div>
    </div>
  )
}

function CreateKey({
  scopes: defs,
  defaultLimit,
  onClose,
  onCreated,
}: {
  scopes: Catalog['key_scopes']
  defaultLimit: number
  onClose: () => void
  onCreated: (key: string) => void
}) {
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<Record<string, boolean>>(
    Object.fromEntries(defs.map((x) => [x.id, x.default])),
  )
  const [limit, setLimit] = useState(String(defaultLimit))
  const spends = scopes.public || scopes.frontier

  function create() {
    const on = defs.filter((x) => scopes[x.id])
    const scope =
      on.length === defs.length
        ? 'Everything'
        : on.length === 1 && on[0].id === 'pools'
          ? 'Your pools only'
          : on.map((x) => x.title).join(', ') || 'Nothing'
    const tail = spends && limit ? `, up to ${Number(limit).toLocaleString('en-US')} credits a month` : ''
    onCreated(store.createKey(name.trim(), scope + tail))
  }

  return (
    <Modal onClose={onClose}>
      <h2 className="text-[19px] font-bold">Create an API key</h2>
      <p className="mt-1 text-[14px] text-dim">For any app that speaks the OpenAI API.</p>
      <p className="label mt-5 !text-[10.5px]">Name</p>
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="My scripts"
        className="mt-2 w-full rounded-xl bg-input px-4 py-2.5 text-[15px] outline-none placeholder:text-faint"
      />
      <p className="label mt-5 !text-[10.5px]">What it can reach</p>
      <div className="mt-2 space-y-3.5">
        {defs.map((x) => (
          <div key={x.id} className="flex items-center justify-between">
            <div>
              <p className="text-[15px] font-medium">{x.title}</p>
              <p className="text-[13px] text-dim">{x.body}</p>
            </div>
            <Toggle label={x.title} on={scopes[x.id]} onChange={(v) => setScopes({ ...scopes, [x.id]: v })} />
          </div>
        ))}
      </div>
      {spends && (
        <div className="fade mt-5 flex items-center justify-between">
          <p className="text-[15px] font-medium">Monthly limit</p>
          <input
            value={limit}
            onChange={(e) => setLimit(e.target.value.replace(/\D/g, ''))}
            className="w-28 rounded-lg bg-input px-3 py-2 text-right text-[15px] outline-none"
          />
        </div>
      )}
      <div className="mt-6 flex justify-end gap-3">
        <button onClick={onClose} className="px-4 py-2 text-[14px] text-dim">
          Cancel
        </button>
        <button onClick={create} className="rounded-lg bg-signal px-5 py-2 text-[14px] font-semibold text-[#1a0a03]">
          Create key
        </button>
      </div>
    </Modal>
  )
}
