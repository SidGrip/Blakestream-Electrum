import { useEffect } from 'react'
import { useStore } from './store'
import { resolveCoinColor } from './types'
import { hexToRgb, getReadableText } from './coinTheme'
import CoinSidebar from './components/CoinSidebar'
import PortfolioDonut from './components/PortfolioDonut'
import CoinDetail from './components/CoinDetail'
import Setup from './components/Setup'
import LaunchScreen from './components/LaunchScreen'
import Toasts from './components/Toasts'

export default function App() {
  const startPolling = useStore((s) => s.startPolling)
  const stopPolling = useStore((s) => s.stopPolling)
  const refresh = useStore((s) => s.refresh)
  const error = useStore((s) => s.error)
  const onboarded = useStore((s) => s.onboarded)
  const initialChecked = useStore((s) => s.initialChecked)
  const connectError = useStore((s) => s.connectError)
  const selected = useStore((s) => s.selected)
  const coinColorOverrides = useStore((s) => s.coinColorOverrides)

  // The effective brand color of the focused coin (user override wins). Depending the
  // retint effect on this hex means it re-runs both when the selection changes AND when
  // the user picks a new color for the current coin — so the picker retints live.
  // Only the dashboard follows a coin color; the launch + setup screens stay on the neutral
  // teal accent (otherwise they'd flash to BLC's color the moment the daemons report).
  const selectedHex = initialChecked && onboarded && selected
    ? resolveCoinColor(coinColorOverrides, selected)
    : '#4fc3f7'

  useEffect(() => {
    // Initial fetch + 8s polling loop; clean up on unmount.
    void refresh()
    startPolling(8000)
    return () => stopPolling()
  }, [refresh, startPolling, stopPolling])

  // Retint the glass accent (--coin*) to the selected coin's brand color so
  // primary buttons, tabs and glows follow the focused coin. Falls back to the
  // cyan accent when nothing is selected.
  useEffect(() => {
    const hex = selectedHex
    const root = document.documentElement
    root.style.setProperty('--coin', hex)
    root.style.setProperty('--coin-rgb', hexToRgb(hex))
    root.style.setProperty('--coin-text', getReadableText(hex))
    root.style.setProperty('--coin-glow', hex)
  }, [selectedHex])

  // Until the backend answers /setup/status, show the single launch screen: the coin
  // icons cascade as daemons start, and — when a vault exists — the unlock password +
  // button share that same screen (no second full-screen cascade on relaunch).
  if (!initialChecked) {
    return <LaunchScreen failed={connectError} />
  }
  // First-run: Setup renders create/restore. (Relaunch unlock now lives in LaunchScreen.)
  if (!onboarded) {
    return <Setup />
  }

  // Electrum-style: coin list + holdings donut on the left rail, one coin in
  // focus on the right.
  return (
    <div className="app-shell">
      <aside className="rail">
        <div
          style={{
            padding: '16px 16px 10px',
            borderBottom: '1px solid rgba(255,255,255,0.06)',
            fontSize: 15,
            fontWeight: 700,
            letterSpacing: 1,
            lineHeight: 1.4,
          }}
        >
          BLAKESTREAM
          <br />
          WALLET
        </div>
        <CoinSidebar />
        <PortfolioDonut />
      </aside>
      <main className="main">
        {error && (
          <div className="error-banner" role="alert">
            Backend unavailable: {error}
          </div>
        )}
        <CoinDetail />
      </main>
      <Toasts />
    </div>
  )
}
