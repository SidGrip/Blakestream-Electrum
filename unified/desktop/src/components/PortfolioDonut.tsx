import { useMemo } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import { resolveCoinColor } from '../types'
import { useStore } from '../store'
import { formatFiat } from '../explorer'

const NEUTRAL = '#2e333a'
const TEXT = '#e6e6e6'
const MUTED = '#8a929b'

const SIZE = 150
const INNER = 46
const OUTER = 66

interface Slice {
  ticker: string
  value: number
  color: string
}

type DonutTooltipProps = {
  active?: boolean
  payload?: Array<{ payload?: Slice & { percent: number } }>
}

function toNumber(v: string | null | undefined): number {
  if (v == null) return 0
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function DonutTooltip({ active, payload }: DonutTooltipProps) {
  if (!active || !payload || payload.length === 0) return null
  const datum = payload[0]?.payload as (Slice & { percent: number }) | undefined
  if (!datum) return null
  return (
    <div
      style={{
        background: 'rgba(20,23,27,0.85)',
        backdropFilter: 'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
        border: '1px solid rgba(255,255,255,0.10)',
        borderRadius: 8,
        padding: '6px 10px',
        color: TEXT,
        fontSize: 12,
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ color: datum.color, fontWeight: 600 }}>{datum.ticker}</span>
      <span style={{ color: MUTED, marginLeft: 8 }}>{datum.percent.toFixed(1)}%</span>
    </div>
  )
}

/**
 * Holdings allocation donut. Slices are weighted by fiat value only when every held coin is
 * priced AND in fiat view (else by raw amount), so the split never mixes fiat and raw amounts.
 */
export default function PortfolioDonut() {
  const portfolio = useStore((s) => s.portfolio)
  const overrides = useStore((s) => s.coinColorOverrides)
  const balanceView = useStore((s) => s.balanceView)
  const fiatCurrency = useStore((s) => s.fiatCurrency)
  const open = useStore((s) => s.allocationOpen)
  const setOpen = useStore((s) => s.setAllocationOpen)

  const { slices, total, anyFiat } = useMemo(() => {
    const empty = { slices: [] as Slice[], total: 0, fiatWeighted: false, anyFiat: false }
    if (!portfolio) return empty

    const entries = Object.entries(portfolio.coins ?? {})
    const positiveAmount = entries.filter(([, c]) => toNumber(c.amount) > 0)
    // Weight by fiat only when every held coin is priced AND in fiat — else the split would mix units.
    const allPriced =
      positiveAmount.length > 0 &&
      positiveAmount.every(([, c]) => c.value_fiat != null && toNumber(c.value_fiat) > 0)
    const allFiat = balanceView === 'fiat'
    const useFiat = allFiat && allPriced
    const anyFiat = allFiat

    const built: Slice[] = entries.map(([ticker, c]) => ({
      ticker,
      value: useFiat ? toNumber(c.value_fiat) : toNumber(c.amount),
      color: resolveCoinColor(overrides, ticker),
    }))

    const positive = built.filter((s) => s.value > 0)
    const sum = positive.reduce((acc, s) => acc + s.value, 0)

    return { slices: positive, total: sum, fiatWeighted: useFiat, anyFiat }
  }, [portfolio, overrides, balanceView])

  const hasSlices = slices.length > 0 && total > 0

  const chartData = hasSlices
    ? slices.map((s) => ({ ...s, percent: (s.value / total) * 100 }))
    : [{ ticker: '', value: 1, color: NEUTRAL, percent: 0 }]

  return (
    <div
      style={{
        background: 'rgba(34,38,43,0.58)',
        backdropFilter: 'blur(20px) saturate(170%) contrast(108%)',
        WebkitBackdropFilter: 'blur(20px) saturate(170%) contrast(108%)',
        border: '1px solid rgba(255,255,255,0.13)',
        borderRadius: 12,
        boxShadow:
          '0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.15), inset 0 -2px 4px rgba(0,0,0,0.18)',
        padding: 14,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        minWidth: 0,
        flex: '0 0 auto', // keep natural height; the coin list (flex:1) is the only thing that scrolls
      }}
    >
      {anyFiat && (
        <div style={{ borderBottom: '1px solid rgba(255,255,255,0.08)', paddingBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
            <span style={{ color: MUTED, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Total
            </span>
            <span style={{ color: TEXT, fontSize: 13, fontWeight: 700 }}>
              ≈ {formatFiat(portfolio?.total?.value_fiat ?? null, fiatCurrency)}
              {(portfolio?.unpriced?.length ?? 0) > 0 && (
                <span style={{ color: MUTED, fontWeight: 400, fontSize: 11, marginLeft: 4 }}>(partial)</span>
              )}
            </span>
          </div>
        </div>
      )}

      {/* Collapsible header: chevron + "Allocation"; collapsed by default, remembered. */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        title={open ? 'Hide allocation' : 'Show allocation'}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, width: '100%',
          background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
          color: MUTED, fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase',
          font: 'inherit', textAlign: 'left',
        }}
      >
        <span style={{ display: 'inline-block', width: 10, transition: 'transform .15s', transform: open ? 'rotate(90deg)' : 'none' }}>▸</span>
        Allocation
      </button>

      {open && (
        <>
          <div style={{ position: 'relative', width: '100%', height: SIZE }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                {hasSlices && <Tooltip content={<DonutTooltip />} cursor={false} />}
                <Pie
                  data={chartData}
                  dataKey="value"
                  nameKey="ticker"
                  innerRadius={INNER}
                  outerRadius={OUTER}
                  paddingAngle={hasSlices ? 2 : 0}
                  stroke="none"
                  isAnimationActive={false}
                >
                  {chartData.map((d, i) => (
                    <Cell key={`${d.ticker}-${i}`} fill={d.color} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
          </div>

          {hasSlices ? (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 12px' }}>
              {chartData.map((d) => (
                <div key={d.ticker} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, minWidth: 0 }}>
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: d.color,
                      flex: '0 0 auto',
                    }}
                  />
                  <span style={{ color: TEXT }}>{d.ticker}</span>
                  <span style={{ color: MUTED }}>{d.percent.toFixed(1)}%</span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: MUTED, fontSize: 12, textAlign: 'center' }}>no holdings yet</div>
          )}
        </>
      )}
    </div>
  )
}
