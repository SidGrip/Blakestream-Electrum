// Shared table-cell primitives for the coin-detail tabs (History, Addresses, LN).
// Salvaged from the old AssetsTable so every tab table looks identical.

export function Th({
  children,
  align = 'left',
  width,
  style,
  onClick,
}: {
  children: React.ReactNode
  align?: 'left' | 'right' | 'center'
  width?: string
  style?: React.CSSProperties
  // When set, the header is a clickable sort control (pointer cursor, no text selection).
  onClick?: () => void
}) {
  return (
    <th
      onClick={onClick}
      style={{
        width,
        padding: '10px 16px',
        fontWeight: 600,
        fontSize: 11,
        textTransform: 'uppercase',
        letterSpacing: 0.5,
        textAlign: align,
        color: '#8a929b',
        cursor: onClick ? 'pointer' : undefined,
        userSelect: onClick ? 'none' : undefined,
        ...style,
      }}
    >
      {children}
    </th>
  )
}

export function Td({
  children,
  align = 'left',
  mono = false,
  muted = false,
}: {
  children: React.ReactNode
  align?: 'left' | 'right' | 'center'
  mono?: boolean
  muted?: boolean
}) {
  return (
    <td
      style={{
        padding: '12px 16px',
        textAlign: align,
        color: muted ? '#8a929b' : '#e6e6e6',
        fontVariantNumeric: mono ? 'tabular-nums' : undefined,
        fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
        wordBreak: mono ? 'break-all' : undefined,
      }}
    >
      {children}
    </td>
  )
}
