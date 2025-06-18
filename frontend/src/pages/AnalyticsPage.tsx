import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Download, GitCompare, Sigma } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, errorMessage } from '@/api/client'
import { Badge, Card, EmptyState, ErrorBanner, Spinner } from '@/components/ui'
import { formatDateTime, formatDuration, formatNumber, formatPercent } from '@/lib/format'
import type { ComparisonRow, Schedule } from '@/api/types'

const CHART_METRICS = [
  { key: 'on_time_delivery_pct', label: 'On-time delivery (%)', better: 'up' },
  { key: 'weighted_tardiness', label: 'Weighted tardiness (min)', better: 'down' },
  { key: 'makespan_minutes', label: 'Makespan (min)', better: 'down' },
  { key: 'machine_utilisation_pct', label: 'Utilisation (%)', better: 'up' },
  { key: 'setup_minutes', label: 'Setup (min)', better: 'down' },
  { key: 'operating_cost', label: 'Operating cost', better: 'down' },
] as const

interface CompareResponse {
  schedules: { id: number; code: string; name: string; source: string; metrics: Record<string, any> }[]
  comparisons: {
    baseline_schedule_id: number
    optimised_schedule_id: number
    rows: ComparisonRow[]
    improved_metrics: number
    regressed_metrics: number
  }[]
}

export function AnalyticsPage() {
  const [baselineId, setBaselineId] = useState<number | null>(null)
  const [candidateId, setCandidateId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: async () => (await api.get<Schedule[]>('/schedules?limit=50')).data,
  })

  const compare = useMutation({
    mutationFn: async (ids: number[]) =>
      (await api.post<CompareResponse>('/schedules/compare', { schedule_ids: ids })).data,
    onSuccess: () => setError(null),
    onError: (err) => setError(errorMessage(err)),
  })

  const list = useMemo(() => schedules.data ?? [], [schedules.data])
  const baseline = baselineId ?? list[0]?.id ?? null
  const candidate = candidateId ?? list[1]?.id ?? null

  const comparisonRows = compare.data?.comparisons?.[0]?.rows ?? []

  const chartData = useMemo(() => {
    const schedulesForChart = compare.data?.schedules ?? []
    if (schedulesForChart.length === 0) return []
    return CHART_METRICS.map((metric) => {
      const row: Record<string, string | number> = { metric: metric.label }
      for (const schedule of schedulesForChart) {
        row[schedule.code] = Number(schedule.metrics?.[metric.key] ?? 0)
      }
      return row
    })
  }, [compare.data])

  const trendData = useMemo(() => {
    return [...list]
      .reverse()
      .map((schedule) => ({
        code: schedule.code,
        on_time: Number(schedule.metrics?.on_time_delivery_pct ?? 0),
        tardiness: Number(schedule.metrics?.weighted_tardiness ?? 0),
      }))
  }, [list])

  const downloadReport = () => {
    if (!compare.data) return
    const lines: string[] = []
    lines.push('metric,baseline,optimised,delta,verdict')
    for (const row of comparisonRows) {
      lines.push(
        [
          `"${row.metric}"`,
          row.baseline,
          row.optimised,
          row.delta,
          row.verdict,
        ].join(','),
      )
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'schedule-comparison.csv'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  if (schedules.isLoading) return <Spinner label="Loading analytics…" />

  if (list.length < 2) {
    return (
      <Card>
        <EmptyState
          title="Not enough plan versions"
          description="Run the optimizer at least twice to compare a baseline with an optimised plan."
          icon={<GitCompare size={22} />}
        />
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-steel-900">Analytics</h1>
          <p className="text-sm text-steel-500">
            Measured, reproducible comparisons between stored plan versions.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="input w-64"
            value={baseline ?? ''}
            onChange={(event) => setBaselineId(Number(event.target.value))}
            aria-label="Baseline plan"
          >
            {list.map((schedule) => (
              <option key={schedule.id} value={schedule.id}>
                Baseline: {schedule.code} ({schedule.source.toUpperCase()})
              </option>
            ))}
          </select>
          <select
            className="input w-64"
            value={candidate ?? ''}
            onChange={(event) => setCandidateId(Number(event.target.value))}
            aria-label="Compared plan"
          >
            {list.map((schedule) => (
              <option key={schedule.id} value={schedule.id}>
                Compare: {schedule.code} ({schedule.source.toUpperCase()})
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => baseline && candidate && compare.mutate([baseline, candidate])}
            disabled={compare.isPending || !baseline || !candidate}
          >
            <Sigma size={14} /> Compare
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={downloadReport}
            disabled={!compare.data}
          >
            <Download size={14} /> Report CSV
          </button>
        </div>
      </header>

      {error && <ErrorBanner message={error} />}

      {comparisonRows.length > 0 && (
        <Card
          title="Metric comparison"
          subtitle={`${compare.data?.comparisons[0].improved_metrics} improved · ${compare.data?.comparisons[0].regressed_metrics} regressed`}
          bodyClassName="p-0"
        >
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th className="text-right">Baseline</th>
                  <th className="text-right">Optimised</th>
                  <th className="text-right">Delta</th>
                  <th className="text-right">Relative</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {comparisonRows.map((row) => (
                  <tr key={row.metric}>
                    <td className="text-xs">{row.metric}</td>
                    <td className="text-right font-mono text-xs">{formatNumber(row.baseline, 2)}</td>
                    <td className="text-right font-mono text-xs">{formatNumber(row.optimised, 2)}</td>
                    <td className="text-right font-mono text-xs">
                      {row.delta > 0 ? '+' : ''}
                      {formatNumber(row.delta, 2)}
                    </td>
                    <td className="text-right font-mono text-xs">
                      {row.delta_pct === null ? '—' : `${row.delta_pct > 0 ? '+' : ''}${row.delta_pct.toFixed(1)}%`}
                    </td>
                    <td>
                      <Badge
                        tone={
                          row.verdict === 'better' ? 'green' : row.verdict === 'worse' ? 'red' : 'slate'
                        }
                      >
                        {row.verdict}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="Plan history" subtitle="On-time delivery across every stored plan version">
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="code" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
                <Tooltip />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="on_time"
                  name="On-time %"
                  stroke="#0f766e"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="Weighted tardiness history" subtitle="Lower is better">
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="code" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="tardiness"
                  name="Weighted tardiness"
                  stroke="#b45309"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      {chartData.length > 0 && (
        <Card title="Baseline vs optimised" subtitle="Key indicators for the selected plan versions">
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="metric" tick={{ fontSize: 10 }} interval={0} angle={-15} textAnchor="end" height={60} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                {compare.data?.schedules.map((schedule, index) => (
                  <Bar
                    key={schedule.id}
                    dataKey={schedule.code}
                    fill={index === 0 ? '#94a3b8' : '#0f766e'}
                    radius={[4, 4, 0, 0]}
                  >
                    {chartData.map((_, cellIndex) => (
                      <Cell key={cellIndex} fill={index === 0 ? '#94a3b8' : '#0f766e'} />
                    ))}
                  </Bar>
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      <Card title="Stored plan versions" bodyClassName="p-0">
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>Plan</th>
                <th>Engine</th>
                <th>Status</th>
                <th className="text-right">On time</th>
                <th className="text-right">Tardiness</th>
                <th className="text-right">Makespan</th>
                <th className="text-right">Setup</th>
                <th className="text-right">Cost</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {list.map((schedule) => (
                <tr key={schedule.id}>
                  <td>
                    <p className="font-mono text-xs font-semibold">{schedule.code}</p>
                    <p className="text-[11px] text-steel-500">{schedule.name}</p>
                  </td>
                  <td>
                    <Badge tone={schedule.source === 'cpsat' ? 'blue' : 'slate'}>
                      {schedule.source.toUpperCase()}
                    </Badge>
                  </td>
                  <td>
                    <Badge tone={schedule.is_published ? 'green' : 'slate'}>{schedule.status}</Badge>
                  </td>
                  <td className="text-right font-mono text-xs">
                    {formatPercent(schedule.metrics?.on_time_delivery_pct)}
                  </td>
                  <td className="text-right font-mono text-xs">
                    {formatNumber(schedule.metrics?.weighted_tardiness)}
                  </td>
                  <td className="text-right font-mono text-xs">
                    {formatDuration(schedule.metrics?.makespan_minutes)}
                  </td>
                  <td className="text-right font-mono text-xs">
                    {formatDuration(schedule.metrics?.setup_minutes)}
                  </td>
                  <td className="text-right font-mono text-xs">
                    {formatNumber(schedule.metrics?.operating_cost)}
                  </td>
                  <td className="text-xs text-steel-500">{formatDateTime(schedule.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
