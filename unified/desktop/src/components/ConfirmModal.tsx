import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { primaryBtn, secondaryBtn } from './uiKit'

// App-wide themed replacement for window.confirm(): a frosted-glass modal matching the wallet
// instead of the OS dialog. Mount <ConfirmProvider> once at the root; call useConfirm() anywhere
// to get an async confirm(opts) => Promise<boolean>.

export interface ConfirmOptions {
  title?: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'default' | 'danger'
}

const ConfirmContext = createContext<(opts: ConfirmOptions) => Promise<boolean>>(
  () => Promise.resolve(false),
)

export function useConfirm() {
  return useContext(ConfirmContext)
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null)
  const resolver = useRef<((ok: boolean) => void) | null>(null)

  const confirm = useCallback((next: ConfirmOptions) => {
    setOpts(next)
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve
    })
  }, [])

  const close = useCallback((ok: boolean) => {
    resolver.current?.(ok)
    resolver.current = null
    setOpts(null)
  }, [])

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {opts && <ConfirmDialog opts={opts} onClose={close} />}
    </ConfirmContext.Provider>
  )
}

function ConfirmDialog({ opts, onClose }: { opts: ConfirmOptions; onClose: (ok: boolean) => void }) {
  const danger = opts.tone === 'danger'
  const cancelRef = useRef<HTMLButtonElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    // Default focus to the safe choice for destructive prompts, the primary otherwise.
    ;(danger ? cancelRef : confirmRef).current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(false) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [danger, onClose])

  const confirmStyle: React.CSSProperties = danger
    ? {
        ...secondaryBtn,
        background: 'rgba(239,83,80,0.16)',
        border: '1px solid rgba(239,83,80,0.55)',
        color: '#ffd2cf',
        fontWeight: 700,
      }
    : primaryBtn

  return (
    <div
      className="confirm-backdrop"
      style={{
        position: 'fixed', inset: 0, zIndex: 1000, padding: 20,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(6,8,10,0.55)',
        backdropFilter: 'blur(4px)', WebkitBackdropFilter: 'blur(4px)',
      }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(false) }}
    >
      <div
        className="confirm-card"
        role="alertdialog"
        aria-modal="true"
        style={{
          width: 'min(420px, 94%)',
          background: 'rgba(28,32,37,0.96)',
          border: '1px solid rgba(255,255,255,0.13)',
          borderRadius: 14,
          boxShadow: '0 20px 60px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.10)',
          backdropFilter: 'blur(20px) saturate(160%)', WebkitBackdropFilter: 'blur(20px) saturate(160%)',
          padding: '20px 22px 18px',
          color: '#e6e6e6',
        }}
      >
        {opts.title && (
          <div style={{ fontSize: 15, fontWeight: 700, color: '#eef2f8', marginBottom: 8 }}>
            {opts.title}
          </div>
        )}
        <div style={{ fontSize: 14, lineHeight: 1.55, color: '#c3c9d1', whiteSpace: 'pre-line' }}>
          {opts.message}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 20 }}>
          <button ref={cancelRef} type="button" style={secondaryBtn} onClick={() => onClose(false)}>
            {opts.cancelLabel ?? 'Cancel'}
          </button>
          <button ref={confirmRef} type="button" style={confirmStyle} onClick={() => onClose(true)}>
            {opts.confirmLabel ?? 'OK'}
          </button>
        </div>
      </div>
    </div>
  )
}
