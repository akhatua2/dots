import { Icon } from '../components/Icon'

/**
 * Pick one of a few options. It sits in the page (no floating menu), and the
 * chosen row gets the same orange tint as the other selected states.
 */
export function OptionList({ options, value, onChange }: { options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <ul role="radiogroup" className="overflow-hidden rounded-lg border border-line bg-input">
      {options.map((option) => {
        const selected = option === value
        return (
          <li key={option}>
            <button
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(option)}
              className={`flex w-full items-center justify-between px-2.5 py-1.5 text-left text-[13px] transition-colors ${
                selected ? 'bg-signal-soft text-signal-text' : 'text-[#cfcfcf] hover:bg-active/60'
              }`}
            >
              {option}
              {selected && <Icon name="check" size={13} strokeWidth={2.4} />}
            </button>
          </li>
        )
      })}
    </ul>
  )
}
