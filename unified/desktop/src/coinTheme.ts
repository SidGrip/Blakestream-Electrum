// Pure helpers to drive the glassmorphism coin accent. The selected coin's brand
// color (from COIN_COLORS) is pushed onto :root as --coin / --coin-rgb /
// --coin-text / --coin-glow, retinting every coin-tinted glass surface.

/** "#ff9800" -> "255,152,0" for use in rgba(var(--coin-rgb), a). */
export const hexToRgb = (h: string): string => {
  const n = h.replace('#', '')
  return `${parseInt(n.slice(0, 2), 16)},${parseInt(n.slice(2, 4), 16)},${parseInt(
    n.slice(4, 6),
    16,
  )}`
}

/** Readable foreground (dark on light coins, white on dark coins) for --coin-text. */
export const getReadableText = (h: string): string => {
  const n = h.replace('#', '')
  const r = parseInt(n.slice(0, 2), 16)
  const g = parseInt(n.slice(2, 4), 16)
  const b = parseInt(n.slice(4, 6), 16)
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.55 ? '#0e1013' : '#ffffff'
}
