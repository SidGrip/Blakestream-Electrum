import { useEffect, useRef, useState } from 'react'
import { input } from './uiKit'

// Inline, click-to-edit label cell. Renders the value as text with a subtle hover
// affordance (faint underline + a tiny pencil); clicking swaps in a compact glass input
// pre-filled with the value. Enter / blur saves (optimistically), Escape cancels, and an
// unchanged value is never saved. View and edit modes share the same reserved height so
// the row never jumps. Styled to match the app's `input` but smaller (fontSize 12).
export default function EditableLabel({
  value,
  placeholder = 'add label',
  onSave,
}: {
  value: string
  placeholder?: string
  onSave: (next: string) => void | Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [shown, setShown] = useState(value) // optimistic display value
  const [hover, setHover] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // Keep the displayed value in sync when the parent's value changes (e.g. a refresh
  // re-pulls authoritative labels), unless the user is mid-edit.
  useEffect(() => {
    if (!editing) setShown(value)
  }, [value, editing])

  const begin = () => {
    setDraft(shown)
    setEditing(true)
  }

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  const commit = () => {
    if (!editing) return
    const next = draft.trim()
    setEditing(false)
    if (next === shown) return // unchanged — don't save
    setShown(next) // optimistic
    void Promise.resolve(onSave(next)).catch(() => {
      /* revert on failure; a refresh would re-pull authoritative labels anyway */
      setShown(value)
    })
  }

  const cancel = () => {
    setDraft(shown)
    setEditing(false)
  }

  // Reserved height keeps view/edit modes the same vertical size (no layout jump).
  const minHeight = 24

  if (editing) {
    return (
      <input
        ref={inputRef}
        style={{
          ...input,
          padding: '3px 8px',
          fontSize: 12,
          borderRadius: 6,
          height: minHeight,
          width: '100%',
          minWidth: 0,
          boxSizing: 'border-box',
        }}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            cancel()
          }
        }}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
      />
    )
  }

  const empty = !shown
  return (
    <span
      role="button"
      tabIndex={0}
      onClick={begin}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          begin()
        }
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        minHeight,
        maxWidth: '100%',
        cursor: 'text',
        color: empty ? '#8a929b' : '#e6e6e6',
        borderBottom: hover ? '1px dashed rgba(255,255,255,0.28)' : '1px dashed transparent',
        outline: 'none',
      }}
    >
      <span
        style={{
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          fontStyle: empty ? 'italic' : undefined,
        }}
      >
        {empty ? placeholder : shown}
      </span>
      <span
        aria-hidden
        style={{
          fontSize: 11,
          color: 'var(--coin)',
          opacity: hover ? 0.85 : 0,
          transition: 'opacity .15s',
          flexShrink: 0,
        }}
      >
        ✎
      </span>
    </span>
  )
}
