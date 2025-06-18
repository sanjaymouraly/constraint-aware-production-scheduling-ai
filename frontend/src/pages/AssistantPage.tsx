import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Send, ShieldCheck, Sparkles } from 'lucide-react'
import { api, errorMessage } from '@/api/client'
import { Badge, Card, ErrorBanner, Field } from '@/components/ui'
import { formatNumber } from '@/lib/format'
import type { Schedule } from '@/api/types'
import { useAuth } from '@/auth/useAuth'

interface ExplainResponse {
  answer: {
    summary?: string
    explanation?: string
    bullets?: string[]
    key_findings?: string[]
    risks?: string[]
    recommendations?: string[]
    evidence_refs?: string[]
    unsupported?: boolean
    [key: string]: unknown
  }
  verification: {
    ok: boolean
    unsupported_claims: string[]
    referenced_ids: string[]
    notes: string
  }
  evidence: {
    summary: Record<string, any>
    orders: any[]
    machines: any[]
    operators: any[]
    validation: Record<string, any>
    diagnostics: any[]
    question: string
    comparison?: Record<string, any>
  }
  provider: string
  model: string
  latency_ms: number
  prompt_tokens: number
  completion_tokens: number
  estimated_cost_usd: number
}

const SUGGESTIONS = [
  'Which orders are late and why?',
  'What is the bottleneck machine and how loaded is it?',
  'Summarise the validation result of this plan.',
  'How much setup time does the plan contain?',
  'Compare this plan with the baseline.',
]

export function AssistantPage() {
  const { isAdmin } = useAuth()
  const [scheduleId, setScheduleId] = useState<number | null>(null)
  const [baselineId, setBaselineId] = useState<number | null>(null)
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState<ExplainResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: async () => (await api.get<Schedule[]>('/schedules?limit=30')).data,
  })

  const current = scheduleId ?? schedules.data?.[0]?.id ?? null

  const explain = useMutation({
    mutationFn: async () =>
      (
        await api.post<ExplainResponse>('/ai/explain', {
          schedule_id: current,
          question,
          baseline_schedule_id: baselineId || undefined,
        })
      ).data,
    onSuccess: (data) => {
      setResult(data)
      setError(null)
    },
    onError: (err) => setError(errorMessage(err)),
  })

  if (!isAdmin) {
    return (
      <Card>
        <ErrorBanner message="The AI assistant is restricted to administrators. Ask an admin for access." />
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-steel-900">AI assistant</h1>
        <p className="text-sm text-steel-500">
          The model only explains a validated solver plan from a grounded evidence pack. It can never
          create, alter or override a schedule.
        </p>
      </header>

      {error && <ErrorBanner message={error} />}

      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <div className="space-y-6">
          <Card title="Ask about a plan" subtitle="Answers are verified against the solver evidence">
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Plan to explain">
                  <select
                    className="input"
                    value={current ?? ''}
                    onChange={(event) => setScheduleId(Number(event.target.value))}
                  >
                    {(schedules.data ?? []).map((schedule) => (
                      <option key={schedule.id} value={schedule.id}>
                        {schedule.code} — {schedule.source.toUpperCase()} — {schedule.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Compare against (optional)">
                  <select
                    className="input"
                    value={baselineId ?? ''}
                    onChange={(event) =>
                      setBaselineId(event.target.value ? Number(event.target.value) : null)
                    }
                  >
                    <option value="">No baseline</option>
                    {(schedules.data ?? [])
                      .filter((schedule) => schedule.id !== current)
                      .map((schedule) => (
                        <option key={schedule.id} value={schedule.id}>
                          {schedule.code} — {schedule.source.toUpperCase()}
                        </option>
                      ))}
                  </select>
                </Field>
              </div>

              <Field label="Question">
                <textarea
                  className="input"
                  rows={3}
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="e.g. Which orders are late and what causes the delay?"
                />
              </Field>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => explain.mutate()}
                  disabled={explain.isPending || !current || question.trim().length < 2}
                >
                  {explain.isPending ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Send size={15} />
                  )}
                  {explain.isPending ? 'Asking…' : 'Ask the assistant'}
                </button>
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setQuestion(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          </Card>

          {result ? (
            <Card
              title="Grounded answer"
              subtitle={`${result.provider} · ${result.model} · ${formatNumber(result.latency_ms)} ms · ${formatNumber(result.prompt_tokens + result.completion_tokens)} tokens · $${result.estimated_cost_usd.toFixed(4)}`}
              actions={
                <Badge tone={result.verification.ok ? 'green' : 'red'}>
                  <ShieldCheck size={13} />
                  {result.verification.ok ? 'All numbers verified' : 'Unsupported claims flagged'}
                </Badge>
              }
            >
              {result.answer.summary && (
                <p className="text-sm text-steel-800">{result.answer.summary}</p>
              )}
              {result.answer.explanation && (
                <p className="mt-2 whitespace-pre-line text-sm text-steel-700">
                  {result.answer.explanation}
                </p>
              )}

              {(() => {
                const findings =
                  result.answer.key_findings ?? result.answer.bullets ?? []
                return Array.isArray(findings) && findings.length > 0 ? (
                  <BulletList title="Key findings" items={findings} tone="accent" />
                ) : null
              })()}
              {Array.isArray(result.answer.risks) && result.answer.risks.length > 0 && (
                <BulletList title="Risks" items={result.answer.risks} tone="warn" />
              )}
              {Array.isArray(result.answer.recommendations) &&
                result.answer.recommendations.length > 0 && (
                  <BulletList title="Recommendations" items={result.answer.recommendations} tone="default" />
                )}

              {!result.verification.ok && (
                <div className="mt-4 rounded-lg border border-danger-500/30 bg-danger-500/10 px-4 py-3 text-sm text-danger-700">
                  <p className="flex items-center gap-2 font-semibold">
                    <AlertTriangle size={15} /> Numeric claims not found in the evidence
                  </p>
                  <ul className="mt-1 list-disc pl-5">
                    {result.verification.unsupported_claims.map((claim, index) => (
                      <li key={index}>{claim}</li>
                    ))}
                  </ul>
                </div>
              )}

              {Array.isArray(result.answer.evidence_refs) && result.answer.evidence_refs.length > 0 && (
                <p className="mt-4 text-xs text-steel-500">
                  <span className="font-medium text-steel-600">Evidence used:</span>{' '}
                  {result.answer.evidence_refs.join(', ')}
                </p>
              )}

              <details className="mt-4">
                <summary className="cursor-pointer text-sm font-medium text-steel-600">
                  Show the evidence pack the model was given
                </summary>
                <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-steel-900 p-3 font-mono text-[11px] leading-relaxed text-steel-100">
                  {JSON.stringify(result.evidence, null, 2)}
                </pre>
              </details>
            </Card>
          ) : null}
        </div>

        <Card title="Grounding contract" subtitle="How the assistant is kept honest">
          <ol className="space-y-3 text-sm text-steel-700">
            <li className="flex gap-2">
              <span className="font-mono text-xs text-accent-700">1.</span>
              The solver produces a plan; the independent validator checks every hard constraint.
            </li>
            <li className="flex gap-2">
              <span className="font-mono text-xs text-accent-700">2.</span>
              Only a validated plan can be explained — the API rebuilds the evidence pack from the
              stored assignments.
            </li>
            <li className="flex gap-2">
              <span className="font-mono text-xs text-accent-700">3.</span>
              The model receives the evidence pack as JSON and may only restate it.
            </li>
            <li className="flex gap-2">
              <span className="font-mono text-xs text-accent-700">4.</span>
              Every number in the answer is cross-checked against the evidence; unsupported claims are
              flagged and shown to the user.
            </li>
          </ol>

          <div className="mt-5 rounded-lg border border-steel-200 bg-steel-50 p-3 text-xs text-steel-600">
            <p className="font-semibold text-steel-700">Provider configuration</p>
            <p className="mt-1">
              Providers, keys, timeouts and spend limits are managed by an administrator under AI
              settings. API keys never leave the backend.
            </p>
          </div>
        </Card>
      </div>
    </div>
  )
}

function BulletList({
  title,
  items,
  tone,
}: {
  title: string
  items: string[]
  tone: 'accent' | 'warn' | 'default'
}) {
  const color =
    tone === 'accent' ? 'text-accent-700' : tone === 'warn' ? 'text-warn-600' : 'text-steel-700'
  return (
    <div className="mt-4">
      <p className="flex items-center gap-2 text-sm font-semibold text-steel-800">
        <Sparkles size={14} className={color} />
        {title}
      </p>
      <ul className={`mt-1.5 list-disc space-y-1 pl-5 text-sm ${color}`}>
        {items.map((item, index) => (
          <li key={index}>{item}</li>
        ))}
      </ul>
    </div>
  )
}
