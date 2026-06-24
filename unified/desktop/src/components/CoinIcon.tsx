import { COIN_ICONS } from '../coinIcons'
import { COIN_COLORS } from '../types'

// The coin's icon image, sized to `size` px. Falls back to the brand-colored dot
// for any ticker without an image so the UI never breaks on an unknown coin.
export default function CoinIcon({ ticker, size = 24 }: { ticker: string; size?: number }) {
  const src = COIN_ICONS[ticker]
  if (src) {
    return (
      <img
        src={src}
        alt={ticker}
        width={size}
        height={size}
        style={{ flex: '0 0 auto', display: 'block', objectFit: 'contain' }}
      />
    )
  }
  const color = COIN_COLORS[ticker] ?? '#8a929b'
  return (
    <span
      aria-hidden
      style={{ width: 10, height: 10, borderRadius: '50%', background: color, flex: '0 0 auto' }}
    />
  )
}
