import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Clock,
  Gauge,
  Package,
  TrendingUp,
  Wrench,
} from 'lucide-react'
import { api } from '@/api/client'
import { Badge, Card, EmptyState, ErrorBanner, Spinner, Stat } from '@/components/ui'
import { formatDuration, formatNumber, formatPercent, formatDateTime } from '@/lib/format'
import type { Metrics, Schedule } from '@/api/types'

interface DashboardResponse {
  work_orders: { by_status: Record<string, number>; total: number; total_quantity: number; open: number }
  resources: {
    machines: number
    operators: number
    materials: number
    low_stock_materials: { sku: string; on_hand: number; safety_stock: number }[]
  }
  active_schedule: {
    id: number
    code: string
    name: string
    source: string
    status: string
    created_at: string
    metrics: Metrics
    validation: { valid?: boolean; error_count?: number }
  } | null
  recent_events: { id: number; kind: string; severity: string; title: string; created_at: string }[]
}

export function DashboardPage() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dashboard'],
    queryFn: async () => (await api.get<DashboardResponse>('/dashboard')).data,
  })

  const schedules = useQuery({
    queryKey: ['schedules', 'recent'],
    queryFn: async () => (await api.get<Schedule[]>('/schedules?limit=8')).data,
  })

  if (isLoading) return <Spinner label="Loading the plant overview…" />
  if (error) return <ErrorBanner message="Could not load the dashboard." onRetry={() => refetch()} />
  if (!data) return null

  const metrics = data.active_schedule?.metrics
  const orders = metrics?.orders ?? []
  const lateOrders = orders.filter((o) => o.is_late)
  const machines = metrics?.machines ?? []
  const bottleneck = machines.length ? machines[0] : null

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-steel-900">Plant overview</h1>
          <p className="text-sm text-steel-500">
            Live operational status of the production plan and the shop floor.
          </p>
        </div>
        {data.active_schedule ? (
          <div className="text-right text-xs text-steel-500">
            <p className="font-medium text-steel-700">{data.active_schedule.name}</p>
            <p>
              {data.active_schedule.source.toUpperCase()} ·{' '}
              {formatDateTime(data.active_schedule.created_at)}
            </p>
          </div>
        ) : null}
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="On-time delivery"
          value={metrics ? formatPercent(metrics.on_time_delivery_pct) : '—'}
          tone={metrics && metrics.on_time_delivery_pct >= 95 ? 'good' : metrics && metrics.on_time_delivery_pct < 80 ? 'bad' : 'default'}
          hint={metrics ? `${metrics.orders_on_time} of ${metrics.orders_scheduled} orders` : 'No plan yet'}
          icon={<CheckCircle2 size={16} />}
        />
        <Stat
          label="Weighted tardiness"
          value={metrics ? formatNumber(metrics.weighted_tardiness) : '—'}
          tone={metrics && metrics.weighted_tardiness > 0 ? 'warn' : 'good'}
          hint={metrics ? `${formatDuration(metrics.tardiness_minutes)} total delay` : 'No plan yet'}
          icon={<Clock size={16} />}
        />
        <Stat
          label="Machine utilisation"
          value={metrics ? formatPercent(metrics.machine_utilisation_pct) : '—'}
          hint={metrics ? `of ${formatNumber(metrics.machine_available_minutes)} available min` : 'No plan yet'}
          icon={<Gauge size={16} />}
        />
        <Stat
          label="Setup time"
          value={metrics ? formatDuration(metrics.setup_minutes) : '—'}
          hint={metrics ? `${formatNumber(metrics.setup_minutes)} changeover minutes` : 'No plan yet'}
          icon={<Wrench size={16} />}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Production workload"
          subtitle="Open work orders by status and the current delivery risk"
        >
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Open orders" value={formatNumber(data.work_orders.open)} hint={`${data.work_orders.total} total`} />
            <Stat label="Units ordered" value={formatNumber(data.work_orders.total_quantity)} />
            <Stat
              label="Late orders"
              value={formatNumber(lateOrders.length)}
              tone={lateOrders.length > 0 ? 'bad' : 'good'}
              hint={lateOrders.length ? 'Due date exceeded' : 'All within due date'}
            />
          </div>

          <div className="mt-5">
            <h3 className="mb-2 text-sm font-semibold text-steel-700">Orders needing attention</h3>
            {lateOrders.length === 0 ? (
              <EmptyState
                title="No late orders"
                description="Every scheduled order completes within its due date."
                icon={<CheckCircle2 size={22} />}
              />
            ) : (
              <div className="overflow-hidden rounded-lg border border-steel-200">
                <table className="table-base">
                  <thead>
                    <tr>
                      <th>Order</th>
                      <th>Priority</th>
                      <th>Due</th>
                      <th>Completion</th>
                      <th className="text-right">Tardiness</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lateOrders.slice(0, 8).map((order) => (
                      <tr key={order.work_order_id}>
                        <td className="font-mono text-xs">{order.work_order_code}</td>
                        <td>
                          <Badge tone={order.priority >= 4 ? 'red' : 'amber'}>P{order.priority}</Badge>
                        </td>
                        <td className="text-xs">{formatDateTime(order.due_at)}</td>
                        <td className="text-xs">{formatDateTime(order.completion)}</td>
                        <td className="text-right font-mono text-xs text-danger-600">
                          {formatDuration(order.tardiness_minutes)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>

        <div className="space-y-6">
          <Card title="Capacity" subtitle="Machines, labour and materials">
            <ul className="space-y-3 text-sm">
              <li className="flex items-center justify-between">
                <span className="inline-flex items-center gap-2 text-steel-600">
                  <Wrench size={15} /> Active machines
                </span>
                <span className="font-mono font-semibold">{data.resources.machines}</span>
              </li>
              <li className="flex items-center justify-between">
                <span className="inline-flex items-center gap-2 text-steel-600">
                  <Activity size={15} /> Operators
                </span>
                <span className="font-mono font-semibold">{data.resources.operators}</span>
              </li>
              <li className="flex items-center justify-between">
                <span className="inline-flex items-center gap-2 text-steel-600">
                  <Package size={15} /> Material SKUs
                </span>
                <span className="font-mono font-semibold">{data.resources.materials}</span>
              </li>
            </ul>

            {bottleneck && (
              <div className="mt-4 rounded-lg border border-steel-200 bg-steel-50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-steel-500">
                  Most loaded resource
                </p>
                <p className="mt-1 text-sm font-semibold text-steel-900">{bottleneck.machine_code}</p>
                <p className="text-xs text-steel-500">
                  {formatPercent(bottleneck.utilisation_pct)} utilisation ·{' '}
                  {formatDuration(bottleneck.busy_minutes)} busy
                </p>
              </div>
            )}

            {data.resources.low_stock_materials.length > 0 && (
              <div className="mt-4">
                <p className="text-xs font-semibold uppercase tracking-wide text-warn-600">
                  Below safety stock
                </p>
                <ul className="mt-2 space-y-1 text-xs text-steel-600">
                  {data.resources.low_stock_materials.map((material) => (
                    <li key={material.sku} className="flex justify-between">
                      <span className="font-mono">{material.sku}</span>
                      <span>
                        {formatNumber(material.on_hand)} / {formatNumber(material.safety_stock)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Card>

          <Card title="Recent events" subtitle="Operational disruptions and changes">
            {data.recent_events.length === 0 ? (
              <p className="text-sm text-steel-500">No operational events recorded yet.</p>
            ) : (
              <ul className="space-y-2.5">
                {data.recent_events.map((event) => (
                  <li key={event.id} className="flex items-start gap-2.5 text-sm">
                    <AlertTriangle
                      size={15}
                      className={
                        event.severity === 'critical'
                          ? 'mt-0.5 text-danger-500'
                          : 'mt-0.5 text-warn-500'
                      }
                    />
                    <div className="min-w-0">
                      <p className="truncate font-medium text-steel-800">{event.title}</p>
                      <p className="text-xs text-steel-500">{formatDateTime(event.created_at)}</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      <Card
        title="Plan versions"
        subtitle="Every run is stored as an immutable, auditable version"
        actions={
          <Link to="/optimizer" className="btn btn-primary btn-sm">
            <TrendingUp size={15} /> Run the optimizer
          </Link>
        }
        bodyClassName="p-0"
      >
        {schedules.isLoading ? (
          <Spinner />
        ) : (schedules.data ?? []).length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="No plan generated yet"
              description="Run the FCFS baseline or the CP-SAT optimizer to create the first production plan."
              icon={<CalendarClock size={22} />}
              action={
                <Link to="/optimizer" className="btn btn-primary btn-sm">
                  Open the optimizer
                </Link>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Plan</th>
                  <th>Engine</th>
                  <th>Status</th>
                  <th>On time</th>
                  <th>Weighted tardiness</th>
                  <th>Validation</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {(schedules.data ?? []).map((schedule) => (
                  <tr key={schedule.id}>
                    <td>
                      <Link to={`/schedule?schedule=${schedule.id}`} className="font-medium text-accent-700 hover:underline">
                        {schedule.name}
                      </Link>
                      <p className="font-mono text-[11px] text-steel-400">{schedule.code}</p>
                    </td>
                    <td>
                      <Badge tone={schedule.source === 'cpsat' ? 'blue' : 'slate'}>
                        {schedule.source.toUpperCase()}
                      </Badge>
                    </td>
                    <td>
                      <Badge tone={schedule.is_published ? 'green' : 'slate'}>
                        {schedule.status}
                      </Badge>
                    </td>
                    <td className="font-mono text-xs">
                      {schedule.metrics?.on_time_delivery_pct !== undefined
                        ? formatPercent(schedule.metrics.on_time_delivery_pct)
                        : '—'}
                    </td>
                    <td className="font-mono text-xs">
                      {schedule.metrics?.weighted_tardiness !== undefined
                        ? formatNumber(schedule.metrics.weighted_tardiness)
                        : '—'}
                    </td>
                    <td>
                      {schedule.validation?.valid === false ? (
                        <Badge tone="red">invalid</Badge>
                      ) : schedule.validation?.valid === true ? (
                        <Badge tone="green">valid</Badge>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="text-xs text-steel-500">{formatDateTime(schedule.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
