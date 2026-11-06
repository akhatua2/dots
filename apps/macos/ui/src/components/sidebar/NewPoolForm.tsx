import { useState } from 'react'
import { store } from '../../lib/store'
import { Button } from '../../ui/Button'
import { OptionList } from '../../ui/OptionList'
import { TextField } from '../../ui/TextField'

/** Inline "Start a Pool" form, shown in the sidebar instead of a dialog. */
export function NewPoolForm({ models, onDone }: { models: string[]; onDone: () => void }) {
  const [name, setName] = useState('')
  const [model, setModel] = useState(models[0] ?? '')

  function create() {
    if (!name.trim()) return
    store.createPool(name.trim(), model)
    onDone()
  }

  return (
    <div className="space-y-2 rounded-lg bg-active/60 p-2.5">
      <TextField
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => (e.key === 'Enter' ? create() : e.key === 'Escape' ? onDone() : undefined)}
        placeholder="Pool name"
      />
      <OptionList options={models} value={model} onChange={setModel} />
      <div className="flex gap-2">
        <Button variant="primary" className="flex-1 !py-1.5 !text-[13px]" onClick={create}>
          Create
        </Button>
        <Button className="flex-1 !py-1.5 !text-[13px]" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  )
}
