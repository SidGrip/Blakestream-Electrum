import { Area, AreaChart, ResponsiveContainer, YAxis } from 'recharts'

interface SparklineProps {
  data?: number[] | null
  color: string
}

const WIDTH = 80
const HEIGHT = 28

/**
 * Tiny inline sparkline. When no price data is available (the common case for the
 * six Blakestream coins, which have no market feed yet) it renders a flat dashed
 * baseline placeholder instead of an error.
 */
export default function Sparkline({ data, color }: SparklineProps) {
  const points = Array.isArray(data) ? data.filter((n) => Number.isFinite(n)) : []
  const hasData = points.length >= 2

  if (!hasData) {
    // Graceful empty state: a subtle dashed baseline, no axis, no error.
    return (
      <svg
        width={WIDTH}
        height={HEIGHT}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="no data"
        style={{ display: 'block', opacity: 0.55 }}
      >
        <line
          x1={2}
          y1={HEIGHT / 2}
          x2={WIDTH - 2}
          y2={HEIGHT / 2}
          stroke="#2e333a"
          strokeWidth={1.5}
          strokeDasharray="3 3"
        />
      </svg>
    )
  }

  const chartData = points.map((value, index) => ({ index, value }))
  const min = Math.min(...points)
  const max = Math.max(...points)
  const gradientId = `spark-${color.replace('#', '')}`

  return (
    <div style={{ width: WIDTH, height: HEIGHT }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={[min, max]} />
          <Area
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#${gradientId})`}
            isAnimationActive={false}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
