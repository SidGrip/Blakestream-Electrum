import { useStore } from '../store'
import type { TabKey } from '../types'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'history', label: 'History' },
  { key: 'send', label: 'Send' },
  { key: 'receive', label: 'Receive' },
  { key: 'addresses', label: 'Addresses' },
  { key: 'contacts', label: 'Contacts' },
  { key: 'lightning', label: 'Lightning' },
  { key: 'tools', label: 'Tools' },
  { key: 'settings', label: '⚙ Settings' },
]

// The coin-detail tabs. Active tab gets the blue highlight; inactive tabs
// are transparent + muted. Reads/writes the shared activeTab in the store.
export default function TabBar() {
  const activeTab = useStore((s) => s.activeTab)
  const setActiveTab = useStore((s) => s.setActiveTab)

  return (
    <div
      role="tablist"
      style={{
        display: 'flex',
        gap: 4,
        padding: 4,
        borderRadius: 12,
        background: 'rgba(20,23,27,0.55)',
        border: '1px solid rgba(255,255,255,0.06)',
        boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.35)',
        backdropFilter: 'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
      }}
    >
      {TABS.map((t) => (
        <TabBtn
          key={t.key}
          active={activeTab === t.key}
          onClick={() => setActiveTab(t.key)}
        >
          {t.label}
        </TabBtn>
      ))}
    </div>
  )
}

export function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      style={{
        flex: 1,
        padding: '8px 0',
        fontWeight: 600,
        fontSize: 13,
        borderRadius: 9,
        border: active
          ? '1px solid var(--coin)'
          : '1px solid rgba(255,255,255,0.05)',
        cursor: 'pointer',
        // Active = coin-tinted GLASS pill (dark enough that a silver-white label reads on every
        // coin, light or dark) instead of a solid coin fill — so the label color is consistent.
        background: active ? 'rgba(var(--coin-rgb),0.26)' : 'rgba(255,255,255,0.04)',
        color: active ? '#f2f5f9' : '#cfd6dd',
        textShadow: active ? '0 1px 2px rgba(0,0,0,0.45)' : undefined,
        boxShadow: active
          ? '0 0 0 3px color-mix(in srgb, var(--coin), transparent 70%), 0 0 12px color-mix(in srgb, var(--coin), transparent 48%)'
          : 'none',
      }}
    >
      {children}
    </button>
  )
}
