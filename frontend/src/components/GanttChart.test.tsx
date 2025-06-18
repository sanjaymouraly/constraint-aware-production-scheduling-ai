import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { GanttChart } from '@/components/GanttChart'
import type { ScheduleOperation } from '@/api/types'

const HORIZON_START = '2026-03-02T00:00:00Z'
const HORIZON_END = '2026-03-09T00:00:00Z'

function operation(overrides: Partial<ScheduleOperation> = {}): ScheduleOperation {
  return {
    id: 1,
    schedule_id: 1,
    work_order_id: 1,
    work_order_code: 'WO-1001',
    routing_operation_id: 1,
    operation_name: 'Casting',
    operation_sequence: 10,
    machine_id: 1,
    machine_code: 'M-CAST-01',
    operator_id: 7,
    operator_code: 'OP-07',
    starts_at: '2026-03-02T06:00:00Z',
    ends_at: '2026-03-02T10:00:00Z',
    setup_starts_at: '2026-03-02T05:30:00Z',
    setup_minutes: 30,
    processing_minutes: 240,
    is_locked: false,
    status: 'scheduled',
    ...overrides,
  }
}

describe('GanttChart', () => {
  it('renders an accessible chart with the machine row label', () => {
    render(
      <GanttChart
        operations={[operation()]}
        horizonStart={HORIZON_START}
        horizonEnd={HORIZON_END}
      />,
    )
    const svg = screen.getByRole('img', { name: 'Production Gantt chart' })
    expect(svg).toBeInTheDocument()
    expect(screen.getByText('M-CAST-01')).toBeInTheDocument()
  })

  it('shows a tooltip title for every operation bar', () => {
    render(
      <GanttChart
        operations={[operation()]}
        horizonStart={HORIZON_START}
        horizonEnd={HORIZON_END}
      />,
    )
    const titles = Array.from(document.querySelectorAll('title')).map((node) => node.textContent)
    expect(titles.some((t) => t?.includes('WO-1001') && t.includes('Casting'))).toBe(true)
  })

  it('filters operations by free text', () => {
    const ops = [
      operation({ id: 1, work_order_code: 'WO-1001', operation_name: 'Casting' }),
      operation({ id: 2, work_order_code: 'WO-2002', operation_name: 'Painting' }),
    ]
    const { rerender } = render(
      <GanttChart operations={ops} horizonStart={HORIZON_START} horizonEnd={HORIZON_END} />,
    )
    expect(screen.getByText('M-CAST-01')).toBeInTheDocument()
    rerender(
      <GanttChart
        operations={ops}
        horizonStart={HORIZON_START}
        horizonEnd={HORIZON_END}
        filterText="WO-2002"
      />,
    )
    const titles = Array.from(document.querySelectorAll('title')).map((node) => node.textContent)
    expect(titles.some((t) => t?.includes('WO-2002'))).toBe(true)
    expect(titles.some((t) => t?.includes('WO-1001'))).toBe(false)
  })

  it('filters operations by machine', () => {
    const ops = [
      operation({ id: 1, machine_id: 1, machine_code: 'M-CAST-01' }),
      operation({ id: 2, machine_id: 2, machine_code: 'M-PAINT-02' }),
    ]
    render(
      <GanttChart
        operations={ops}
        horizonStart={HORIZON_START}
        horizonEnd={HORIZON_END}
        machineFilter={['M-PAINT-02']}
      />,
    )
    expect(screen.getByText('M-PAINT-02')).toBeInTheDocument()
    expect(screen.queryByText('M-CAST-01')).toBeNull()
  })

  it('reports the zoom level through the callback', async () => {
    const onZoomChange = vi.fn()
    render(
      <GanttChart
        operations={[operation()]}
        horizonStart={HORIZON_START}
        horizonEnd={HORIZON_END}
        onZoomChange={onZoomChange}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'week' }))
    expect(onZoomChange).toHaveBeenCalledWith('week')
  })

  it('toggles the "fit to scheduled work" option', () => {
    render(
      <GanttChart
        operations={[operation()]}
        horizonStart={HORIZON_START}
        horizonEnd={HORIZON_END}
      />,
    )
    const checkbox = screen.getByLabelText('Fit to scheduled work') as HTMLInputElement
    expect(checkbox.checked).toBe(true)
    fireEvent.click(checkbox)
    expect(checkbox.checked).toBe(false)
  })

  it('shows the hovered operation details in the detail panel', () => {
    render(
      <GanttChart
        operations={[operation()]}
        horizonStart={HORIZON_START}
        horizonEnd={HORIZON_END}
      />,
    )
    expect(
      screen.getByText(/Hover an operation to inspect its machine, operator, processing and setup time/),
    ).toBeInTheDocument()

    const bars = Array.from(document.querySelectorAll('rect.cursor-pointer'))
    expect(bars.length).toBe(1)
    fireEvent.mouseEnter(bars[0])
    expect(screen.getByText('WO-1001 · Casting')).toBeInTheDocument()
    // The machine appears twice: once as the row label and once in the
    // hover detail panel.
    expect(screen.getAllByText('M-CAST-01').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('OP-07')).toBeInTheDocument()
    expect(screen.getByText('4.0 h')).toBeInTheDocument()
    expect(screen.getByText('30 min')).toBeInTheDocument()
  })

  it('renders without crashing for an empty operation list', () => {
    render(
      <GanttChart operations={[]} horizonStart={HORIZON_START} horizonEnd={HORIZON_END} />,
    )
    expect(screen.getByRole('img', { name: 'Production Gantt chart' })).toBeInTheDocument()
  })
})
