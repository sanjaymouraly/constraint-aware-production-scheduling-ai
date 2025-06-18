import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  Badge,
  Card,
  EmptyState,
  ErrorBanner,
  Field,
  Modal,
  ProgressBar,
  Spinner,
  Stat,
} from '@/components/ui'

describe('Card', () => {
  it('renders the title, subtitle and children', () => {
    render(
      <Card title="Machine utilisation" subtitle="Last 7 days">
        <p>content</p>
      </Card>,
    )
    expect(screen.getByText('Machine utilisation')).toBeInTheDocument()
    expect(screen.getByText('Last 7 days')).toBeInTheDocument()
    expect(screen.getByText('content')).toBeInTheDocument()
  })
})

describe('Stat', () => {
  it('renders the label, value and hint', () => {
    render(<Stat label="On-time delivery" value="95%" hint="14 of 20 orders" tone="good" />)
    expect(screen.getByText('On-time delivery')).toBeInTheDocument()
    expect(screen.getByText('95%')).toBeInTheDocument()
    expect(screen.getByText('14 of 20 orders')).toBeInTheDocument()
  })
})

describe('Badge', () => {
  it('renders its children', () => {
    render(<Badge tone="green">OPTIMAL</Badge>)
    expect(screen.getByText('OPTIMAL')).toBeInTheDocument()
  })
})

describe('Spinner', () => {
  it('shows the default and a custom label', () => {
    const { rerender } = render(<Spinner />)
    expect(screen.getByText('Loading…')).toBeInTheDocument()
    rerender(<Spinner label="Optimising…" />)
    expect(screen.getByText('Optimising…')).toBeInTheDocument()
  })
})

describe('EmptyState', () => {
  it('renders title, description and an action', async () => {
    const onClick = vi.fn()
    render(
      <EmptyState
        title="No schedules yet"
        description="Run the optimiser to create the first schedule."
        action={
          <button type="button" onClick={onClick}>
            Run optimiser
          </button>
        }
      />,
    )
    expect(screen.getByText('No schedules yet')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Run optimiser' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})

describe('ErrorBanner', () => {
  it('exposes the message through the alert role', () => {
    render(<ErrorBanner message="Solver failed" />)
    expect(screen.getByRole('alert')).toHaveTextContent('Solver failed')
  })

  it('renders a retry button when a handler is supplied', async () => {
    const onRetry = vi.fn()
    render(<ErrorBanner message="Solver failed" onRetry={onRetry} />)
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

describe('ProgressBar', () => {
  it('clamps the value into 0..100 %', () => {
    const { container, rerender } = render(<ProgressBar value={0.5} label="Progress" />)
    const bar = container.querySelector('div.h-full') as HTMLElement
    expect(bar.style.width).toBe('50%')
    rerender(<ProgressBar value={5} label="Progress" />)
    expect((container.querySelector('div.h-full') as HTMLElement).style.width).toBe('100%')
    rerender(<ProgressBar value={-2} label="Progress" />)
    expect((container.querySelector('div.h-full') as HTMLElement).style.width).toBe('0%')
  })
})

describe('Modal', () => {
  it('renders nothing while closed', () => {
    render(
      <Modal open={false} title="Publish schedule" onClose={() => {}}>
        <p>body</p>
      </Modal>,
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('renders a dialog and closes on the close button', async () => {
    const onClose = vi.fn()
    render(
      <Modal open title="Publish schedule" onClose={onClose}>
        <p>body</p>
      </Modal>,
    )
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-label', 'Publish schedule')
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('Field', () => {
  it('shows the hint when there is no error and the error otherwise', () => {
    const { rerender } = render(
      <Field label="Engine" hint="FCFS or CP-SAT">
        <input />
      </Field>,
    )
    expect(screen.getByText('FCFS or CP-SAT')).toBeInTheDocument()
    rerender(
      <Field label="Engine" hint="FCFS or CP-SAT" error="Engine is required">
        <input />
      </Field>,
    )
    expect(screen.queryByText('FCFS or CP-SAT')).toBeNull()
    expect(screen.getByText('Engine is required')).toBeInTheDocument()
  })
})
