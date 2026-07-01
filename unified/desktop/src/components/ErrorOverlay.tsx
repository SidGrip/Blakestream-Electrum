import { useEffect } from 'react'

// Centered, self-dismissing overlay. Floats over its nearest positioned ancestor (give the form
// `position: relative`) and fades out across its 4s lifetime; it calls onDismiss after 4s so the parent
// clears its own state. `tone` switches red (error, default) vs green (success). Long content wraps and
// the box is height-capped so a stray long string can't bleed off-screen.
export default function ErrorOverlay({
  message,
  onDismiss,
  tone = 'error',
}: {
  message: string | null
  onDismiss: () => void
  tone?: 'error' | 'success'
}) {
  useEffect(() => {
    if (!message) return
    const id = setTimeout(onDismiss, 4000)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [message])

  if (!message) return null
  const palette = tone === 'success'
    ? { color: '#bdf0cf', bg: 'rgba(16,38,24,0.94)', border: 'rgba(95,211,138,0.55)' }
    : { color: '#ffd2cf', bg: 'rgba(38,18,18,0.94)', border: 'rgba(239,83,80,0.55)' }
  return (
    <div
      style={{
        position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
        justifyContent: 'center', pointerEvents: 'none', zIndex: 6, padding: 16,
      }}
    >
      <div
        className="error-overlay"
        style={{
          maxWidth: 'min(560px, 92%)', maxHeight: '80%', overflow: 'hidden', textAlign: 'center',
          color: palette.color, fontSize: 14, fontWeight: 600, lineHeight: 1.5,
          whiteSpace: 'pre-line', overflowWrap: 'anywhere', wordBreak: 'break-word',
          background: palette.bg, border: `1px solid ${palette.border}`,
          borderRadius: 12, padding: '12px 18px',
          boxShadow: '0 12px 40px rgba(0,0,0,0.55)',
          backdropFilter: 'blur(10px)', WebkitBackdropFilter: 'blur(10px)',
        }}
      >
        {message}
      </div>
    </div>
  )
}
