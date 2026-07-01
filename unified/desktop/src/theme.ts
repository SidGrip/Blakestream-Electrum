// Palette constants for the dark Blakestream identity. Mirrors styles.css so
// component code (Recharts strokes, inline SVG, etc.) can reference colors in TS.

export const theme = {
  background: '#1a1d21',
  panel: '#22262b',
  border: '#2e333a',
  text: '#e6e6e6',
  muted: '#8a929b',
  accent: '#4fc3f7',
  positive: '#4caf50',
  negative: '#ef5350',
} as const

export type ThemeColor = keyof typeof theme

// Convenience individual exports.
export const BACKGROUND = theme.background
export const PANEL = theme.panel
export const BORDER = theme.border
export const TEXT = theme.text
export const MUTED = theme.muted
export const ACCENT = theme.accent
export const POSITIVE = theme.positive
export const NEGATIVE = theme.negative
