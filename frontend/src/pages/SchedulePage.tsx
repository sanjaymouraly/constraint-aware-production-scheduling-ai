import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, GitCompare, RefreshCw, Send, ShieldCheck, ShieldX } from 'lucide-react'
import { api, errorMessage } from '@/api/client'
import { Badge, Card, EmptyState, ErrorBanner, Spinner } from '@/components/ui'
import { GanttChart, type ZoomLevel } from '@/components/GanttChart'
import { formatDateTime, formatDuration, formatNumber, formatPercent } from '@/lib/format'
import type { OrderMetric, Schedule, ScheduleOperation } from '@/api/types'
import { useAuth } from '@/auth/useAuth'

interface ScheduleDetail extends Schedule {
  operations: ScheduleOperation[]
}

export function SchedulePage() {
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const { canPlan } = useAuth()
  const [zoom, setZoom] = useState<ZoomLevel>('day')
  const [filter, setFilter] = useState('')
  const [showSetup, setShowSetup] = useState(true)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: async () => (await api.get<Schedule[]>('/schedules?limit=40')).data,
  })

  const selectedId = params.get('schedule')
  const selected = useQuery({
    queryKey: ['schedule', selectedId],
    queryFn: async () => (await api.get<ScheduleDetail>(`/schedules/${selectedId}`)).data,
    enabled: Boolean(selectedId),
  })

  const publish = useMutation({
    mutationFn: async (id: number) => (await api.post(`/schedules/${id}/publish`)).data,
    onSuccess: () => {
      setMessage('Plan published — it is now the live schedule.')
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['schedules'] })
    },
    onError: (err) => {
      setError(errorMessage(err))
      setMessage(null)
    },
  })

  const lateCodes = useMemo(() => {
    const orders = (selected.data?.metrics?.orders ?? []) as OrderMetric[]
    return new Set(orders.filter((order) => order.is_late).map((order) => order.work_order_code))
  }, [selected.data])

  const machineFilter = useMemo(() => {
    if (!filter.trim()) return undefined
    return undefined
  }, [filter])

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-steel-900">Production schedule</h1>
          <p className="text-sm text-steel-500">
            Interactive Gantt view of the persisted plan with machine, labour and setup detail.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="input w-72"
            value={selectedId ?? ''}
            onChange={(event) => {
              const value = event.target.value
              if (value) setParams({ schedule: value })
              else setParams({})
            }}
            aria-label="Select a plan version"
          >
            <option value="">Select a plan version…</option>
            {(schedules.data ?? []).map((schedule) => (
              <option key={schedule.id} value={schedule.id}>
                {schedule.code} — {schedule.source.toUpperCase()} — {schedule.status}
              </option>
            ))}
          </select>
          {selectedId && (
            <a
              className="btn btn-secondary btn-sm"
              href={`/api/schedules/${selectedId}/export.csv`}
              download
            >
              <Download size={15} /> CSV
            </a>
          )}
        </div>
      </header>

      {message && (
        <div className="rounded-lg border border-accent-500/30 bg-accent-50 px-4 py-2.5 text-sm text-accent-800">
          {message}
        </div>
      )}
      {error && <ErrorBanner message={error} />}

      {schedules.isLoading ? (
        <Spinner label="Loading plan versions…" />
      ) : (schedules.data ?? []).length === 0 ? (
        <Card>
          <EmptyState
            title="No plan versions yet"
            description="Generate a plan with the FCFS baseline or the CP-SAT optimizer to see it here."
            icon={<GitCompare size={22} />}
          />
        </Card>
      ) : !selectedId ? (
        <Card title="Plan versions" bodyClassName="p-0">
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Plan</th>
                  <th>Engine</th>
                  <th>Status</th>
                  <th>On time</th>
                  <th>Makespan</th>
                  <th>Setup</th>
                  <th>Validation</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(schedules.data ?? []).map((schedule) => (
                  <tr key={schedule.id}>
                    <td>
                      <p className="font-medium text-steel-800">{schedule.name}</p>
                      <p className="font-mono text-[11px] text-steel-400">{schedule.code}</p>
                    </td>
                    <td>
                      <Badge tone={schedule.source === 'cpsat' ? 'blue' : 'slate'}>
                        {schedule.source.toUpperCase()}
                      </Badge>
                    </td>
                    <td>
                      <Badge tone={schedule.is_published ? 'green' : 'slate'}>{schedule.status}</Badge>
                    </td>
                    <td className="font-mono text-xs">
                      {formatPercent(schedule.metrics?.on_time_delivery_pct)}
                    </td>
                    <td className="font-mono text-xs">{formatDuration(schedule.metrics?.makespan_minutes)}</td>
                    <td className="font-mono text-xs">{formatDuration(schedule.metrics?.setup_minutes)}</td>
                    <td>
                      {schedule.validation?.valid === false ? (
                        <Badge tone="red">{schedule.validation.error_count} errors</Badge>
                      ) : (
                        <Badge tone="green">valid</Badge>
                      )}
                    </td>
                    <td className="text-right">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => setParams({ schedule: String(schedule.id) })}
                      >
                        Open
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : selected.isLoading ? (
        <Spinner label="Loading the plan…" />
      ) : selected.error ? (
        <ErrorBanner message="Could not load the selected plan." />
      ) : selected.data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <Stat label="Operations" value={formatNumber(selected.data.metrics?.operations_scheduled)} />
            <Stat label="On time" value={formatPercent(selected.data.metrics?.on_time_delivery_pct)} />
            <Stat label="Makespan" value={formatDuration(selected.data.metrics?.makespan_minutes)} />
            <Stat label="Setup" value={formatDuration(selected.data.metrics?.setup_minutes)} />
            <Stat label="Solver" value={`${formatNumber(selected.data.metrics?.solver_runtime_ms)} ms`} />
          </div>

          <Card
            title="Gantt chart"
            subtitle={`${selected.data.horizon_start ? formatDateTime(selected.data.horizon_start) : ''} → ${formatDateTime(selected.data.horizon_end)}`}
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <input
                  className="input w-48"
                  placeholder="Filter order / operator…"
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                  aria-label="Filter operations"
                />
                <label className="inline-flex items-center gap-2 text-xs text-steel-600">
                  <input
                    type="checkbox"
                    checked={showSetup}
                    onChange={(event) => setShowSetup(event.target.checked)}
                  />
                  Show setup
                </label>
                {canPlan && !selected.data.is_published && (
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => publish.mutate(selected.data!.id)}
                    disabled={publish.isPending}
                  >
                    <Send size={14} /> Publish plan
                  </button>
                )}
                {selected.data.is_published && (
                  <Badge tone="green">
                    <ShieldCheck size={13} /> Published
                  </Badge>
                )}
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => void selected.refetch()}
                  aria-label="Refresh"
                >
                  <RefreshCw size={15} />
                </button>
              </div>
            }
          >
            <GanttChart
              operations={selected.data.operations ?? []}
              horizonStart={selected.data.horizon_start}
              horizonEnd={selected.data.horizon_end}
              zoom={zoom}
              onZoomChange={setZoom}
              showSetup={showSetup}
              filterText={filter}
              machineFilter={machineFilter}
              lateOrderCodes={[...lateCodes]}
              height={560}
            />
          </Card>

          {selected.data.validation && selected.data.validation.violations?.length ? (
            <Card title="Validation issues" subtitle="Reported by the independent schedule validator">
              <ul className="space-y-2 text-sm">
                {selected.data.validation.violations.slice(0, 12).map((violation, index) => (
                  <li key={index} className="flex items-start gap-2">
                    <ShieldX size={15} className="mt-0.5 shrink-0 text-danger-500" />
                    <span className="text-steel-700">
                      <span className="font-mono text-xs text-steel-500">{violation.code}</span>{' '}
                      {violation.message}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-4">
      <p className="stat-label">{label}</p>
      <p className="stat-value mt-1.5">{value}</p>
    </div>
  )
}
