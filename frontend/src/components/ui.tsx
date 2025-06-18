import { cloneElement, isValidElement, useId } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { cn, toneClasses } from '@/lib/cn'

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={cn('card', className)}>
      {(title || actions) && (
        <header className="card-header">
          <div>
            {title && <h2 className="card-title">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-sm text-steel-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn('p-5', bodyClassName)}>{children}</div>
    </section>
  )
}

export function Stat({
  label,
  value,
  hint,
  tone = 'default',
  icon,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'good' | 'bad' | 'warn'
  icon?: ReactNode
}) {
  const toneClass =
    tone === 'good'
      ? 'text-accent-700'
      : tone === 'bad'
        ? 'text-danger-600'
        : tone === 'warn'
          ? 'text-warn-600'
          : 'text-steel-900'
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="stat-label">{label}</p>
        {icon && <span className="text-steel-400">{icon}</span>}
      </div>
      <p className={cn('stat-value mt-1.5', toneClass)}>{value}</p>
      {hint && <p className="mt-1 text-xs text-steel-500">{hint}</p>}
    </div>
  )
}

export function Badge({
  children,
  tone = 'slate',
  className,
}: {
  children: ReactNode
  tone?: keyof typeof toneClasses | string
  className?: string
}) {
  return <span className={cn('badge', toneClasses[tone] ?? toneClasses.slate, className)}>{children}</span>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-10 text-sm text-steel-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-steel-300 border-t-accent-600" />
      {label ?? 'Loading…'}
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-steel-300 bg-steel-50/60 px-6 py-12 text-center">
      {icon && <div className="text-steel-400">{icon}</div>}
      <div>
        <h3 className="text-sm font-semibold text-steel-800">{title}</h3>
        {description && <p className="mx-auto mt-1 max-w-md text-sm text-steel-500">{description}</p>}
      </div>
      {action}
    </div>
  )
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start justify-between gap-4 rounded-lg border border-danger-500/30 bg-danger-500/10 px-4 py-3 text-sm text-danger-700"
    >
      <span>{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn btn-sm btn-secondary">
          Retry
        </button>
      )}
    </div>
  )
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const pct = Math.max(0, Math.min(100, value * 100))
  return (
    <div>
      {label && (
        <div className="mb-1 flex justify-between text-xs text-steel-500">
          <span>{label}</span>
          <span className="font-mono">{pct.toFixed(0)}%</span>
        </div>
      )}
      <div className="h-2 w-full overflow-hidden rounded-full bg-steel-200">
        <div className="h-full rounded-full bg-accent-500 transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  size = 'md',
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl'
}) {
  if (!open) return null
  const width = { sm: 'max-w-md', md: 'max-w-xl', lg: 'max-w-3xl', xl: 'max-w-5xl' }[size]
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-steel-950/40 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn('card my-8 w-full shadow-lift', width)}
      >
        <header className="card-header">
          <h2 className="card-title">{title}</h2>
          <button type="button" onClick={onClose} className="btn btn-ghost btn-sm" aria-label="Close">
            ✕
          </button>
        </header>
        <div className="max-h-[70vh] overflow-y-auto p-5">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-steel-200 px-5 py-3">{footer}</footer>}
      </div>
    </div>
  )
}

export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: string
  hint?: string
  error?: string
  children: ReactNode
  className?: string
}) {
  const generatedId = useId()
  const descriptionId = `${generatedId}-description`

  // When the field wraps a single native control the label is bound to it so
  // that screen readers and assistive technology announce the field name.
  const isNativeControl =
    isValidElement(children) &&
    typeof children.type === 'string' &&
    ['input', 'select', 'textarea'].includes(children.type)

  const controlId = isNativeControl
    ? ((children as ReactElement<{ id?: string }>).props.id ?? generatedId)
    : undefined

  const control = isNativeControl
    ? cloneElement(children as ReactElement<{ id?: string; 'aria-describedby'?: string }>, {
        id: controlId,
        'aria-describedby': hint || error ? descriptionId : undefined,
      })
    : children

  return (
    <div className={className}>
      <label className="label" htmlFor={controlId}>
        {label}
      </label>
      {control}
      {hint && !error && (
        <p id={descriptionId} className="mt-1 text-xs text-steel-500">
          {hint}
        </p>
      )}
      {error && (
        <p id={descriptionId} role="alert" className="mt-1 text-xs font-medium text-danger-600">
          {error}
        </p>
      )}
    </div>
  )
}
