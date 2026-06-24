import { useStore } from '../store'

// Bottom-right transient notifications (e.g. the health-failover "switched server ✓"). Each
// auto-dismisses; click to dismiss early.
export default function Toasts() {
  const toasts = useStore((s) => s.toasts)
  const dismiss = useStore((s) => s.dismissToast)
  if (toasts.length === 0) return null
  return (
    <div
      style={{
        position: 'fixed',
        right: 16,
        bottom: 16,
        zIndex: 2000,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        alignItems: 'flex-end',
        pointerEvents: 'none',
      }}
    >
      {toasts.map((t) => {
        const accent =
          t.kind === 'success' ? 'rgba(95,211,138,0.55)'
            : t.kind === 'warn' ? 'rgba(224,162,58,0.55)' : 'rgba(255,255,255,0.16)'
        const fg = t.kind === 'success' ? '#7fe0a3' : t.kind === 'warn' ? '#e0a23a' : '#e6e6e6'
        return (
          <div
            key={t.id}
            onClick={() => dismiss(t.id)}
            style={{
              pointerEvents: 'auto',
              background: 'rgba(20,23,27,0.92)',
              backdropFilter: 'blur(12px) saturate(140%)',
              WebkitBackdropFilter: 'blur(12px) saturate(140%)',
              border: `1px solid ${accent}`,
              color: fg,
              borderRadius: 10,
              padding: '9px 14px',
              fontSize: 12,
              maxWidth: 340,
              boxShadow: '0 8px 28px rgba(0,0,0,0.5)',
              cursor: 'pointer',
            }}
          >
            {t.text}
          </div>
        )
      })}
    </div>
  )
}
