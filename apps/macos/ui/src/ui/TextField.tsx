import type { InputHTMLAttributes } from 'react'

/** The app's text input: dark well, no border, orange caret. */
export function TextField({ className = '', ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      spellCheck={false}
      autoComplete="off"
      {...rest}
      className={`w-full rounded-lg bg-input px-3 py-2 text-[13.5px] placeholder:text-faint ${className}`}
    />
  )
}
