import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertTriangle, FlaskConical, Loader2, Play, Plus, Rocket } from 'lucide-react'
import { api, errorMessage } from '@/api/client'
import { Badge, Card, EmptyState, ErrorBanner, Field, Modal, Spinner } from '@/components/ui'
import { formatDateTime, formatDuration, formatNumber, formatPercent } from '@/lib/format'
import type { Machine, Material, Operator, Scenario, Schedule, WorkOrder } from '@/api/types'
import { useAuth } from '@/auth/useAuth'

interface ImpactReport {
  available: boolean
  base_schedule_id?: number
  new_schedule_id?: number
  delayed_orders?: { work_order_id: number; delay_minutes: number }[]
  improved_orders?: { work_order_id: number; gain_minutes: number }[]
  disrupted_operations?: number
  metrics_before?: Record<string, number | null>
  metrics_after?: Record<string, number | null>
}

interface SimulateResult {
  status: string
  metrics: Record<string, any>
  validation: { valid: boolean; violation_count: number }
  comparison: { rows: { metric: string; baseline: number; optimised: number; delta: number; verdict: string }[] } | null
  impact: ImpactReport
  schedule_id: number | null
  runtime_ms: number
}

interface DisruptionDefinition {
  value: string
  label: string
  fields: string[]
}

const DISRUPTION_KINDS: DisruptionDefinition[] = [
  { value: 'machine_breakdown', label: 'Machine breakdown', fields: ['machine_id', 'duration_hours'] },
  { value: 'unplanned_maintenance', label: 'Unplanned maintenance', fields: ['machine_id', 'duration_hours'] },
  { value: 'material_delay', label: 'Delayed material receipt', fields: ['receipt_id', 'delay_days'] },
  { value: 'operator_absence', label: 'Operator absence', fields: ['operator_id', 'duration_hours'] },
  { value: 'urgent_order', label: 'Urgent order', fields: ['template_work_order_id', 'quantity'] },
  { value: 'demand_surge', label: 'Demand surge', fields: ['template_work_order_id', 'additional_orders'] },
  { value: 'order_cancelled', label: 'Order cancellation', fields: ['work_order_id'] },
  { value: 'quantity_change', label: 'Quantity change', fields: ['work_order_id', 'quantity'] },
]

export function ScenariosPage() {
  const queryClient = useQueryClient()
  const { canPlan } = useAuth()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [disruptionOpen, setDisruptionOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SimulateResult | null>(null)

  const scenarios = useQuery({
    queryKey: ['scenarios'],
    queryFn: async () => (await api.get<Scenario[]>('/scenarios')).data,
  })

  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: async () => (await api.get<Schedule[]>('/schedules?limit=50')).data,
  })

  const current = selectedId ?? (scenarios.data?.[0]?.id ?? null)

  const impact = useQuery({
    queryKey: ['scenario-impact', current],
    queryFn: async () => (await api.get<ImpactReport>(`/scenarios/${current}/impact`)).data,
    enabled: current !== null,
  })

  const simulate = useMutation({
    mutationFn: async (id: number) =>
      (
        await api.post<SimulateResult>(`/scenarios/${id}/simulate`, {
          engine: 'cpsat',
          time_limit_seconds: 20,
          workers: 2,
          random_seed: 42,
          objective_name: 'weighted_tardiness',
        })
      ).data,
    onSuccess: (data) => {
      setResult(data)
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['scenarios'] })
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const publish = useMutation({
    mutationFn: async (id: number) => (await api.post(`/scenarios/${id}/publish`)).data,
    onSuccess: () => {
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['scenarios'] })
      void queryClient.invalidateQueries({ queryKey: ['schedules'] })
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const selected = scenarios.data?.find((item) => item.id === current) ?? null

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-steel-900">Scenario simulator</h1>
          <p className="text-sm text-steel-500">
            Clone a plan, apply a disruption, re-plan and compare — the live plan is never touched
            until you publish it explicitly.
          </p>
        </div>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setCreating(true)} disabled={!canPlan}>
          <Plus size={15} /> New scenario
        </button>
      </header>

      {error && <ErrorBanner message={error} />}

      {scenarios.isLoading ? (
        <Spinner />
      ) : (scenarios.data ?? []).length === 0 ? (
        <Card>
          <EmptyState
            title="No scenarios yet"
            description="Create a scenario from an existing plan to test what-if scenarios."
            icon={<FlaskConical size={22} />}
          />
        </Card>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[320px_1fr]">
          <Card title="Scenarios" bodyClassName="p-0">
            <ul className="divide-y divide-steel-100">
              {(scenarios.data ?? []).map((scenario) => (
                <li key={scenario.id}>
                  <button
                    type="button"
                    className={`w-full px-4 py-3 text-left transition-colors hover:bg-steel-50 ${
                      current === scenario.id ? 'bg-accent-50/60' : ''
                    }`}
                    onClick={() => {
                      setSelectedId(scenario.id)
                      setResult(null)
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium text-steel-800">{scenario.name}</p>
                      <Badge tone={scenario.status === 'published' ? 'green' : 'slate'}>{scenario.status}</Badge>
                    </div>
                    <p className="font-mono text-[11px] text-steel-400">{scenario.code}</p>
                    <p className="mt-1 text-xs text-steel-500">
                      {(scenario.assumptions?.disruptions ?? []).length} disruption(s)
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          </Card>

          <div className="space-y-6">
            {selected ? (
              <Card
                title={selected.name}
                subtitle={`${selected.code} · cloned from plan #${selected.base_schedule_id ?? '—'} · created ${formatDateTime(selected.created_at)}`}
                actions={
                  <>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => setDisruptionOpen(true)}
                      disabled={!canPlan}
                    >
                      <AlertTriangle size={14} /> Add disruption
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={() => simulate.mutate(selected.id)}
                      disabled={simulate.isPending || !canPlan}
                    >
                      {simulate.isPending ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Play size={14} />
                      )}
                      Re-plan scenario
                    </button>
                  </>
                }
              >
                {selected.description && (
                  <p className="mb-3 text-sm text-steel-600">{selected.description}</p>
                )}

                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-steel-500">
                    Applied disruptions
                  </p>
                  {(selected.assumptions?.disruptions ?? []).length === 0 ? (
                    <p className="mt-1 text-sm text-steel-500">
                      None yet — add a breakdown, a material delay or an urgent order.
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-2">
                      {(selected.assumptions?.disruptions ?? []).map(
                        (item: any, index: number) => (
                          <li
                            key={index}
                            className="rounded-lg border border-steel-200 bg-steel-50 px-3 py-2 text-sm"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-medium text-steel-800">
                                {item.title || item.kind}
                              </span>
                              <Badge
                                tone={
                                  item.severity === 'critical'
                                    ? 'red'
                                    : item.severity === 'warning'
                                      ? 'amber'
                                      : 'blue'
                                }
                              >
                                {item.kind.replace(/_/g, ' ')}
                              </Badge>
                            </div>
                            {item.description && (
                              <p className="mt-0.5 text-xs text-steel-500">{item.description}</p>
                            )}
                          </li>
                        ),
                      )}
                    </ul>
                  )}
                </div>

                {impact.data?.available && (
                  <div className="mt-5">
                    <p className="text-xs font-semibold uppercase tracking-wide text-steel-500">
                      Impact vs. the original plan
                    </p>
                    <div className="mt-2 grid gap-3 sm:grid-cols-3">
                      <ImpactStat
                        label="On-time delivery"
                        before={impact.data.metrics_before?.on_time_delivery_pct}
                        after={impact.data.metrics_after?.on_time_delivery_pct}
                        suffix="%"
                      />
                      <ImpactStat
                        label="Weighted tardiness"
                        before={impact.data.metrics_before?.weighted_tardiness}
                        after={impact.data.metrics_after?.weighted_tardiness}
                      />
                      <ImpactStat
                        label="Makespan (min)"
                        before={impact.data.metrics_before?.makespan_minutes}
                        after={impact.data.metrics_after?.makespan_minutes}
                      />
                    </div>
                    <p className="mt-2 text-xs text-steel-500">
                      {formatNumber(impact.data.disrupted_operations ?? 0)} operations moved to a
                      different machine.
                    </p>
                  </div>
                )}
              </Card>
            ) : null}

            {result ? (
              <Card
                title="Re-plan result"
                subtitle={`Solver status ${result.status} · ${formatNumber(result.runtime_ms)} ms · validation ${
                  result.validation.valid ? 'passed' : 'failed'
                }`}
                actions={
                  result.schedule_id ? (
                    <>
                      <Link
                        to={`/schedule?schedule=${result.schedule_id}`}
                        className="btn btn-secondary btn-sm"
                      >
                        Open Gantt
                      </Link>
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        onClick={() => current && publish.mutate(current)}
                        disabled={publish.isPending}
                      >
                        <Rocket size={14} /> Publish as live plan
                      </button>
                    </>
                  ) : null
                }
              >
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="card p-3">
                    <p className="stat-label">On-time delivery</p>
                    <p className="stat-value mt-1">{formatPercent(result.metrics.on_time_delivery_pct)}</p>
                  </div>
                  <div className="card p-3">
                    <p className="stat-label">Weighted tardiness</p>
                    <p className="stat-value mt-1">{formatNumber(result.metrics.weighted_tardiness)}</p>
                  </div>
                  <div className="card p-3">
                    <p className="stat-label">Makespan</p>
                    <p className="stat-value mt-1">{formatDuration(result.metrics.makespan_minutes)}</p>
                  </div>
                </div>

                {result.comparison && (
                  <div className="mt-4 overflow-x-auto">
                    <table className="table-base">
                      <thead>
                        <tr>
                          <th>Metric</th>
                          <th className="text-right">Baseline</th>
                          <th className="text-right">Scenario</th>
                          <th className="text-right">Delta</th>
                          <th>Verdict</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.comparison.rows.map((row) => (
                          <tr key={row.metric}>
                            <td>{row.metric}</td>
                            <td className="text-right font-mono text-xs">{formatNumber(row.baseline)}</td>
                            <td className="text-right font-mono text-xs">{formatNumber(row.optimised)}</td>
                            <td className="text-right font-mono text-xs">
                              {row.delta > 0 ? '+' : ''}
                              {formatNumber(row.delta)}
                            </td>
                            <td>
                              <Badge
                                tone={
                                  row.verdict === 'better'
                                    ? 'green'
                                    : row.verdict === 'worse'
                                      ? 'red'
                                      : 'slate'
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
                )}
              </Card>
            ) : null}
          </div>
        </div>
      )}

      <CreateScenarioDialog
        open={creating}
        schedules={schedules.data ?? []}
        onClose={() => setCreating(false)}
        onCreated={(id) => {
          setCreating(false)
          setSelectedId(id)
          void queryClient.invalidateQueries({ queryKey: ['scenarios'] })
        }}
      />

      <DisruptionDialog
        open={disruptionOpen}
        scenarioId={current}
        onClose={() => setDisruptionOpen(false)}
        onApplied={() => {
          setDisruptionOpen(false)
          void queryClient.invalidateQueries({ queryKey: ['scenarios'] })
          void queryClient.invalidateQueries({ queryKey: ['scenario-impact', current] })
        }}
      />
    </div>
  )
}

function ImpactStat({
  label,
  before,
  after,
  suffix = '',
}: {
  label: string
  before?: number | null
  after?: number | null
  suffix?: string
}) {
  const b = before ?? null
  const a = after ?? null
  const delta = b !== null && a !== null ? a - b : null
  return (
    <div className="rounded-lg border border-steel-200 bg-white p-3">
      <p className="stat-label">{label}</p>
      <p className="mt-1 font-mono text-sm">
        {b !== null ? `${formatNumber(b)}${suffix}` : '—'} →{' '}
        <span className="font-semibold">{a !== null ? `${formatNumber(a)}${suffix}` : '—'}</span>
      </p>
      {delta !== null && (
        <p className={`text-xs ${delta > 0 ? 'text-accent-700' : delta < 0 ? 'text-danger-600' : 'text-steel-500'}`}>
          {delta > 0 ? '+' : ''}
          {formatNumber(delta)}
          {suffix}
        </p>
      )}
    </div>
  )
}

function CreateScenarioDialog({
  open,
  schedules,
  onClose,
  onCreated,
}: {
  open: boolean
  schedules: Schedule[]
  onClose: () => void
  onCreated: (id: number) => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [baseScheduleId, setBaseScheduleId] = useState('')
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post<Scenario>('/scenarios', {
          name,
          description: description || null,
          base_schedule_id: Number(baseScheduleId),
          lock_started_operations: true,
        })
      ).data,
    onSuccess: (data) => {
      setError(null)
      onCreated(data.id)
    },
    onError: (err) => setError(errorMessage(err)),
  })

  return (
    <Modal
      open={open}
      title="New scenario"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => create.mutate()}
            disabled={create.isPending || !name || !baseScheduleId}
          >
            {create.isPending && <Loader2 size={15} className="animate-spin" />}
            Create scenario
          </button>
        </>
      }
    >
      {error && <ErrorBanner message={error} />}
      <div className="space-y-4">
        <Field label="Base plan" hint="The plan is cloned; the live schedule is untouched.">
          <select
            className="input"
            value={baseScheduleId}
            onChange={(event) => setBaseScheduleId(event.target.value)}
          >
            <option value="">Select a plan…</option>
            {schedules.map((schedule) => (
              <option key={schedule.id} value={schedule.id}>
                {schedule.code} — {schedule.source.toUpperCase()} — {schedule.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Name">
          <input
            className="input"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. CNC-3 breakdown on Monday"
          />
        </Field>
        <Field label="Description">
          <textarea
            className="input"
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
      </div>
    </Modal>
  )
}

function DisruptionDialog({
  open,
  scenarioId,
  onClose,
  onApplied,
}: {
  open: boolean
  scenarioId: number | null
  onClose: () => void
  onApplied: () => void
}) {
  const [kind, setKind] = useState<string>('machine_breakdown')
  const [title, setTitle] = useState('')
  const [severity, setSeverity] = useState('warning')
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  const machines = useQuery({
    queryKey: ['machines'],
    queryFn: async () => (await api.get<Machine[]>('/machines')).data,
    enabled: open,
  })
  const operators = useQuery({
    queryKey: ['operators'],
    queryFn: async () => (await api.get<Operator[]>('/operators')).data,
    enabled: open,
  })
  const materials = useQuery({
    queryKey: ['materials'],
    queryFn: async () => (await api.get<Material[]>('/materials')).data,
    enabled: open,
  })
  const orders = useQuery({
    queryKey: ['work-orders', 'all'],
    queryFn: async () => (await api.get<WorkOrder[]>('/work-orders?limit=200')).data,
    enabled: open,
  })

  const definition = DISRUPTION_KINDS.find((item) => item.value === kind)!

  const apply = useMutation({
    mutationFn: async () => {
      const payload: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(values)) {
        if (value === '') continue
        payload[key] = Number(value)
      }
      return (
        await api.post(`/scenarios/${scenarioId}/disruptions`, {
          kind,
          title: title || definition.label,
          severity,
          payload,
        })
      ).data
    },
    onSuccess: () => {
      setError(null)
      setValues({})
      setTitle('')
      onApplied()
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const receipts = (materials.data ?? []).flatMap((material) =>
    material.receipts.map((receipt) => ({
      id: receipt.id,
      label: `${material.sku} · ${formatDateTime(receipt.expected_at)} · +${formatNumber(receipt.quantity)}`,
    })),
  )

  return (
    <Modal
      open={open}
      title="Apply a disruption"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => apply.mutate()}
            disabled={apply.isPending || scenarioId === null}
          >
            {apply.isPending && <Loader2 size={15} className="animate-spin" />}
            Apply disruption
          </button>
        </>
      }
    >
      {error && <ErrorBanner message={error} />}
      <div className="space-y-4">
        <Field label="Disruption type">
          <select
            className="input"
            value={kind}
            onChange={(event) => {
              setKind(event.target.value)
              setValues({})
            }}
          >
            {DISRUPTION_KINDS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Title">
          <input
            className="input"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder={definition.label}
          />
        </Field>

        <Field label="Severity">
          <select
            className="input"
            value={severity}
            onChange={(event) => setSeverity(event.target.value)}
          >
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="critical">Critical</option>
          </select>
        </Field>

        {definition.fields.includes('machine_id') && (
          <Field label="Machine">
            <select
              className="input"
              value={values.machine_id ?? ''}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, machine_id: event.target.value }))
              }
            >
              <option value="">Select a machine…</option>
              {(machines.data ?? []).map((machine) => (
                <option key={machine.id} value={machine.id}>
                  {machine.code} — {machine.name}
                </option>
              ))}
            </select>
          </Field>
        )}

        {definition.fields.includes('duration_hours') && (
          <Field label="Duration (hours)">
            <input
              type="number"
              min={1}
              step="0.5"
              className="input"
              value={values.duration_hours ?? '4'}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, duration_hours: event.target.value }))
              }
            />
          </Field>
        )}

        {definition.fields.includes('operator_id') && (
          <Field label="Operator">
            <select
              className="input"
              value={values.operator_id ?? ''}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, operator_id: event.target.value }))
              }
            >
              <option value="">Select an operator…</option>
              {(operators.data ?? []).map((operator) => (
                <option key={operator.id} value={operator.id}>
                  {operator.code} — {operator.name}
                </option>
              ))}
            </select>
          </Field>
        )}

        {definition.fields.includes('receipt_id') && (
          <Field label="Material receipt">
            <select
              className="input"
              value={values.receipt_id ?? ''}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, receipt_id: event.target.value }))
              }
            >
              <option value="">Select a receipt…</option>
              {receipts.map((receipt) => (
                <option key={receipt.id} value={receipt.id}>
                  {receipt.label}
                </option>
              ))}
            </select>
          </Field>
        )}

        {definition.fields.includes('delay_days') && (
          <Field label="Delay (days)">
            <input
              type="number"
              min={0}
              step="0.5"
              className="input"
              value={values.delay_days ?? '2'}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, delay_days: event.target.value }))
              }
            />
          </Field>
        )}

        {definition.fields.includes('work_order_id') && (
          <Field label="Work order">
            <select
              className="input"
              value={values.work_order_id ?? ''}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, work_order_id: event.target.value }))
              }
            >
              <option value="">Select a work order…</option>
              {(orders.data ?? []).map((order) => (
                <option key={order.id} value={order.id}>
                  {order.code} · {order.customer_name} · due {formatDateTime(order.due_at)}
                </option>
              ))}
            </select>
          </Field>
        )}

        {definition.fields.includes('template_work_order_id') && (
          <Field label="Template work order" hint="Copied into a new order with the parameters below.">
            <select
              className="input"
              value={values.template_work_order_id ?? ''}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, template_work_order_id: event.target.value }))
              }
            >
              <option value="">Select a work order…</option>
              {(orders.data ?? []).map((order) => (
                <option key={order.id} value={order.id}>
                  {order.code} · {order.product_sku} · qty {formatNumber(order.quantity)}
                </option>
              ))}
            </select>
          </Field>
        )}

        {definition.fields.includes('quantity') && (
          <Field label="Quantity">
            <input
              type="number"
              min={1}
              className="input"
              value={values.quantity ?? '25'}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, quantity: event.target.value }))
              }
            />
          </Field>
        )}

        {definition.fields.includes('additional_orders') && (
          <Field label="Additional orders">
            <input
              type="number"
              min={1}
              className="input"
              value={values.additional_orders ?? '2'}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, additional_orders: event.target.value }))
              }
            />
          </Field>
        )}
      </div>
    </Modal>
  )
}
