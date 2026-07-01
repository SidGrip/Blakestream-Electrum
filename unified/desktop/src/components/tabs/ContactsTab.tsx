import { useState } from 'react'
import { addContact, removeContact } from '../../api'
import type { Contact } from '../../types'
import { useStore } from '../../store'
import { lbl, input, primaryBtn, errBox, card } from '../uiKit'
import EditableLabel from '../EditableLabel'

// Per-coin address book; "Pay" hands the address up to CoinDetail to prefill Send.
export default function ContactsTab({
  coin,
  onPay,
}: {
  coin: string
  onPay: (address: string) => void
}) {
  const contacts = useStore((s) => s.contacts)
  const loadContacts = useStore((s) => s.loadContacts)
  const addOpen = useStore((s) => s.contactsAddOpen)
  const setAddOpen = useStore((s) => s.setContactsAddOpen)
  const coinContacts = contacts.filter((c) => c.coin === coin)

  const [label, setLabel] = useState('')
  const [address, setAddress] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)

  const add = async () => {
    setError(null)
    const addr = address.trim()
    const lab = label.trim()
    if (!addr) return setError('Enter an address.')
    setBusy(true)
    try {
      await addContact(coin, addr, lab)
      await loadContacts()
      setLabel('')
      setAddress('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  // Pasting an already-saved address surfaces its label so the user edits rather than re-adds.
  const onAddressChange = (v: string) => {
    setAddress(v)
    const match = coinContacts.find((c) => c.address === v.trim())
    if (match) setLabel(match.label || '')
  }

  const del = async (id: string) => {
    try {
      await removeContact(id)
      await loadContacts()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  // addContact upserts on (coin, address), so re-adding renames the existing contact in place.
  const saveLabel = async (c: Contact, next: string) => {
    setError(null)
    try {
      await addContact(c.coin, c.address, next.trim())
      await loadContacts()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      throw e // let EditableLabel revert its optimistic value
    }
  }

  const copy = (addr: string) => {
    void navigator.clipboard?.writeText(addr)
    setCopied(addr)
    setTimeout(() => setCopied((c) => (c === addr ? null : c)), 1500)
  }

  return (
    <section
      style={{
        ...card,
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        // Header stays put; only the list below scrolls.
        flex: '0 1 auto',
        minHeight: 0,
        maxHeight: '100%',
        overflow: 'hidden',
      }}
    >
      {/* Open/closed state lives in the store so it survives navigation but resets on restart. */}
      <div style={{ flex: '0 0 auto' }}>
        <button
          type="button"
          onClick={() => setAddOpen(!addOpen)}
          style={{
            display: 'flex', alignItems: 'center', gap: 8, width: '100%',
            background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
            color: '#e6e6e6', fontSize: 12, fontWeight: 700,
          }}
        >
          <span
            aria-hidden
            style={{
              display: 'inline-block', width: 10, color: '#8a929b',
              transition: 'transform .15s', transform: addOpen ? 'rotate(90deg)' : 'none',
            }}
          >
            ▸
          </span>
          ＋ Add contact
        </button>
        {addOpen && (
          <div style={{ marginTop: 10 }}>
            <label style={lbl}>Label</label>
            <input
              style={input}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Exchange deposit"
              autoComplete="off"
            />
            <label style={lbl}>{coin} address</label>
            <input
              style={input}
              value={address}
              onChange={(e) => onAddressChange(e.target.value)}
              placeholder={`${coin.toLowerCase()}1…`}
              autoComplete="off"
              spellCheck={false}
            />
            {error && <div style={errBox}>{error}</div>}
            <button
              style={{ ...primaryBtn, marginTop: 14 }}
              disabled={busy}
              onClick={() => void add()}
            >
              {busy ? 'Adding…' : 'Add'}
            </button>
          </div>
        )}
      </div>

      {/* Contact list */}
      <div style={{ flex: '0 1 auto', minHeight: 0, overflowY: 'auto', borderTop: '1px solid #2e333a', paddingTop: 12 }}>
        {coinContacts.length === 0 ? (
          <div style={{ color: '#8a929b', fontSize: 13, padding: '8px 0' }}>
            No saved contacts yet.
          </div>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {coinContacts.map((c) => (
              <li
                key={c.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '10px 0',
                  borderBottom: '1px solid #2e333a',
                }}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  {/* Click name to rename: Enter/blur saves, Escape cancels. */}
                  <div style={{ fontWeight: 600, fontSize: 13 }}>
                    <EditableLabel value={c.label} placeholder="add label" onSave={(next) => saveLabel(c, next)} />
                  </div>
                  <div
                    style={{
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                      fontSize: 11,
                      color: '#8a929b',
                      wordBreak: 'break-all',
                    }}
                  >
                    {c.address}
                  </div>
                </div>
                <button
                  type="button"
                  style={{
                    ...pill,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    minWidth: 80,
                    padding: '4px 10px',
                    boxSizing: 'border-box',
                    outline: 'none',
                    transition: 'color .15s, background .15s, border-color .15s',
                    ...(copied === c.address
                      ? { color: '#7fe0a3', border: '1px solid rgba(95,211,138,0.55)', background: 'rgba(95,211,138,0.14)' }
                      : null),
                  }}
                  onClick={() => copy(c.address)}
                >
                  {copied === c.address ? 'Copied ✓' : 'Copy'}
                </button>
                <button type="button" style={pill} onClick={() => onPay(c.address)}>
                  Pay
                </button>
                <button
                  type="button"
                  aria-label="Delete contact"
                  style={{ ...pill, color: '#ef5350' }}
                  onClick={() => void del(c.id)}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

    </section>
  )
}

const pill: React.CSSProperties = {
  padding: '4px 10px',
  fontSize: 11,
  borderRadius: 6,
  border: '1px solid #2e333a',
  background: '#1a1d21',
  color: '#cfd4da',
  flex: '0 0 auto',
}
