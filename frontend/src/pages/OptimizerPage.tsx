import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  ArrowDownRight,
  ArrowUpRight,
  Loader2,
  Minus,
  Play,
  Sigma,
} from 'lucide-react'
import { api, errorMessage } from '@/api/client'
import { Badge, Card, ErrorBanner, Field, Spinner } from '@/components/ui'
import { formatDuration, formatNumber, formatPercent } from '@/lib/format'
import type { ComparisonRow, RunResponse, Schedule } from '@/api/types'
import { useAuth } from '@/auth/useAuth'

const OBJECTIVES = [
  { value: 'weighted_tardiness', label: 'Weighted tardiness (default)' },
  { value: 'minimize_tardiness', label: 'Minimise total tardiness' },
  { value: 'minimize_makespan', label: 'Minimise makespan' },
  { value: 'minimize_setup', label: 'Minimise setup time' },
  { value: 'minimize_cost', label: 'Minimise operating cost' },
  { value: 'balanced', label: 'Balanced plan' },
]

const WEIGHT_TERMS = [
  { key: 'tardiness', label: 'Tardiness' },
  { key: 'makespan', label: 'Makespan' },
  { key: 'setup', label: 'Setup' },
  { key: 'overtime', label: 'Overtime' },
  { key: 'stability', label: 'Plan stability' },
  { key: 'flow', label: 'Flow time' },
]

export function OptimizerPage() {
  const queryClient = useQueryClient()
  const { canPlan } = useAuth()
  const [engine, setEngine] = useState<'fcfs' | 'cpsat'>('cpsat')
  const [objective, setObjective] = useState('weighted_tardiness')
  const [timeLimit, setTimeLimit] = useState(20)
  const [workers, setWorkers] = useState(2)
  const [seed, setSeed] = useState(42)
  const [mode, setMode] = useState<'weighted' | 'lexicographic'>('weighted')
  const [weights, setWeights] = useState<Record<string, number>>({
    tardiness: 1,
    makespan: 0.2,
    setup: 0.1,
    overtime: 0.1,
    stability: 0.05,
    flow: 0.1,
  })
  const [result, setResult] = useState<RunResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [compareSelection, setCompareSelection] = useState<number[]>([])

  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: async () => (await api.get<Schedule[]>('/schedules?limit=50')).data,
  })

  const run = useMutation({
    mutationFn: async () => {
      const payload = {
        engine,
        objective_name: objective,
        objective_mode: mode,
        time_limit_seconds: timeLimit,
        workers,
        random_seed: seed,
        weights: mode === 'weighted' ? weights : {},
        name: `${engine.toUpperCase()} ${objective}`,
        notes: 'Run from the optimizer workspace',
      }
      return (await api.post<RunResponse>('/scheduling/run', payload)).data
    },
    onSuccess: (data) => {
      setResult(data)
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['schedules'] })
    },
    onError: (err) => {
      setError(errorMessage(err))
      setResult(null)
    },
  })

  const compare = useMutation({
    mutationFn: async (ids: number[]) =>
      (await api.post('/schedules/compare', { schedule_ids: ids })).data as {
        comparisons: { rows: ComparisonRow[]; improved_metrics: number; regressed_metrics: number }[]
      },
  })

  const comparisonRows = result?.comparison?.rows ?? []

  const statusTone = useMemo(() => {
    const status = result?.status?.toUpperCase()
    if (status === 'OPTIMAL') return 'green'
    if (status === 'FEASIBLE') return 'blue'
    if (status === 'INFEASIBLE') return 'red'
    return 'amber'
  }, [result?.status])

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-steel-900">Schedule optimizer</h1>
        <p className="text-sm text-steel-500">
          The solver decides the plan. The assistant only explains it — never the other way round.
        </p>
      </header>

      <div className="grid gap-6 xl:grid-cols-[380px_1fr]">
        <Card title="Run configuration" subtitle="Both engines respect the identical hard constraints">
          <div className="space-y-4">
            <Field label="Engine">
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setEngine('fcfs')}
                  className={`btn ${engine === 'fcfs' ? 'btn-primary' : 'btn-secondary'}`}
                >
                  FCFS baseline
                </button>
                <button
                  type="button"
                  onClick={() => setEngine('cpsat')}
                  className={`btn ${engine === 'cpsat' ? 'btn-primary' : 'btn-secondary'}`}
                >
                  CP-SAT optimizer
                </button>
              </div>
            </Field>

            <Field label="Objective">
              <select
                className="input"
                value={objective}
                onChange={(event) => setObjective(event.target.value)}
              >
                {OBJECTIVES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Objective combination">
              <select
                className="input"
                value={mode}
                onChange={(event) => setMode(event.target.value as 'weighted' | 'lexicographic')}
              >
                <option value="weighted">Weighted sum (normalised)</option>
                <option value="lexicographic">Lexicographic (tardiness first)</option>
              </select>
            </Field>

            {mode === 'weighted' && (
              <Field label="Objective weights" hint="Relative importance of each term">
                <div className="space-y-2">
                  {WEIGHT_TERMS.map((term) => (
                    <div key={term.key} className="flex items-center gap-3">
                      <span className="w-28 shrink-0 text-xs text-steel-600">{term.label}</span>
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={weights[term.key] ?? 0}
                        onChange={(event) =>
                          setWeights((prev) => ({
                            ...prev,
                            [term.key]: Number(event.target.value),
                          }))
                        }
                        className="h-1.5 flex-1 accent-accent-600"
                        aria-label={`${term.label} weight`}
                      />
                      <span className="w-10 text-right font-mono text-xs text-steel-500">
                        {(weights[term.key] ?? 0).toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              </Field>
            )}

            <div className="grid grid-cols-3 gap-3">
              <Field label="Time limit (s)">
                <input
                  type="number"
                  min={1}
                  max={600}
                  className="input"
                  value={timeLimit}
                  onChange={(event) => setTimeLimit(Number(event.target.value))}
                />
              </Field>
              <Field label="Workers">
                <input
                  type="number"
                  min={1}
                  max={16}
                  className="input"
                  value={workers}
                  onChange={(event) => setWorkers(Number(event.target.value))}
                />
              </Field>
              <Field label="Seed">
                <input
                  type="number"
                  min={0}
                  className="input"
                  value={seed}
                  onChange={(event) => setSeed(Number(event.target.value))}
                />
              </Field>
            </div>

            <button
              type="button"
              className="btn-primary w-full"
              onClick={() => run.mutate()}
              disabled={run.isPending || !canPlan}
              title={canPlan ? undefined : 'Planner or admin role required'}
            >
              {run.isPending ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Play size={16} />
              )}
              {run.isPending ? 'Solving…' : `Run ${engine === 'cpsat' ? 'CP-SAT' : 'FCFS'}`}
            </button>
            {!canPlan && (
              <p className="text-xs text-warn-600">
                Your role can view results but cannot start solver runs.
              </p>
            )}
          </div>
        </Card>

        <div className="space-y-6">
          {error && <ErrorBanner message={error} />}

          {!result && !run.isPending ? (
            <Card title="No run yet">
              <p className="text-sm text-steel-500">
                Configure the solver and run it. Every run is validated by the independent
                constraint validator before the metrics are shown.
              </p>
            </Card>
          ) : null}

          {run.isPending ? (
            <Card>
              <Spinner label="Solving the production plan…" />
            </Card>
          ) : null}

          {result ? (
            <>
              <Card
                title="Solver result"
                subtitle="Status, objective and independent validation"
                actions={
                  <div className="flex items-center gap-2">
                    <Badge tone={statusTone}>{result.status}</Badge>
                    {result.schedule && (
                      <Link to={`/schedule?schedule=${result.schedule.id}`} className="btn btn-secondary btn-sm">
                        Open Gantt
                      </Link>
                    )}
                  </div>
                }
              >
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                  <Metric
                    label="Solver status"
                    value={result.solver_status}
                    hint={
                      result.solver_status === 'OPTIMAL'
                        ? 'Proven optimal'
                        : 'Time limited — not globally optimal'
                    }
                  />
                  <Metric
                    label="Objective value"
                    value={
                      result.objective_value !== undefined && result.objective_value !== null
                        ? formatNumber(result.objective_value)
                        : '—'
                    }
                    hint={
                      result.best_bound !== undefined && result.best_bound !== null
                        ? `bound ${formatNumber(result.best_bound)}`
                        : 'no bound reported'
                    }
                  />
                  <Metric
                    label="Runtime"
                    value={`${formatNumber(result.runtime_ms)} ms`}
                    hint={`${result.metrics.operations_scheduled} operations scheduled`}
                  />
                  <Metric
                    label="Independent validation"
                    value={result.validation.valid ? 'Valid' : 'Invalid'}
                    hint={`${result.validation.violation_count} violations`}
                  />
                </div>

                {result.unscheduled.length > 0 && (
                  <div className="mt-4 rounded-lg border border-warn-500/30 bg-warn-500/10 px-4 py-3 text-sm text-warn-700">
                    {result.unscheduled.length} operation(s) could not be scheduled under the hard
                    constraints: {result.unscheduled.slice(0, 5).join(', ')}
                    {result.unscheduled.length > 5 ? '…' : ''}
                  </div>
                )}

                {!result.validation.valid && (
                  <div className="mt-4 rounded-lg border border-danger-500/30 bg-danger-500/10 px-4 py-3 text-sm text-danger-700">
                    <p className="font-semibold">The validator rejected this plan:</p>
                    <ul className="mt-1 list-disc pl-5">
                      {result.validation.violations.slice(0, 8).map((violation, index) => (
                        <li key={index}>
                          <span className="font-mono text-xs">{violation.code}</span>{' '}
                          {violation.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </Card>

              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <Metric
                  label="On-time delivery"
                  value={formatPercent(result.metrics.on_time_delivery_pct)}
                  hint={`${result.metrics.orders_on_time}/${result.metrics.orders_scheduled} orders`}
                />
                <Metric
                  label="Weighted tardiness"
                  value={formatNumber(result.metrics.weighted_tardiness)}
                  hint={`${formatDuration(result.metrics.tardiness_minutes)} total`}
                />
                <Metric
                  label="Makespan"
                  value={formatDuration(result.metrics.makespan_minutes)}
                  hint={`${formatNumber(result.metrics.machine_utilisation_pct)}% utilisation`}
                />
                <Metric
                  label="Setup time"
                  value={formatDuration(result.metrics.setup_minutes)}
                  hint={`operating cost ${formatNumber(result.metrics.operating_cost)}`}
                />
              </div>

              {comparisonRows.length > 0 && (
                <Card
                  title="Baseline comparison"
                  subtitle={`FCFS baseline vs. this plan — ${result.comparison?.improved_metrics ?? 0} metrics improved, ${result.comparison?.regressed_metrics ?? 0} regressed`}
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
                          <th className="text-right">Change</th>
                          <th>Verdict</th>
                        </tr>
                      </thead>
                      <tbody>
                        {comparisonRows.map((row) => (
                          <tr key={row.metric}>
                            <td>{row.metric}</td>
                            <td className="text-right font-mono text-xs">
                              {formatNumber(row.baseline)}
                            </td>
                            <td className="text-right font-mono text-xs">
                              {formatNumber(row.optimised)}
                            </td>
                            <td className="text-right font-mono text-xs">
                              {row.delta > 0 ? '+' : ''}
                              {formatNumber(row.delta)}
                            </td>
                            <td className="text-right font-mono text-xs">
                              {row.delta_pct === null ? '—' : `${row.delta_pct > 0 ? '+' : ''}${row.delta_pct.toFixed(1)}%`}
                            </td>
                            <td>
                              <Verdict verdict={row.verdict} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              )}
            </>
          ) : null}
        </div>
      </div>

      <Card
        title="Compare stored plan versions"
        subtitle="Pick two or more versions; the first is the baseline"
        bodyClassName="p-0"
      >
        <div className="flex flex-wrap items-center gap-3 border-b border-steel-200 p-4">
          {(schedules.data ?? []).map((schedule) => {
            const selected = compareSelection.includes(schedule.id)
            return (
              <button
                key={schedule.id}
                type="button"
                className={`btn btn-sm ${selected ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() =>
                  setCompareSelection((prev) =>
                    prev.includes(schedule.id)
                      ? prev.filter((id) => id !== schedule.id)
                      : [...prev, schedule.id],
                  )
                }
              >
                {schedule.code} · {schedule.source.toUpperCase()}
              </button>
            )
          })}
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={compareSelection.length < 2 || compare.isPending}
            onClick={() => compare.mutate(compareSelection)}
          >
            <Sigma size={14} /> Compare {compareSelection.length} versions
          </button>
        </div>

        {compare.isError && <div className="p-4"><ErrorBanner message={errorMessage(compare.error)} /></div>}

        {compare.data?.comparisons?.map((comparison, index) => (
          <div key={index} className="border-b border-steel-100 last:border-0">
            <p className="px-4 pt-3 text-xs font-semibold uppercase tracking-wide text-steel-500">
              {comparison.improved_metrics} improved · {comparison.regressed_metrics} regressed
            </p>
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th className="text-right">Baseline</th>
                    <th className="text-right">Optimised</th>
                    <th className="text-right">Delta</th>
                    <th>Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.rows.map((row) => (
                    <tr key={row.metric}>
                      <td>{row.metric}</td>
                      <td className="text-right font-mono text-xs">{formatNumber(row.baseline)}</td>
                      <td className="text-right font-mono text-xs">{formatNumber(row.optimised)}</td>
                      <td className="text-right font-mono text-xs">
                        {row.delta > 0 ? '+' : ''}
                        {formatNumber(row.delta)}
                      </td>
                      <td>
                        <Verdict verdict={row.verdict} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </Card>
    </div>
  )
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card p-4">
      <p className="stat-label">{label}</p>
      <p className="stat-value mt-1.5">{value}</p>
      {hint && <p className="mt-1 text-xs text-steel-500">{hint}</p>}
    </div>
  )
}

function Verdict({ verdict }: { verdict: string; direction?: string }) {
  if (verdict === 'better') {
    return (
      <Badge tone="green">
        <ArrowDownRight size={13} /> better
      </Badge>
    )
  }
  if (verdict === 'worse') {
    return (
      <Badge tone="red">
        <ArrowUpRight size={13} /> worse
      </Badge>
    )
  }
  return (
    <Badge tone="slate">
      <Minus size={13} /> equal
    </Badge>
  )
}
