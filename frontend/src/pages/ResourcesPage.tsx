import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Factory, GitBranch, Package, Plus, UserRound, Wrench } from 'lucide-react'
import { api } from '@/api/client'
import { Badge, Card, EmptyState, ErrorBanner, Field, Modal, Spinner } from '@/components/ui'
import { formatDateTime, formatNumber } from '@/lib/format'
import { errorMessage } from '@/api/client'
import { useAuth } from '@/auth/useAuth'
import type { Calendar, Machine, Material, Operator, Routing, RoutingOperation } from '@/api/types'

const TABS = [
  { key: 'machines', label: 'Machines', icon: Factory },
  { key: 'operators', label: 'Operators', icon: UserRound },
  { key: 'materials', label: 'Materials', icon: Package },
  { key: 'calendars', label: 'Calendars', icon: CalendarClock },
  { key: 'routings', label: 'Routings', icon: GitBranch },
] as const

type TabKey = (typeof TABS)[number]['key']

type ResourceKind = 'machine' | 'operator' | 'material'

export function ResourcesPage() {
  const [tab, setTab] = useState<TabKey>('machines')
  const [createKind, setCreateKind] = useState<ResourceKind | null>(null)
  const { canPlan } = useAuth()

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-steel-900">Resources</h1>
          <p className="text-sm text-steel-500">
            Machines, labour, materials and production calendars — the hard constraints the solver
            must respect.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setTab('machines'); setCreateKind('machine') }} disabled={!canPlan}>
            <Plus size={14} /> Machine
          </button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setTab('operators'); setCreateKind('operator') }} disabled={!canPlan}>
            <Plus size={14} /> Operator
          </button>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => { setTab('materials'); setCreateKind('material') }} disabled={!canPlan}>
            <Plus size={14} /> Material
          </button>
        </div>
      </header>

      <div className="flex flex-wrap gap-2">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`btn btn-sm ${tab === item.key ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setTab(item.key)}
          >
            <item.icon size={15} />
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'machines' && <MachinesPanel canPlan={canPlan} />}
      {tab === 'operators' && <OperatorsPanel canPlan={canPlan} />}
      {tab === 'materials' && <MaterialsPanel canPlan={canPlan} />}
      {tab === 'calendars' && <CalendarsPanel canPlan={canPlan} />}
      {tab === 'routings' && <RoutingsPanel canPlan={canPlan} />}
      {createKind && <ResourceCreateModal kind={createKind} onClose={() => setCreateKind(null)} />}
    </div>
  )
}

function MachinesPanel({ canPlan }: { canPlan: boolean }) {
  const queryClient = useQueryClient()
  const [target, setTarget] = useState<Machine | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['machines'],
    queryFn: async () => (await api.get<Machine[]>('/machines')).data,
  })
  const toggle = useMutation({
    mutationFn: async (machine: Machine) =>
      api.patch(`/machines/${machine.id}`, { is_active: !machine.is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['machines'] }),
  })
  if (isLoading) return <Spinner />
  if (!data?.length) {
    return (
      <Card>
        <EmptyState title="No machines" description="Add machines to make the shop floor plannable." icon={<Factory size={22} />} />
      </Card>
    )
  }
  return (
    <>
    <Card bodyClassName="p-0">
      <div className="overflow-x-auto">
        <table className="table-base">
          <thead>
            <tr>
              <th>Code</th>
              <th>Name</th>
              <th>Type</th>
              <th>Families</th>
              <th>Calendar</th>
              <th className="text-right">Setup (min)</th>
              <th className="text-right">Cost/h</th>
              <th>Downtimes</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {data.map((machine) => (
              <tr key={machine.id}>
                <td className="font-mono text-xs font-semibold">{machine.code}</td>
                <td className="text-xs">{machine.name}</td>
                <td className="text-xs text-steel-600">{machine.machine_type}</td>
                <td className="text-xs text-steel-500">
                  {machine.supported_families.join(', ') || '—'}
                </td>
                <td className="font-mono text-xs text-steel-500">{machine.calendar_code ?? '—'}</td>
                <td className="text-right font-mono text-xs">{formatNumber(machine.initial_setup_minutes)}</td>
                <td className="text-right font-mono text-xs">{formatNumber(machine.hourly_cost)}</td>
                <td className="text-xs">
                  {machine.downtime_count > 0 ? (
                    <Badge tone="amber">{machine.downtime_count}</Badge>
                  ) : (
                    <span className="text-steel-400">0</span>
                  )}
                </td>
                <td>
                  <Badge tone={machine.is_active ? 'green' : 'slate'}>
                    {machine.is_active ? 'active' : 'inactive'}
                  </Badge>
                </td>
                <td>
                  <div className="flex gap-1">
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => setTarget(machine)} disabled={!canPlan}>
                      <Wrench size={13} /> Downtime
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => toggle.mutate(machine)} disabled={!canPlan || toggle.isPending}>
                      {machine.is_active ? 'Deactivate' : 'Activate'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
    {target && <ResourceActionModal kind="machine-downtime" id={target.id} name={target.code} onClose={() => setTarget(null)} />}
  </>
  )
}

function OperatorsPanel({ canPlan }: { canPlan: boolean }) {
  const queryClient = useQueryClient()
  const [target, setTarget] = useState<Operator | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['operators'],
    queryFn: async () => (await api.get<Operator[]>('/operators')).data,
  })
  const toggle = useMutation({
    mutationFn: async (operator: Operator) =>
      api.patch(`/operators/${operator.id}`, { is_active: !operator.is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['operators'] }),
  })
  if (isLoading) return <Spinner />
  if (!data?.length) {
    return (
      <Card>
        <EmptyState title="No operators" description="Add operators to model labour capacity." icon={<UserRound size={22} />} />
      </Card>
    )
  }
  return (
    <>
    <Card bodyClassName="p-0">
      <div className="overflow-x-auto">
        <table className="table-base">
          <thead>
            <tr>
              <th>Code</th>
              <th>Name</th>
              <th>Skills</th>
              <th>Certified for</th>
              <th>Calendar</th>
              <th className="text-right">Regular min/wk</th>
              <th className="text-right">OT limit</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {data.map((operator) => (
              <tr key={operator.id}>
                <td className="font-mono text-xs font-semibold">{operator.code}</td>
                <td className="text-xs">{operator.name}</td>
                <td className="text-xs text-steel-600">{operator.skills.join(', ') || '—'}</td>
                <td className="text-xs text-steel-500">
                  {operator.eligible_machine_types.join(', ') || '—'}
                </td>
                <td className="font-mono text-xs text-steel-500">#{operator.calendar_id}</td>
                <td className="text-right font-mono text-xs">{formatNumber(operator.weekly_regular_minutes)}</td>
                <td className="text-right font-mono text-xs">{formatNumber(operator.weekly_overtime_limit_minutes)}</td>
                <td>
                  <Badge tone={operator.is_active ? 'green' : 'slate'}>
                    {operator.is_active ? 'active' : 'inactive'}
                  </Badge>
                </td>
                <td>
                  <div className="flex gap-1">
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => setTarget(operator)} disabled={!canPlan}>Absence</button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => toggle.mutate(operator)} disabled={!canPlan || toggle.isPending}>
                      {operator.is_active ? 'Deactivate' : 'Activate'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
    {target && <ResourceActionModal kind="operator-absence" id={target.id} name={target.code} onClose={() => setTarget(null)} />}
    </>
  )
}

function MaterialsPanel({ canPlan }: { canPlan: boolean }) {
  const queryClient = useQueryClient()
  const [target, setTarget] = useState<Material | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['materials'],
    queryFn: async () => (await api.get<Material[]>('/materials')).data,
  })
  const toggle = useMutation({
    mutationFn: async (material: Material) =>
      api.patch(`/materials/${material.id}`, { is_active: !material.is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['materials'] }),
  })
  if (isLoading) return <Spinner />
  if (!data?.length) {
    return (
      <Card>
        <EmptyState title="No materials" description="Add materials to constrain the plan." icon={<Package size={22} />} />
      </Card>
    )
  }
  return (
    <>
    <Card bodyClassName="p-0">
      <div className="overflow-x-auto">
        <table className="table-base">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Name</th>
              <th>Unit</th>
              <th className="text-right">On hand</th>
              <th className="text-right">Safety stock</th>
              <th className="text-right">Total available</th>
              <th>Upcoming receipts</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {data.map((material) => (
              <tr key={material.id}>
                <td className="font-mono text-xs font-semibold">{material.sku}</td>
                <td className="text-xs">{material.name}</td>
                <td className="text-xs text-steel-500">{material.unit}</td>
                <td className="text-right font-mono text-xs">{formatNumber(material.on_hand)}</td>
                <td className="text-right font-mono text-xs">{formatNumber(material.safety_stock)}</td>
                <td className="text-right font-mono text-xs">{formatNumber(material.total_available)}</td>
                <td className="text-xs text-steel-600">
                  {material.receipts.length === 0 ? (
                    <span className="text-steel-400">none</span>
                  ) : (
                    <ul className="space-y-0.5">
                      {material.receipts.map((receipt) => (
                        <li key={receipt.id} className="font-mono text-[11px]">
                          {formatDateTime(receipt.expected_at)} · +{formatNumber(receipt.quantity)}
                          {receipt.is_confirmed ? '' : ' (unconfirmed)'}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td>
                  <div className="flex flex-col items-start gap-1">
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => setTarget(material)} disabled={!canPlan}>Add receipt</button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => toggle.mutate(material)} disabled={!canPlan || toggle.isPending}>
                      {material.is_active ? 'Deactivate' : 'Activate'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
    {target && <ResourceActionModal kind="material-receipt" id={target.id} name={target.sku} onClose={() => setTarget(null)} />}
    </>
  )
}

function CalendarsPanel({ canPlan }: { canPlan: boolean }) {
  const [target, setTarget] = useState<Calendar | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['calendars'],
    queryFn: async () => (await api.get<Calendar[]>('/calendars')).data,
  })
  if (isLoading) return <Spinner />
  if (!data?.length) {
    return (
      <Card>
        <EmptyState title="No calendars" description="Calendars define working time per resource." icon={<CalendarClock size={22} />} />
      </Card>
    )
  }
  return (
    <>
    <div className="grid gap-4 lg:grid-cols-2">
      {data.map((calendar) => (
        <Card
          key={calendar.id}
          title={calendar.name}
          subtitle={`${calendar.code} · ${calendar.timezone}`}
          actions={
            <>
              <Badge tone={calendar.is_continuous ? 'blue' : 'slate'}>
                {calendar.is_continuous ? 'continuous' : 'shift based'}
              </Badge>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setTarget(calendar)} disabled={!canPlan}>
                <Plus size={13} /> Exception
              </button>
            </>
          }
        >
          <p className="text-xs text-steel-500">{calendar.description}</p>
          <div className="mt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-steel-500">Shifts</p>
            <ul className="mt-1.5 space-y-1 text-xs text-steel-700">
              {calendar.shifts.map((shift) => (
                <li key={shift.id} className="flex justify-between">
                  <span>
                    {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][shift.day_of_week] ?? shift.day_of_week}
                  </span>
                  <span className="font-mono">
                    {minutesToClock(shift.start_minute)} – {minutesToClock(shift.end_minute)}
                    {shift.is_overnight ? ' (+1d)' : ''}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          {calendar.breaks.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-steel-500">Breaks</p>
              <ul className="mt-1.5 space-y-1 text-xs text-steel-700">
                {calendar.breaks.map((item) => (
                  <li key={item.id} className="flex justify-between">
                    <span>{item.label ?? 'Break'}</span>
                    <span className="font-mono">
                      {minutesToClock(item.start_minute)} – {minutesToClock(item.end_minute)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {calendar.exceptions.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-steel-500">Exceptions</p>
              <ul className="mt-1.5 space-y-1 text-xs text-steel-700">
                {calendar.exceptions.map((item) => (
                  <li key={item.id} className="flex justify-between">
                    <span>
                      {item.day} · {item.label ?? item.kind}
                    </span>
                    <span className="font-mono">{item.kind}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      ))}
    </div>
    {target && <ResourceActionModal kind="calendar-exception" id={target.id} name={target.code} onClose={() => setTarget(null)} />}
    </>
  )
}

function RoutingsPanel({ canPlan }: { canPlan: boolean }) {
  const [target, setTarget] = useState<RoutingOperation | null>(null)
  const { data, isLoading, error } = useQuery({
    queryKey: ['routings'],
    queryFn: async () => (await api.get<Routing[]>('/routings')).data,
  })
  if (isLoading) return <Spinner label="Loading routings…" />
  if (error) return <ErrorBanner message={errorMessage(error)} />
  if (!data?.length) {
    return (
      <Card>
        <EmptyState title="No routings" description="Attach routing operations to products to model alternatives, transfers, batches and parallel steps." icon={<GitBranch size={22} />} />
      </Card>
    )
  }
  return (
    <>
      <div className="grid gap-4">
        {data.map((routing) => (
          <Card key={routing.id} title={routing.name} subtitle={`${routing.code} · version ${routing.version}`}>
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Step</th><th>Operation</th><th>Machine type</th><th>Skills</th>
                    <th>Batch</th><th>Transfer</th><th>Alternatives</th><th>Parallel group</th><th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {routing.operations.map((operation) => (
                    <tr key={operation.id}>
                      <td className="font-mono text-xs">{operation.sequence}</td>
                      <td className="text-xs font-medium">{operation.name}</td>
                      <td className="text-xs">{operation.machine_type}</td>
                      <td className="text-xs text-steel-600">{operation.required_skills.join(', ') || '—'}</td>
                      <td className="text-xs text-steel-600">
                        {operation.batch_size ? `${operation.batch_size} units · ${formatNumber(operation.batch_setup_minutes)} min changeover` : 'single lot'}
                      </td>
                      <td className="font-mono text-xs">{formatNumber(operation.transfer_time_minutes)} min</td>
                      <td className="text-xs">
                        {operation.allow_alternative_machines ? 'Allowed' : 'Preferred only'}
                        {operation.preferred_machine_ids.length > 0 && ` · ${operation.preferred_machine_ids.join(', ')}`}
                      </td>
                      <td className="text-xs">{operation.is_parallel_group ?? '—'}</td>
                      <td>
                        <button type="button" className="btn btn-secondary btn-sm" disabled={!canPlan} onClick={() => setTarget(operation)}>Edit rules</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-xs text-steel-500">
              Batch sizing adds internal changeover minutes to the indivisible operation duration; operations sharing a parallel-group label may proceed concurrently.
            </p>
          </Card>
        ))}
      </div>
      {target && <RoutingOperationModal operation={target} onClose={() => setTarget(null)} />}
    </>
  )
}

function RoutingOperationModal({
  operation,
  onClose,
}: {
  operation: RoutingOperation
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [batchSize, setBatchSize] = useState(operation.batch_size?.toString() ?? '')
  const [batchSetup, setBatchSetup] = useState(String(operation.batch_setup_minutes))
  const [transfer, setTransfer] = useState(String(operation.transfer_time_minutes))
  const [preferredMachines, setPreferredMachines] = useState(operation.preferred_machine_ids.join(', '))
  const [alternatives, setAlternatives] = useState(operation.allow_alternative_machines)
  const [parallelGroup, setParallelGroup] = useState(operation.is_parallel_group ?? '')
  const [error, setError] = useState<string | null>(null)
  const update = useMutation({
    mutationFn: async () => (await api.patch<RoutingOperation>(`/routings/operations/${operation.id}`, {
      batch_size: batchSize === '' ? null : Number(batchSize),
      batch_setup_minutes: Number(batchSetup),
      transfer_time_minutes: Number(transfer),
      preferred_machine_ids: preferredMachines.split(',').map((value) => value.trim()).filter(Boolean).map(Number),
      allow_alternative_machines: alternatives,
      is_parallel_group: parallelGroup.trim() || null,
    })).data,
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: ['routings'] })
      queryClient.setQueryData<Routing[]>(['routings'], (old) =>
        (old ?? []).map((routing) => ({
          ...routing,
          operations: routing.operations.map((item) => item.id === updated.id ? updated : item),
        })),
      )
      onClose()
    },
    onError: (err) => setError(errorMessage(err)),
  })
  return (
    <Modal
      open
      title={`Routing rules — ${operation.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={update.isPending} onClick={() => update.mutate()}>{update.isPending ? 'Saving…' : 'Save routing rules'}</button>
        </>
      }
    >
      {error && <ErrorBanner message={error} />}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Maximum batch size" hint="Leave empty to process the work order as one lot."><input className="input" type="number" min="1" step="1" value={batchSize} onChange={(e) => setBatchSize(e.target.value)} /></Field>
        <Field label="Changeover between batches (min)"><input className="input" type="number" min="0" step="1" value={batchSetup} onChange={(e) => setBatchSetup(e.target.value)} /></Field>
        <Field label="Transfer time to next step (min)"><input className="input" type="number" min="0" step="1" value={transfer} onChange={(e) => setTransfer(e.target.value)} /></Field>
        <Field label="Preferred machine IDs" hint="Comma-separated machine IDs; blank allows any eligible machine."><input className="input" value={preferredMachines} onChange={(e) => setPreferredMachines(e.target.value)} placeholder="1, 2" /></Field>
        <Field label="Parallel group" hint="Operations with the same label may run concurrently."><input className="input" value={parallelGroup} onChange={(e) => setParallelGroup(e.target.value)} placeholder="e.g. prep-a" /></Field>
        <Field label="Allow alternative machines"><input type="checkbox" checked={alternatives} onChange={(e) => setAlternatives(e.target.checked)} /></Field>
      </div>
    </Modal>
  )
}

type ResourceActionKind =
  | 'machine-downtime'
  | 'operator-absence'
  | 'material-receipt'
  | 'calendar-exception'

function ResourceActionModal({
  kind,
  id,
  name,
  onClose,
}: {
  kind: ResourceActionKind
  id: number
  name: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [startsAt, setStartsAt] = useState(() => new Date().toISOString().slice(0, 16))
  const [endsAt, setEndsAt] = useState(() => new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString().slice(0, 16))
  const [expectedAt, setExpectedAt] = useState(() => new Date(Date.now() + 3 * 86_400_000).toISOString().slice(0, 16))
  const [quantity, setQuantity] = useState('100')
  const [reference, setReference] = useState('')
  const [reason, setReason] = useState('')
  const [kindValue, setKindValue] = useState('maintenance')
  const [day, setDay] = useState(() => new Date().toISOString().slice(0, 10))
  const [exceptionKind, setExceptionKind] = useState('holiday')
  const [startMinute, setStartMinute] = useState('')
  const [endMinute, setEndMinute] = useState('')
  const [label, setLabel] = useState('')
  const [error, setError] = useState<string | null>(null)

  const action = useMutation({
    mutationFn: async () => {
      if (kind === 'machine-downtime') {
        return api.post(`/machines/${id}/downtimes`, {
          starts_at: new Date(startsAt).toISOString(),
          ends_at: new Date(endsAt).toISOString(),
          kind: kindValue,
          reason: reason || null,
        })
      }
      if (kind === 'operator-absence') {
        return api.post(`/operators/${id}/absences`, {
          starts_at: new Date(startsAt).toISOString(),
          ends_at: new Date(endsAt).toISOString(),
          reason: reason || null,
        })
      }
      if (kind === 'material-receipt') {
        return api.post(`/materials/${id}/receipts`, {
          expected_at: new Date(expectedAt).toISOString(),
          quantity: Number(quantity),
          reference: reference || null,
          is_confirmed: false,
        })
      }
      const payload: Record<string, string | number | null> = {
        day,
        kind: exceptionKind,
        label: label || null,
        start_minute: startMinute === '' ? null : Number(startMinute),
        end_minute: endMinute === '' ? null : Number(endMinute),
      }
      return api.post(`/calendars/${id}/exceptions`, payload)
    },
    onSuccess: async (response) => {
      const key = kind === 'machine-downtime'
        ? 'machines'
        : kind === 'operator-absence'
          ? 'operators'
          : kind === 'material-receipt'
            ? 'materials'
            : 'calendars'
      await queryClient.invalidateQueries({ queryKey: [key] })
      if (kind === 'calendar-exception') {
        queryClient.setQueryData<Calendar[]>(['calendars'], (old) =>
          (old ?? []).map((calendar) => calendar.id === id ? response.data : calendar),
        )
      }
      await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      onClose()
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const titles: Record<ResourceActionKind, string> = {
    'machine-downtime': `Add maintenance / breakdown — ${name}`,
    'operator-absence': `Record operator absence — ${name}`,
    'material-receipt': `Add material receipt — ${name}`,
    'calendar-exception': `Add calendar exception — ${name}`,
  }
  const valid = kind === 'material-receipt'
    ? Number(quantity) > 0 && Boolean(expectedAt)
    : kind === 'calendar-exception'
      ? Boolean(day) && Boolean(exceptionKind)
      : Boolean(startsAt && endsAt && new Date(endsAt) > new Date(startsAt))

  return (
    <Modal
      open
      title={titles[kind]}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!valid || action.isPending}
            onClick={() => { setError(null); action.mutate() }}
          >
            {action.isPending ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      {error && <ErrorBanner message={error} />}
      {(kind === 'machine-downtime' || kind === 'operator-absence') && (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Starts at"><input className="input" type="datetime-local" value={startsAt} onChange={(e) => setStartsAt(e.target.value)} /></Field>
            <Field label="Ends at"><input className="input" type="datetime-local" value={endsAt} onChange={(e) => setEndsAt(e.target.value)} /></Field>
          </div>
          {kind === 'machine-downtime' && (
            <Field label="Window type">
              <select className="input" value={kindValue} onChange={(e) => setKindValue(e.target.value)}>
                <option value="maintenance">Maintenance</option>
                <option value="breakdown">Breakdown</option>
                <option value="planned_stop">Planned stop</option>
                <option value="overhaul">Overhaul</option>
              </select>
            </Field>
          )}
          <Field label="Reason"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Optional note" /></Field>
        </div>
      )}
      {kind === 'material-receipt' && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Expected at"><input className="input" type="datetime-local" value={expectedAt} onChange={(e) => setExpectedAt(e.target.value)} /></Field>
          <Field label="Quantity"><input className="input" type="number" min="0.01" step="0.01" value={quantity} onChange={(e) => setQuantity(e.target.value)} /></Field>
          <Field label="Purchase order / reference"><input className="input" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="PO-1050" /></Field>
        </div>
      )}
      {kind === 'calendar-exception' && (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Date"><input className="input" type="date" value={day} onChange={(e) => setDay(e.target.value)} /></Field>
            <Field label="Exception type"><select className="input" value={exceptionKind} onChange={(e) => setExceptionKind(e.target.value)}><option value="holiday">Holiday (whole day)</option><option value="shutdown">Shutdown</option><option value="extra_shift">Extra shift</option></select></Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Start minute (optional)" hint="0–1439. Leave both empty for a full-day exception."><input className="input" type="number" min="0" max="2879" value={startMinute} onChange={(e) => setStartMinute(e.target.value)} /></Field>
            <Field label="End minute (optional)" hint="Must be later than the start minute."><input className="input" type="number" min="1" max="2880" value={endMinute} onChange={(e) => setEndMinute(e.target.value)} /></Field>
          </div>
          <Field label="Label"><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Plant shutdown" /></Field>
        </div>
      )}
    </Modal>
  )
}

function minutesToClock(minutes: number): string {
  const hour = Math.floor(minutes / 60) % 24
  const minute = minutes % 60
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

function ResourceCreateModal({
  kind,
  onClose,
}: {
  kind: ResourceKind
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const calendars = useQuery({
    queryKey: ['calendars'],
    queryFn: async () => (await api.get<Calendar[]>('/calendars')).data,
  })
  const [values, setValues] = useState<Record<string, string>>(() => {
    if (kind === 'machine') {
      return {
        code: '',
        name: '',
        machine_type: 'CNC',
        supported_families: 'A, B, C',
        capabilities: 'cnc',
        calendar_id: '',
        hourly_cost: '0',
        initial_setup_minutes: '0',
      } as Record<string, string>
    }
    if (kind === 'operator') {
      return {
        code: '',
        name: '',
        skills: '',
        certifications: '',
        eligible_machine_types: '',
        calendar_id: '',
        weekly_regular_minutes: '2400',
        weekly_overtime_limit_minutes: '600',
        hourly_cost: '0',
      } as Record<string, string>
    }
    return {
      sku: '',
      name: '',
      unit: 'pcs',
      on_hand: '0',
      safety_stock: '0',
      cost_per_unit: '0',
    } as Record<string, string>
  })
  const [error, setError] = useState<string | null>(null)
  const update = (key: string, value: string) => setValues((old) => ({ ...old, [key]: value }))
  const list = (key: string) =>
    (values[key] ?? '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)

  useEffect(() => {
    if (kind !== 'material' && !values.calendar_id && calendars.data?.[0]) {
      update('calendar_id', String(calendars.data[0].id))
    }
  }, [calendars.data, kind, values.calendar_id])

  const create = useMutation({
    mutationFn: async () => {
      if (kind === 'machine') {
        return api.post('/machines', {
          code: values.code.trim(),
          name: values.name.trim(),
          machine_type: values.machine_type.trim().toUpperCase(),
          supported_families: list('supported_families'),
          capabilities: list('capabilities'),
          calendar_id: Number(values.calendar_id),
          hourly_cost: Number(values.hourly_cost),
          initial_setup_minutes: Number(values.initial_setup_minutes),
        })
      }
      if (kind === 'operator') {
        return api.post('/operators', {
          code: values.code.trim(),
          name: values.name.trim(),
          skills: list('skills'),
          certifications: list('certifications'),
          eligible_machine_types: list('eligible_machine_types').map((value) => value.toUpperCase()),
          calendar_id: Number(values.calendar_id),
          weekly_regular_minutes: Number(values.weekly_regular_minutes),
          weekly_overtime_limit_minutes: Number(values.weekly_overtime_limit_minutes),
          hourly_cost: Number(values.hourly_cost),
        })
      }
      return api.post('/materials', {
        sku: values.sku.trim().toUpperCase(),
        name: values.name.trim(),
        unit: values.unit.trim(),
        on_hand: Number(values.on_hand),
        safety_stock: Number(values.safety_stock),
        cost_per_unit: Number(values.cost_per_unit),
      })
    },
    onSuccess: async (response) => {
      const key = kind === 'machine' ? 'machines' : kind === 'operator' ? 'operators' : 'materials'
      await queryClient.invalidateQueries({ queryKey: [key] })
      // Update the active list from the authoritative POST response as well as
      // invalidating it. This avoids a UI race with a very fast follow-up GET.
      queryClient.setQueryData<Array<{ id: number }>>([key], (old) => {
        const rows = old ?? []
        return [...rows.filter((row) => row.id !== response.data.id), response.data]
      })
      onClose()
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const title = kind === 'machine' ? 'Add machine' : kind === 'operator' ? 'Add operator' : 'Add material'
  const canSubmit = kind === 'material'
    ? Boolean(values.sku?.trim() && values.name?.trim() && values.unit?.trim())
    : Boolean(values.code?.trim() && values.name?.trim() && values.calendar_id)

  return (
    <Modal
      open
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!canSubmit || create.isPending || calendars.isLoading}
            onClick={() => {
              setError(null)
              create.mutate()
            }}
          >
            {create.isPending ? 'Saving…' : 'Save resource'}
          </button>
        </>
      }
    >
      {error && <ErrorBanner message={error} />}
      {kind === 'machine' && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Machine code"><input className="input" value={values.code} onChange={(e) => update('code', e.target.value)} placeholder="CNC-04" /></Field>
          <Field label="Machine name"><input className="input" value={values.name} onChange={(e) => update('name', e.target.value)} placeholder="CNC Mill 4" /></Field>
          <Field label="Machine type"><input className="input" value={values.machine_type} onChange={(e) => update('machine_type', e.target.value)} placeholder="CNC" /></Field>
          <Field label="Calendar"><select className="input" value={values.calendar_id} onChange={(e) => update('calendar_id', e.target.value)}><option value="">Select calendar…</option>{(calendars.data ?? []).map((calendar) => <option key={calendar.id} value={calendar.id}>{calendar.code} — {calendar.name}</option>)}</select></Field>
          <Field label="Supported families" hint="Comma-separated, e.g. A, B, C"><input className="input" value={values.supported_families} onChange={(e) => update('supported_families', e.target.value)} /></Field>
          <Field label="Capabilities" hint="Comma-separated tags"><input className="input" value={values.capabilities} onChange={(e) => update('capabilities', e.target.value)} /></Field>
          <Field label="Hourly cost"><input className="input" type="number" min="0" step="0.01" value={values.hourly_cost} onChange={(e) => update('hourly_cost', e.target.value)} /></Field>
          <Field label="Initial setup minutes"><input className="input" type="number" min="0" step="1" value={values.initial_setup_minutes} onChange={(e) => update('initial_setup_minutes', e.target.value)} /></Field>
        </div>
      )}
      {kind === 'operator' && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Operator code"><input className="input" value={values.code} onChange={(e) => update('code', e.target.value)} placeholder="OP-11" /></Field>
          <Field label="Operator name"><input className="input" value={values.name} onChange={(e) => update('name', e.target.value)} /></Field>
          <Field label="Skills" hint="Comma-separated tags"><input className="input" value={values.skills} onChange={(e) => update('skills', e.target.value)} placeholder="cnc-op, milling" /></Field>
          <Field label="Certifications" hint="Comma-separated"><input className="input" value={values.certifications} onChange={(e) => update('certifications', e.target.value)} /></Field>
          <Field label="Eligible machine types" hint="Comma-separated"><input className="input" value={values.eligible_machine_types} onChange={(e) => update('eligible_machine_types', e.target.value)} placeholder="CNC, WELD" /></Field>
          <Field label="Calendar"><select className="input" value={values.calendar_id} onChange={(e) => update('calendar_id', e.target.value)}><option value="">Select calendar…</option>{(calendars.data ?? []).map((calendar) => <option key={calendar.id} value={calendar.id}>{calendar.code} — {calendar.name}</option>)}</select></Field>
          <Field label="Regular minutes / week"><input className="input" type="number" min="0" value={values.weekly_regular_minutes} onChange={(e) => update('weekly_regular_minutes', e.target.value)} /></Field>
          <Field label="Overtime limit / week"><input className="input" type="number" min="0" value={values.weekly_overtime_limit_minutes} onChange={(e) => update('weekly_overtime_limit_minutes', e.target.value)} /></Field>
          <Field label="Hourly cost"><input className="input" type="number" min="0" step="0.01" value={values.hourly_cost} onChange={(e) => update('hourly_cost', e.target.value)} /></Field>
        </div>
      )}
      {kind === 'material' && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="SKU"><input className="input" value={values.sku} onChange={(e) => update('sku', e.target.value)} placeholder="RM-ALU-2" /></Field>
          <Field label="Material name"><input className="input" value={values.name} onChange={(e) => update('name', e.target.value)} /></Field>
          <Field label="Unit"><input className="input" value={values.unit} onChange={(e) => update('unit', e.target.value)} placeholder="kg, pcs" /></Field>
          <Field label="On hand"><input className="input" type="number" min="0" step="0.01" value={values.on_hand} onChange={(e) => update('on_hand', e.target.value)} /></Field>
          <Field label="Safety stock"><input className="input" type="number" min="0" step="0.01" value={values.safety_stock} onChange={(e) => update('safety_stock', e.target.value)} /></Field>
          <Field label="Cost per unit"><input className="input" type="number" min="0" step="0.01" value={values.cost_per_unit} onChange={(e) => update('cost_per_unit', e.target.value)} /></Field>
        </div>
      )}
    </Modal>
  )
}
