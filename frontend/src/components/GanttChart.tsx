import { useEffect, useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/cn'
import { formatDateTime, formatDuration } from '@/lib/format'
import type { ScheduleOperation } from '@/api/types'

export type ZoomLevel = 'hour' | 'day' | 'week'

interface GanttProps {
  operations: ScheduleOperation[]
  horizonStart: string
  horizonEnd: string
  zoom?: ZoomLevel
  onZoomChange?: (zoom: ZoomLevel) => void
  showSetup?: boolean
  filterText?: string
  machineFilter?: string[]
  lateOrderCodes?: string[]
  height?: number
}

const LABEL_WIDTH = 190
const ROW_HEIGHT = 34
const BAR_HEIGHT = 20
const MIN_SPAN_MS = 4 * 60 * 60 * 1000

function parse(value: string): number {
  return new Date(value).getTime()
}

function tickStep(zoom: ZoomLevel): number {
  if (zoom === 'hour') return 60 * 60 * 1000
  if (zoom === 'day') return 24 * 60 * 60 * 1000
  return 7 * 24 * 60 * 60 * 1000
}

function formatTick(value: number, zoom: ZoomLevel): string {
  const date = new Date(value)
  if (zoom === 'hour') {
    return date.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit' })
  }
  if (zoom === 'day') {
    return date.toLocaleDateString(undefined, { month: 'short', day: '2-digit' })
  }
  return date.toLocaleDateString(undefined, { month: 'short', day: '2-digit' })
}

function useElementWidth<T extends HTMLElement>(ref: React.RefObject<T | null>): number {
  const [width, setWidth] = useState(900)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? 0
      if (next > 0) setWidth(next)
    })
    observer.observe(element)
    setWidth(element.clientWidth || 900)
    return () => observer.disconnect()
  }, [ref])
  return width
}

export function GanttChart({
  operations,
  horizonStart,
  horizonEnd,
  zoom = 'day',
  onZoomChange,
  showSetup = true,
  filterText = '',
  machineFilter,
  lateOrderCodes,
  height = 520,
}: GanttProps) {
  const [hover, setHover] = useState<ScheduleOperation | null>(null)
  const [fitToWork, setFitToWork] = useState(true)
  const containerRef = useRef<HTMLDivElement>(null)
  const width = useElementWidth(containerRef)

  const rows = useMemo(() => {
    const map = new Map<number, { id: number; code: string; ops: ScheduleOperation[] }>()
    const needle = filterText.trim().toLowerCase()
    for (const op of operations) {
      if (machineFilter && machineFilter.length && !machineFilter.includes(op.machine_code ?? '')) {
        continue
      }
      if (
        needle &&
        !(op.work_order_code ?? '').toLowerCase().includes(needle) &&
        !(op.operation_name ?? '').toLowerCase().includes(needle) &&
        !(op.operator_code ?? '').toLowerCase().includes(needle)
      ) {
        continue
      }
      const entry = map.get(op.machine_id) ?? {
        id: op.machine_id,
        code: op.machine_code ?? `M${op.machine_id}`,
        ops: [],
      }
      entry.ops.push(op)
      map.set(op.machine_id, entry)
    }
    return [...map.values()].sort((a, b) => a.code.localeCompare(b.code))
  }, [operations, filterText, machineFilter])

  // The visible window: either the full planning horizon or the bounding box
  // of the scheduled work, whichever is more informative.
  const { start, end } = useMemo(() => {
    const horizonFrom = parse(horizonStart)
    const horizonTo = parse(horizonEnd)
    if (!fitToWork || operations.length === 0) {
      return { start: horizonFrom, end: horizonTo }
    }
    let min = Number.POSITIVE_INFINITY
    let max = Number.NEGATIVE_INFINITY
    for (const op of operations) {
      const opStart = parse(op.starts_at)
      const opEnd = parse(op.ends_at)
      if (opStart < min) min = opStart
      if (opEnd > max) max = opEnd
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return { start: horizonFrom, end: horizonTo }
    }
    const padding = Math.max((max - min) * 0.04, 30 * 60 * 1000)
    const from = Math.max(min - padding, horizonFrom)
    const to = Math.min(max + padding, horizonTo)
    if (to - from < MIN_SPAN_MS) {
      const centre = (from + to) / 2
      return { start: centre - MIN_SPAN_MS / 2, end: centre + MIN_SPAN_MS / 2 }
    }
    return { start: from, end: to }
  }, [operations, horizonStart, horizonEnd, fitToWork])

  const span = Math.max(end - start, 60 * 60 * 1000)
  const chartWidth = Math.max(width - LABEL_WIDTH - 16, 320)
  const pixelPerMs = chartWidth / span

  const ticks = useMemo(() => {
    const step = tickStep(zoom)
    const result: number[] = []
    let cursor = Math.ceil(start / step) * step
    while (cursor <= end) {
      result.push(cursor)
      cursor += step
    }
    return result
  }, [start, end, zoom])

  const chartHeight = Math.max(rows.length * ROW_HEIGHT, ROW_HEIGHT)
  const totalHeight = chartHeight + 34

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3 text-xs text-steel-600">
          <LegendSwatch color="bg-accent-500" label="On time" />
          <LegendSwatch color="bg-warn-400" label="Late order" />
          <LegendSwatch color="bg-steel-500" label="Locked" />
          <LegendSwatch color="bg-danger-400" label="Setup" />
          <label className="ml-1 inline-flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={fitToWork}
              onChange={(event) => setFitToWork(event.target.checked)}
            />
            Fit to scheduled work
          </label>
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-steel-300 bg-white p-1">
          {(['hour', 'day', 'week'] as ZoomLevel[]).map((level) => (
            <button
              key={level}
              type="button"
              onClick={() => onZoomChange?.(level)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors',
                zoom === level ? 'bg-accent-600 text-white' : 'text-steel-600 hover:bg-steel-100',
              )}
            >
              {level}
            </button>
          ))}
        </div>
      </div>

      <div className="card overflow-hidden" style={{ maxHeight: height }}>
        <div ref={containerRef} className="overflow-auto scroll-thin" style={{ maxHeight: height }}>
          <svg
            width={LABEL_WIDTH + chartWidth}
            height={totalHeight}
            role="img"
            aria-label="Production Gantt chart"
          >
            <rect x={0} y={0} width={LABEL_WIDTH + chartWidth} height={30} className="fill-steel-50" />
            {ticks.map((tick) => {
              const x = LABEL_WIDTH + (tick - start) * pixelPerMs
              return (
                <g key={tick}>
                  <line
                    x1={x}
                    y1={30}
                    x2={x}
                    y2={totalHeight}
                    className="stroke-steel-100"
                    strokeWidth={1}
                  />
                  <text x={x + 4} y={20} className="fill-steel-500 text-[10px]">
                    {formatTick(tick, zoom)}
                  </text>
                </g>
              )
            })}

            {rows.map((row, rowIndex) => {
              const y = 30 + rowIndex * ROW_HEIGHT
              return (
                <g key={row.id}>
                  <rect
                    x={0}
                    y={y}
                    width={LABEL_WIDTH + chartWidth}
                    height={ROW_HEIGHT}
                    className={cn(rowIndex % 2 === 0 ? 'fill-white' : 'fill-steel-50/60')}
                  />
                  <text x={12} y={y + ROW_HEIGHT / 2 + 4} className="fill-steel-700 text-[12px] font-medium">
                    {row.code}
                  </text>
                  <line
                    x1={0}
                    y1={y + ROW_HEIGHT}
                    x2={LABEL_WIDTH + chartWidth}
                    y2={y + ROW_HEIGHT}
                    className="stroke-steel-100"
                  />
                  {row.ops.map((op) => {
                    const x = LABEL_WIDTH + (parse(op.starts_at) - start) * pixelPerMs
                    const opWidth = Math.max((parse(op.ends_at) - parse(op.starts_at)) * pixelPerMs, 3)
                    const setupWidth = showSetup
                      ? op.setup_minutes * 60 * 1000 * pixelPerMs
                      : 0
                    const barY = y + (ROW_HEIGHT - BAR_HEIGHT) / 2
                    const isLocked = op.is_locked
                    const isLate = (lateOrderCodes ?? []).includes(op.work_order_code ?? '')
                    const fill = isLocked ? 'fill-steel-500' : isLate ? 'fill-warn-400' : 'fill-accent-500'
                    return (
                      <g key={op.id}>
                        {showSetup && op.setup_minutes > 0 && (
                          <rect
                            x={x - setupWidth}
                            y={barY + 2}
                            width={Math.max(setupWidth, 2)}
                            height={BAR_HEIGHT - 4}
                            rx={2}
                            className="fill-danger-400/70"
                          />
                        )}
                        <rect
                          x={x}
                          y={barY}
                          width={opWidth}
                          height={BAR_HEIGHT}
                          rx={4}
                          className={cn(fill, 'cursor-pointer transition-opacity hover:opacity-80')}
                          onMouseEnter={() => setHover(op)}
                          onMouseLeave={() => setHover(null)}
                        >
                          <title>
                            {`${op.work_order_code ?? ''} · ${op.operation_name ?? ''} · ${formatDateTime(
                              op.starts_at,
                            )} → ${formatDateTime(op.ends_at)}`}
                          </title>
                        </rect>
                        {opWidth > 58 && (
                          <text
                            x={x + 6}
                            y={barY + BAR_HEIGHT / 2 + 4}
                            className="pointer-events-none fill-white text-[10px] font-medium"
                          >
                            {op.work_order_code}
                          </text>
                        )}
                      </g>
                    )
                  })}
                </g>
              )
            })}
          </svg>
        </div>
      </div>

      <div className="min-h-[64px] rounded-lg border border-steel-200 bg-white px-4 py-3 text-sm">
        {hover ? (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
            <div>
              <p className="font-semibold text-steel-900">
                {hover.work_order_code} · {hover.operation_name}
              </p>
              <p className="text-xs text-steel-500">
                {formatDateTime(hover.starts_at)} → {formatDateTime(hover.ends_at)}
              </p>
            </div>
            <div className="text-xs text-steel-600">
              <p>
                <span className="text-steel-400">Machine:</span> {hover.machine_code}
              </p>
              <p>
                <span className="text-steel-400">Operator:</span> {hover.operator_code ?? 'unassigned'}
              </p>
            </div>
            <div className="text-xs text-steel-600">
              <p>
                <span className="text-steel-400">Processing:</span>{' '}
                {formatDuration(hover.processing_minutes)}
              </p>
              <p>
                <span className="text-steel-400">Setup:</span> {formatDuration(hover.setup_minutes)}
              </p>
            </div>
            {hover.is_locked && (
              <span className="badge bg-steel-100 text-steel-600 ring-1 ring-inset ring-steel-300">
                Locked (committed)
              </span>
            )}
          </div>
        ) : (
          <p className="text-xs text-steel-400">
            Hover an operation to inspect its machine, operator, processing and setup time.
          </p>
        )}
      </div>
    </div>
  )
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn('h-2.5 w-4 rounded', color)} />
      {label}
    </span>
  )
}
