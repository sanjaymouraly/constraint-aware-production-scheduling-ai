import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  Loader2,
  PlugZap,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { api, errorMessage } from '@/api/client'
import { Badge, Card, ErrorBanner, Field, Modal, Spinner } from '@/components/ui'
import { formatDateTime, formatNumber } from '@/lib/format'
import type { AIProvider, AISettings, ProviderCatalogEntry } from '@/api/types'
import { useAuth } from '@/auth/useAuth'

const KINDS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'custom', label: 'Custom (OpenAI-compatible)' },
  { value: 'ollama', label: 'Ollama (local)' },
  { value: 'mock', label: 'Offline mock' },
]

export function AiSettingsPage() {
  const queryClient = useQueryClient()
  const { isAdmin } = useAuth()
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [settings, setSettings] = useState<AISettings | null>(null)

  const providers = useQuery({
    queryKey: ['ai-providers'],
    queryFn: async () => (await api.get<AIProvider[]>('/ai/providers')).data,
  })
  const catalog = useQuery({
    queryKey: ['ai-catalog'],
    queryFn: async () => (await api.get<ProviderCatalogEntry[]>('/ai/catalog')).data,
  })
  const aiSettings = useQuery({
    queryKey: ['ai-settings'],
    queryFn: async () => (await api.get<AISettings>('/ai/settings')).data,
  })

  useEffect(() => {
    if (aiSettings.data) setSettings(aiSettings.data)
  }, [aiSettings.data])

  const saveSettings = useMutation({
    mutationFn: async (payload: AISettings) => (await api.put<AISettings>('/ai/settings', payload)).data,
    onSuccess: (data) => {
      setSettings(data)
      setMessage('AI policy updated.')
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['ai-settings'] })
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const testProvider = useMutation({
    mutationFn: async (id: number) => (await api.post(`/ai/providers/${id}/test`)).data,
    onSuccess: (data: any) => {
      setMessage(
        data.ok
          ? `Connection OK in ${data.latency_ms} ms${data.models?.length ? ` — ${data.models.length} models discovered` : ''}.`
          : `Connection failed: ${data.message}`,
      )
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['ai-providers'] })
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const discover = useMutation({
    mutationFn: async (id: number) => (await api.get(`/ai/providers/${id}/models`)).data,
    onSuccess: (data: any) => {
      setMessage(`Models discovered: ${(data.models ?? []).join(', ') || 'none returned'}.`)
      setError(null)
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/ai/providers/${id}`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ai-providers'] })
    },
    onError: (err) => setError(errorMessage(err)),
  })

  if (!isAdmin) {
    return (
      <Card>
        <ErrorBanner message="AI settings are restricted to administrators." />
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-steel-900">AI settings</h1>
          <p className="text-sm text-steel-500">
            Provider-independent LLM configuration. Secrets stay on the server.
          </p>
        </div>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setCreating(true)}>
          <Plus size={15} /> Add provider
        </button>
      </header>

      {message && (
        <div className="rounded-lg border border-accent-500/30 bg-accent-50 px-4 py-2.5 text-sm text-accent-800">
          {message}
        </div>
      )}
      {error && <ErrorBanner message={error} onRetry={() => setError(null)} />}

      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <Card title="Configured providers" bodyClassName="p-0">
          {providers.isLoading ? (
            <Spinner />
          ) : (providers.data ?? []).length === 0 ? (
            <div className="p-5">
              <p className="text-sm text-steel-500">
                No provider configured. The scheduler works fully offline — AI is only needed for
                explanations.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Kind</th>
                    <th>Model</th>
                    <th>Endpoint</th>
                    <th>Key</th>
                    <th>Limits</th>
                    <th>Last test</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(providers.data ?? []).map((provider) => (
                    <tr key={provider.id}>
                      <td>
                        <p className="text-sm font-medium text-steel-800">{provider.name}</p>
                        {provider.is_default && <Badge tone="blue">default</Badge>}
                        {!provider.is_enabled && <Badge tone="slate">disabled</Badge>}
                      </td>
                      <td className="text-xs text-steel-600">{provider.kind}</td>
                      <td className="font-mono text-xs">{provider.model}</td>
                      <td className="max-w-[200px] truncate font-mono text-[11px] text-steel-500">
                        {provider.base_url ?? '—'}
                      </td>
                      <td className="font-mono text-[11px] text-steel-500">
                        {provider.has_api_key ? provider.masked_api_key : 'not set'}
                      </td>
                      <td className="text-[11px] text-steel-500">
                        {formatNumber(provider.request_timeout_seconds, 0)}s ·{' '}
                        {formatNumber(provider.max_tokens)} tok · {provider.max_retries} retries
                      </td>
                      <td className="text-[11px]">
                        {provider.last_test_status ? (
                          <span
                            className={
                              provider.last_test_status === 'ok'
                                ? 'text-accent-700'
                                : 'text-danger-600'
                            }
                          >
                            {provider.last_test_status}
                          </span>
                        ) : (
                          <span className="text-steel-400">not tested</span>
                        )}
                        <p className="text-steel-400">
                          {provider.last_test_at ? formatDateTime(provider.last_test_at) : ''}
                        </p>
                      </td>
                      <td className="whitespace-nowrap text-right">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => testProvider.mutate(provider.id)}
                          disabled={testProvider.isPending}
                          title="Test the connection"
                        >
                          <PlugZap size={14} />
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => discover.mutate(provider.id)}
                          disabled={discover.isPending}
                          title="Discover models"
                        >
                          <RefreshCw size={14} />
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm text-danger-600"
                          onClick={() => {
                            if (window.confirm(`Delete provider ${provider.name}?`)) {
                              remove.mutate(provider.id)
                            }
                          }}
                          title="Delete"
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

        <div className="space-y-6">
          <Card title="Platform policy" subtitle="Applies to every provider">
            {settings ? (
              <div className="space-y-4">
                <label className="flex items-center justify-between gap-3 text-sm">
                  <span className="text-steel-700">AI features enabled</span>
                  <input
                    type="checkbox"
                    checked={settings.enabled}
                    onChange={(event) =>
                      setSettings({ ...settings, enabled: event.target.checked })
                    }
                  />
                </label>
                <label className="flex items-center justify-between gap-3 text-sm">
                  <span className="text-steel-700">Allow paid providers (OpenAI, Anthropic, OpenRouter)</span>
                  <input
                    type="checkbox"
                    checked={settings.allow_paid_providers}
                    onChange={(event) =>
                      setSettings({ ...settings, allow_paid_providers: event.target.checked })
                    }
                  />
                </label>
                <label className="flex items-center justify-between gap-3 text-sm">
                  <span className="text-steel-700">Allow local providers (Ollama, custom)</span>
                  <input
                    type="checkbox"
                    checked={settings.allow_local_providers}
                    onChange={(event) =>
                      setSettings({ ...settings, allow_local_providers: event.target.checked })
                    }
                  />
                </label>

                <div className="grid grid-cols-2 gap-3">
                  <Field label="Monthly budget (USD)">
                    <input
                      type="number"
                      min={0}
                      step="0.5"
                      className="input"
                      value={settings.monthly_budget_usd}
                      onChange={(event) =>
                        setSettings({ ...settings, monthly_budget_usd: Number(event.target.value) })
                      }
                    />
                  </Field>
                  <Field label="Spend limit (USD)">
                    <input
                      type="number"
                      min={0}
                      step="0.5"
                      className="input"
                      value={settings.spend_limit_usd}
                      onChange={(event) =>
                        setSettings({ ...settings, spend_limit_usd: Number(event.target.value) })
                      }
                    />
                  </Field>
                </div>

                <Field label="Max cost per request (USD)">
                  <input
                    type="number"
                    min={0}
                    step="0.05"
                    className="input"
                    value={settings.max_cost_per_request_usd}
                    onChange={(event) =>
                      setSettings({
                        ...settings,
                        max_cost_per_request_usd: Number(event.target.value),
                      })
                    }
                  />
                </Field>

                <div className="rounded-lg border border-steel-200 bg-steel-50 p-3 text-xs text-steel-600">
                  <p>
                    Spent so far: <span className="font-mono">${settings.spent_usd.toFixed(4)}</span>
                  </p>
                </div>

                <button
                  type="button"
                  className="btn btn-primary w-full"
                  onClick={() => saveSettings.mutate(settings)}
                  disabled={saveSettings.isPending}
                >
                  {saveSettings.isPending && <Loader2 size={15} className="animate-spin" />}
                  Save policy
                </button>
              </div>
            ) : (
              <Spinner />
            )}
          </Card>

          <Card title="Supported providers" subtitle="Any OpenAI-compatible endpoint can be added">
            {(catalog.data ?? []).map((entry) => (
              <div key={entry.kind} className="border-b border-steel-100 py-2.5 last:border-0">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium text-steel-800">{entry.label}</p>
                  {entry.hosted ? <Badge tone="blue">hosted</Badge> : <Badge tone="green">local</Badge>}
                </div>
                <p className="mt-0.5 font-mono text-[11px] text-steel-500">{entry.default_base_url}</p>
                <p className="text-[11px] text-steel-500">
                  default model: {entry.default_model} · key required: {entry.requires_key ? 'yes' : 'no'}
                </p>
              </div>
            ))}
          </Card>
        </div>
      </div>

      <Card title="Subscription vs API access" subtitle="An important distinction">
        <div className="flex items-start gap-3 text-sm text-steel-700">
          <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-accent-600" />
          <div>
            <p>
              A ChatGPT Plus/Pro subscription does <strong>not</strong> include OpenAI API access.
              API usage is billed separately by token volume through a platform API key.
            </p>
            <p className="mt-1">
              This platform only talks to a provider through its own API key, and it never asks for
              ChatGPT account credentials. Local providers (Ollama) and the offline mock provider
              need no key at all.
            </p>
          </div>
        </div>
      </Card>

      <ProviderDialog
        open={creating}
        catalog={catalog.data ?? []}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false)
          void queryClient.invalidateQueries({ queryKey: ['ai-providers'] })
        }}
      />
    </div>
  )
}

function ProviderDialog({
  open,
  catalog,
  onClose,
  onCreated,
}: {
  open: boolean
  catalog: ProviderCatalogEntry[]
  onClose: () => void
  onCreated: () => void
}) {
  const [form, setForm] = useState({
    name: '',
    kind: 'openai',
    base_url: '',
    model: '',
    api_key: '',
    request_timeout_seconds: '45',
    max_tokens: '2000',
    temperature: '0.2',
    max_retries: '2',
    is_default: false,
    notes: '',
  })
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post<AIProvider>('/ai/providers', {
          name: form.name,
          kind: form.kind,
          base_url: form.base_url || null,
          model: form.model,
          api_key: form.api_key || null,
          request_timeout_seconds: Number(form.request_timeout_seconds),
          max_tokens: Number(form.max_tokens),
          temperature: Number(form.temperature),
          max_retries: Number(form.max_retries),
          is_default: form.is_default,
          notes: form.notes || null,
        })
      ).data,
    onSuccess: () => {
      setError(null)
      onCreated()
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const entry = catalog.find((item) => item.kind === form.kind)

  return (
    <Modal
      open={open}
      title="Add an AI provider"
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
            disabled={create.isPending || !form.name || !form.model}
          >
            {create.isPending && <Loader2 size={15} className="animate-spin" />}
            Save provider
          </button>
        </>
      }
    >
      {error && <ErrorBanner message={error} />}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Display name">
          <input
            className="input"
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="e.g. Production OpenAI"
          />
        </Field>
        <Field label="Provider kind">
          <select
            className="input"
            value={form.kind}
            onChange={(event) => {
              const kind = event.target.value
              const match = catalog.find((item) => item.kind === kind)
              setForm({
                ...form,
                kind,
                base_url: match?.default_base_url ?? '',
                model: match?.default_model ?? '',
              })
            }}
          >
            {KINDS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Model">
          <input
            className="input"
            value={form.model}
            onChange={(event) => setForm({ ...form, model: event.target.value })}
            placeholder={entry?.default_model ?? 'model name'}
          />
        </Field>
        <Field label="Base URL" hint="Leave empty to use the provider default">
          <input
            className="input"
            value={form.base_url}
            onChange={(event) => setForm({ ...form, base_url: event.target.value })}
            placeholder={entry?.default_base_url ?? ''}
          />
        </Field>
        <Field
          label="API key"
          className="sm:col-span-2"
          hint={entry?.requires_key ? 'Stored encrypted on the server, never returned to the browser.' : 'This provider does not need a key.'}
        >
          <input
            type="password"
            className="input"
            value={form.api_key}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })}
            autoComplete="off"
          />
        </Field>
        <Field label="Timeout (s)">
          <input
            type="number"
            min={1}
            max={300}
            className="input"
            value={form.request_timeout_seconds}
            onChange={(event) =>
              setForm({ ...form, request_timeout_seconds: event.target.value })
            }
          />
        </Field>
        <Field label="Max tokens">
          <input
            type="number"
            min={64}
            max={32000}
            className="input"
            value={form.max_tokens}
            onChange={(event) => setForm({ ...form, max_tokens: event.target.value })}
          />
        </Field>
        <Field label="Temperature">
          <input
            type="number"
            min={0}
            max={2}
            step="0.1"
            className="input"
            value={form.temperature}
            onChange={(event) => setForm({ ...form, temperature: event.target.value })}
          />
        </Field>
        <Field label="Max retries">
          <input
            type="number"
            min={0}
            max={5}
            className="input"
            value={form.max_retries}
            onChange={(event) => setForm({ ...form, max_retries: event.target.value })}
          />
        </Field>
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input
            type="checkbox"
            checked={form.is_default}
            onChange={(event) => setForm({ ...form, is_default: event.target.checked })}
          />
          Use as the default provider
        </label>
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
