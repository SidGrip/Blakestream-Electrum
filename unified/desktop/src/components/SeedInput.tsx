import { useEffect, useRef, useState } from 'react'
import { BIP39_WORDS, BIP39_SET } from '../bip39'

const VALID_COUNTS = [12, 15, 18, 21, 24]
const MAX_SUGGESTIONS = 8

// Electrum-style seed entry: each finished word becomes a chip (green = valid BIP39
// word, red = unknown), with live prefix suggestions for the word being typed. Reports
// the word list + whether it looks like a complete valid-wordlist phrase to the parent;
// the backend still does the authoritative BIP39 checksum check on submit.
export default function SeedInput({
  onChange,
}: {
  onChange: (words: string[], looksValid: boolean) => void
}) {
  const [chips, setChips] = useState<string[]>([])
  const [current, setCurrent] = useState('')
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const prefix = current.toLowerCase().trim()
  const suggestions = prefix
    ? BIP39_WORDS.filter((w) => w.startsWith(prefix)).slice(0, MAX_SUGGESTIONS)
    : []

  const looksValid = VALID_COUNTS.includes(chips.length) && chips.every((w) => BIP39_SET.has(w))
  const allKnown = chips.every((w) => BIP39_SET.has(w))

  useEffect(() => {
    onChange(chips, looksValid)
  }, [chips, looksValid])

  const commit = (word?: string) => {
    // Explicit pick (click/Tab) wins; otherwise commit what was typed, auto-completing
    // only when the prefix is unambiguous (exactly one match), like Electrum.
    let w = word
    if (w === undefined) w = suggestions.length === 1 ? suggestions[0] : current
    w = (w ?? '').toLowerCase().trim()
    if (!w) return
    setChips((c) => [...c, w])
    setCurrent('')
    setSel(0)
  }

  // Click a chip to pull it back into the input for editing (Electrum-like).
  const editChip = (i: number) => {
    setCurrent(chips[i])
    setChips((c) => c.filter((_, idx) => idx !== i))
    setSel(0)
    inputRef.current?.focus()
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.key === ' ' || e.key === 'Enter') && current.trim()) {
      e.preventDefault()
      commit()
    } else if (e.key === 'Tab' && suggestions.length) {
      e.preventDefault()
      commit(suggestions[sel])
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSel((i) => Math.min(i + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSel((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Backspace' && current === '' && chips.length) {
      e.preventDefault()
      setChips((c) => c.slice(0, -1))
    }
  }

  const onPaste = (e: React.ClipboardEvent) => {
    const text = e.clipboardData.getData('text')
    const words = text.trim().toLowerCase().split(/\s+/).filter(Boolean)
    if (words.length > 1) {
      e.preventDefault()
      setChips((c) => [...c, ...words])
      setCurrent('')
      setSel(0)
    }
  }

  return (
    <div style={{ marginBottom: 12 }}>
      <div
        onClick={() => inputRef.current?.focus()}
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 6,
          minHeight: 92,
          padding: 10,
          background: '#1a1d21',
          border: '1px solid #2e333a',
          borderRadius: 8,
          cursor: 'text',
          alignContent: 'flex-start',
        }}
      >
        {chips.map((w, i) => {
          const ok = BIP39_SET.has(w)
          return (
            <span
              key={i}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '3px 8px',
                borderRadius: 6,
                fontSize: 13,
                color: '#fff',
                background: ok ? '#2e7d52' : '#a5403c',
              }}
            >
              <span style={{ color: '#cfe6da', fontSize: 11 }}>{i + 1}.</span>
              <span
                onClick={(e) => {
                  e.stopPropagation()
                  editChip(i)
                }}
                title="click to edit"
                style={{ cursor: 'pointer' }}
              >
                {w}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  setChips((c) => c.filter((_, idx) => idx !== i))
                }}
                aria-label={`remove ${w}`}
                style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.8)', cursor: 'pointer', padding: 0, fontSize: 14, lineHeight: 1 }}
              >
                ×
              </button>
            </span>
          )
        })}
        <input
          ref={inputRef}
          value={current}
          onChange={(e) => {
            setCurrent(e.target.value.replace(/\s/g, ''))
            setSel(0)
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={chips.length === 0 ? 'Type or paste your recovery phrase…' : ''}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          style={{
            flex: 1,
            minWidth: 120,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            color: '#e6e6e6',
            font: 'inherit',
            fontSize: 14,
          }}
        />
      </div>

      {suggestions.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
          {suggestions.map((w, i) => (
            <button
              key={w}
              onMouseDown={(e) => {
                e.preventDefault()
                commit(w)
              }}
              style={{
                padding: '4px 10px',
                borderRadius: 6,
                border: '1px solid #2e333a',
                cursor: 'pointer',
                fontSize: 13,
                background: i === sel ? '#4fc3f7' : '#22262b',
                color: i === sel ? '#1a1d21' : '#cfd4da',
              }}
            >
              {w}
            </button>
          ))}
        </div>
      )}

      <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 11, color: looksValid ? '#4caf50' : '#8a929b' }}>
          {chips.length} word{chips.length === 1 ? '' : 's'}
          {!allKnown
            ? ' — some words aren’t in the BIP39 list (shown in red)'
            : looksValid
              ? ' — looks like a valid recovery phrase ✓'
              : chips.length > 0
                ? ' — keep going (12 / 15 / 18 / 21 / 24 words)'
                : ''}
        </span>
        {chips.length > 0 && (
          <button
            onClick={() => {
              setChips([])
              setCurrent('')
            }}
            style={{ marginLeft: 'auto', background: 'none', border: 'none', color: '#8a929b', cursor: 'pointer', fontSize: 11, textDecoration: 'underline', padding: 0 }}
          >
            Clear all
          </button>
        )}
      </div>
    </div>
  )
}
