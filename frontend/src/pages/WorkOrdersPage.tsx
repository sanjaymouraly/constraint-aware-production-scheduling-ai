import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Download,
  FileUp,
  Loader2,
  Pencil,
  Plus,
  Search,
  Trash2,
  Upload,
} from 'lucide-react'
import { api, errorMessage } from '@/api/client'
import { Badge, Card, EmptyState, ErrorBanner, Field, Modal, Spinner } from '@/components/ui'
import { formatDateTime, formatNumber } from '@/lib/format'
import type { WorkOrder } from '@/api/types'
import { useAuth } from '@/auth/useAuth'

interface Customer {
  id: number
  code: string
  name: string
}
interface Product {
  id: number
  sku: string
  name: string
}

interface ImportResult {
  rows: number
  valid: number
  errors: { row: number; message: string; data: Record<string, string> }[]
  created: number
  dry_run: boolean
}

const STATUSES = ['planned', 'released', 'in_progress', 'completed', 'cancelled']

export function WorkOrdersPage() {
  const queryClient = useQueryClient()
  const { canPlan } = useAuth()
  const fileInput = useRef<HTMLInputElement>(null)
  const validateOnlyRef = useRef(false)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [editing, setEditing] = useState<WorkOrder | null>(null)
  const [creating, setCreating] = useState(false)
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const orders = useQuery({
    queryKey: ['work-orders', search, status],
    queryFn: async () => {
      const params = new URLSearchParams()
      if (search) params.set('search', search)
      if (status) params.set('status', status)
      params.set('limit', '200')
      return (await api.get<WorkOrder[]>(`/work-orders?${params.toString()}`)).data
    },
  })

  const customers = useQuery({
    queryKey: ['customers'],
    queryFn: async () => (await api.get<Customer[]>('/customers')).data,
  })
  const products = useQuery({
    queryKey: ['products'],
    queryFn: async () => (await api.get<Product[]>('/products')).data,
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/work-orders/${id}`)
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['work-orders'] }),
    onError: (err) => setError(errorMessage(err)),
  })

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return (await api.post<ImportResult>('/work-orders/import?dry_run=false', form)).data
    },
    onSuccess: (data) => {
      setImportResult(data)
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['work-orders'] })
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const dryRun = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return (await api.post<ImportResult>('/work-orders/import?dry_run=true', form)).data
    },
    onSuccess: (data) => setImportResult(data),
    onError: (err) => setError(errorMessage(err)),
  })

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-steel-900">Work orders</h1>
          <p className="text-sm text-steel-500">
            Demand for the scheduler — create, prioritise, import and export orders.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <a className="btn btn-secondary btn-sm" href="/api/work-orders/template.csv" download>
            <Download size={15} /> Template
          </a>
          <a className="btn btn-secondary btn-sm" href="/api/work-orders/export.csv" download>
            <Download size={15} /> Export
          </a>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => fileInput.current?.click()}
            disabled={!canPlan}
          >
            <Upload size={15} /> Import CSV / JSON
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => {
              validateOnlyRef.current = true
              fileInput.current?.click()
            }}
            disabled={!canPlan}
            title="Upload CSV or JSON to validate it without creating anything"
          >
            <FileUp size={15} /> Validate file
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".csv,.json,text/csv,application/json"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) {
                if (validateOnlyRef.current) dryRun.mutate(file)
                else upload.mutate(file)
              }
              validateOnlyRef.current = false
              event.target.value = ''
            }}
          />
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => setCreating(true)}
            disabled={!canPlan}
          >
            <Plus size={15} /> New work order
          </button>
        </div>
      </header>

      {error && <ErrorBanner message={error} onRetry={() => setError(null)} />}

      {importResult && (
        <Card
          title={importResult.dry_run ? 'Import dry run' : 'Import completed'}
          subtitle={`${importResult.rows} rows read · ${importResult.valid} valid · ${importResult.created} created`}
          actions={
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setImportResult(null)}>
              Dismiss
            </button>
          }
        >
          {importResult.errors.length === 0 ? (
            <p className="text-sm text-accent-700">
              All rows passed validation
              {importResult.created > 0 ? ` and ${importResult.created} work orders were created.` : '.'}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Row</th>
                    <th>Error</th>
                    <th>Data</th>
                  </tr>
                </thead>
                <tbody>
                  {importResult.errors.map((item) => (
                    <tr key={item.row}>
                      <td className="font-mono text-xs">{item.row}</td>
                      <td className="text-xs text-danger-600">{item.message}</td>
                      <td className="font-mono text-[11px] text-steel-500">
                        {Object.entries(item.data)
                          .map(([key, value]) => `${key}=${value}`)
                          .join(' · ')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      <Card bodyClassName="p-0">
        <div className="flex flex-wrap items-center gap-3 border-b border-steel-200 p-4">
          <div className="relative flex-1 min-w-[220px]">
            <Search size={15} className="pointer-events-none absolute left-3 top-2.5 text-steel-400" />
            <input
              className="input pl-9"
              placeholder="Search by order code…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label="Search work orders"
            />
          </div>
          <select
            className="input w-44"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            aria-label="Filter by status"
          >
            <option value="">All statuses</option>
            {STATUSES.map((item) => (
              <option key={item} value={item}>
                {item.replace('_', ' ')}
              </option>
            ))}
          </select>
          <span className="text-sm text-steel-500">
            {orders.isLoading ? 'Loading…' : `${orders.data?.length ?? 0} orders`}
          </span>
        </div>

        {orders.isLoading ? (
          <Spinner />
        ) : (orders.data ?? []).length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="No work orders match"
              description="Adjust the filters, import a CSV or create a work order manually."
              icon={<FileUp size={22} />}
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Customer</th>
                  <th>Product</th>
                  <th className="text-right">Qty</th>
                  <th>Priority</th>
                  <th>Release</th>
                  <th>Due</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(orders.data ?? []).map((order) => (
                  <tr key={order.id}>
                    <td>
                      <p className="font-mono text-xs font-semibold text-steel-800">{order.code}</p>
                      {order.is_urgent && <Badge tone="red">urgent</Badge>}
                      {order.hard_deadline && <Badge tone="amber">hard deadline</Badge>}
                    </td>
                    <td className="text-xs text-steel-600">{order.customer_name ?? '—'}</td>
                    <td className="font-mono text-xs text-steel-600">{order.product_sku ?? '—'}</td>
                    <td className="text-right font-mono text-xs">{formatNumber(order.quantity)}</td>
                    <td>
                      <Badge tone={order.priority >= 4 ? 'red' : order.priority <= 2 ? 'slate' : 'amber'}>
                        P{order.priority}
                      </Badge>
                    </td>
                    <td className="text-xs text-steel-500">{formatDateTime(order.release_at)}</td>
                    <td className="text-xs text-steel-500">{formatDateTime(order.due_at)}</td>
                    <td>
                      <Badge
                        tone={
                          order.status === 'completed'
                            ? 'green'
                            : order.status === 'cancelled'
                              ? 'red'
                              : order.status === 'in_progress'
                                ? 'blue'
                                : 'slate'
                        }
                      >
                        {order.status.replace('_', ' ')}
                      </Badge>
                    </td>
                    <td className="whitespace-nowrap text-right">
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => setEditing(order)}
                        aria-label={`Edit ${order.code}`}
                      >
                        <Pencil size={14} />
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm text-danger-600"
                        onClick={() => {
                          if (window.confirm(`Delete work order ${order.code}?`)) remove.mutate(order.id)
                        }}
                        aria-label={`Delete ${order.code}`}
                        disabled={!canPlan}
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <WorkOrderDialog
        open={creating || editing !== null}
        order={editing}
        customers={customers.data ?? []}
        products={products.data ?? []}
        onClose={() => {
          setCreating(false)
          setEditing(null)
        }}
        onSaved={() => {
          setCreating(false)
          setEditing(null)
          void queryClient.invalidateQueries({ queryKey: ['work-orders'] })
        }}
      />
    </div>
  )
}

function WorkOrderDialog({
  open,
  order,
  customers,
  products,
  onClose,
  onSaved,
}: {
  open: boolean
  order: WorkOrder | null
  customers: Customer[]
  products: Product[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState({
    customer_id: '',
    product_id: '',
    quantity: '10',
    priority: '3',
    release_at: '',
    due_at: '',
    penalty_per_hour: '50',
    status: 'released',
    notes: '',
  })
  const [error, setError] = useState<string | null>(null)
  const [initialised, setInitialised] = useState<number | 'new' | null>(null)

  // Re-seed the form whenever a different order is opened.
  const key = order?.id ?? (open ? 'new' : null)
  if (open && key !== initialised) {
    setInitialised(key)
    setError(null)
    setForm({
      customer_id: order ? String(order.customer_id) : customers[0] ? String(customers[0].id) : '',
      product_id: order ? String(order.product_id) : products[0] ? String(products[0].id) : '',
      quantity: order ? String(order.quantity) : '10',
      priority: order ? String(order.priority) : '3',
      release_at: order ? toLocalInput(order.release_at) : toLocalInput(new Date().toISOString()),
      due_at: order ? toLocalInput(order.due_at) : toLocalInput(new Date(Date.now() + 86400000 * 3).toISOString()),
      penalty_per_hour: order ? String(order.penalty_per_hour) : '50',
      status: order ? order.status : 'released',
      notes: order ? (order.notes ?? '') : '',
    })
  }

  const save = useMutation({
    mutationFn: async () => {
      const payload = {
        customer_id: Number(form.customer_id),
        product_id: Number(form.product_id),
        quantity: Number(form.quantity),
        priority: Number(form.priority),
        release_at: new Date(form.release_at).toISOString(),
        due_at: new Date(form.due_at).toISOString(),
        penalty_per_hour: Number(form.penalty_per_hour),
        status: form.status,
        notes: form.notes || null,
      }
      if (order) {
        await api.patch(`/work-orders/${order.id}`, payload)
      } else {
        await api.post('/work-orders', payload)
      }
    },
    onSuccess: onSaved,
    onError: (err) => setError(errorMessage(err)),
  })

  return (
    <Modal
      open={open}
      title={order ? `Edit ${order.code}` : 'New work order'}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => save.mutate()}
            disabled={save.isPending}
          >
            {save.isPending && <Loader2 size={15} className="animate-spin" />}
            {order ? 'Save changes' : 'Create work order'}
          </button>
        </>
      }
    >
      {error && <ErrorBanner message={error} />}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Customer">
          <select
            className="input"
            value={form.customer_id}
            onChange={(event) => setForm({ ...form, customer_id: event.target.value })}
          >
            {customers.map((customer) => (
              <option key={customer.id} value={customer.id}>
                {customer.code} — {customer.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Product">
          <select
            className="input"
            value={form.product_id}
            onChange={(event) => setForm({ ...form, product_id: event.target.value })}
          >
            {products.map((product) => (
              <option key={product.id} value={product.id}>
                {product.sku} — {product.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Quantity">
          <input
            type="number"
            min={1}
            step="1"
            className="input"
            value={form.quantity}
            onChange={(event) => setForm({ ...form, quantity: event.target.value })}
          />
        </Field>
        <Field label="Priority" hint="1 = lowest, 5 = highest">
          <select
            className="input"
            value={form.priority}
            onChange={(event) => setForm({ ...form, priority: event.target.value })}
          >
            {[1, 2, 3, 4, 5].map((value) => (
              <option key={value} value={value}>
                P{value}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Release at">
          <input
            type="datetime-local"
            className="input"
            value={form.release_at}
            onChange={(event) => setForm({ ...form, release_at: event.target.value })}
          />
        </Field>
        <Field label="Due at">
          <input
            type="datetime-local"
            className="input"
            value={form.due_at}
            onChange={(event) => setForm({ ...form, due_at: event.target.value })}
          />
        </Field>
        <Field label="Penalty per hour">
          <input
            type="number"
            min={0}
            className="input"
            value={form.penalty_per_hour}
            onChange={(event) => setForm({ ...form, penalty_per_hour: event.target.value })}
          />
        </Field>
        <Field label="Status">
          <select
            className="input"
            value={form.status}
            onChange={(event) => setForm({ ...form, status: event.target.value })}
          >
            {STATUSES.map((item) => (
              <option key={item} value={item}>
                {item.replace('_', ' ')}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Notes" className="sm:col-span-2">
          <textarea
            className="input"
            rows={2}
            value={form.notes}
            onChange={(event) => setForm({ ...form, notes: event.target.value })}
          />
        </Field>
      </div>
    </Modal>
  )
}

function toLocalInput(iso: string): string {
  const date = new Date(iso)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}
