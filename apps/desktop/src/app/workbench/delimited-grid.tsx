import 'react-data-grid/lib/styles.css'

import { useEffect, useMemo, useRef, useState } from 'react'
import { type Column, DataGrid, type RenderEditCellContentProps } from 'react-data-grid'

import { useI18n } from '@/i18n'
import { Plus } from '@/lib/icons'
import { cn } from '@/lib/utils'

import {
  delimitedColumnLabel,
  type DelimitedKind,
  parseDelimited,
  serializeDelimited
} from './delimited'

type GridRow = { id: number } & Record<string, number | string>

// react-data-grid 7 beta calls this stage-3 method; Electron 40's V8 does not
// ship it yet, so the grid throws while measuring a cell.
type InsertComputed = (key: unknown, callback: (key: unknown) => unknown) => unknown

function installInsertComputed(prototype: object) {
  const target = prototype as unknown as { getOrInsertComputed?: InsertComputed }

  if (typeof target.getOrInsertComputed === 'function') {
    return
  }

  target.getOrInsertComputed = function (this: unknown, key: unknown, callback: (key: unknown) => unknown) {
    const store = this as {
      get(key: unknown): unknown
      has(key: unknown): boolean
      set(key: unknown, value: unknown): unknown
    }

    if (!store.has(key)) {
      store.set(key, callback(key))
    }

    return store.get(key)
  }
}

installInsertComputed(Map.prototype)
installInsertComputed(WeakMap.prototype)

function TextCellEditor({ column, onClose, onRowChange, row }: RenderEditCellContentProps<GridRow>) {
  return (
    <input
      autoFocus
      className="h-full w-full border-0 bg-transparent px-2 text-[0.72rem] outline-none"
      onBlur={() => onClose(true)}
      onChange={event => onRowChange({ ...row, [column.key]: event.target.value })}
      value={String(row[column.key] ?? '')}
    />
  )
}

function gridRows(rows: string[][], columnCount: number): GridRow[] {
  return rows.map((cells, index) => {
    const row: GridRow = { id: index }

    for (let column = 0; column < columnCount; column += 1) {
      row[`c${column}`] = cells[column] ?? ''
    }

    return row
  })
}

export function DelimitedGrid({
  kind,
  onChange,
  readOnly = false,
  text
}: {
  kind: DelimitedKind
  onChange?: (text: string) => void
  readOnly?: boolean
  text: string
}) {
  const { t } = useI18n()
  const [initial] = useState(() => parseDelimited(text, kind))
  const [rows, setRows] = useState<string[][]>(initial.rows)
  const [rowLengths, setRowLengths] = useState<number[]>(initial.rowLengths)
  const [columnCount, setColumnCount] = useState(initial.columnCount)
  const dialect = useRef(initial.dialect)
  const emitted = useRef(text)

  useEffect(() => {
    if (text === emitted.current) {
      return
    }

    const parsed = parseDelimited(text, kind)

    emitted.current = text
    dialect.current = parsed.dialect
    setRows(parsed.rows)
    setRowLengths(parsed.rowLengths)
    setColumnCount(parsed.columnCount)
  }, [kind, text])

  const columns = useMemo<Column<GridRow>[]>(
    () =>
      Array.from({ length: columnCount }, (_, index) => ({
        key: `c${index}`,
        name: delimitedColumnLabel(index),
        editable: !readOnly,
        minWidth: 48,
        width: 120,
        resizable: true,
        renderEditCell: readOnly ? undefined : TextCellEditor
      })),
    [columnCount, readOnly]
  )

  const displayedRows = useMemo(() => gridRows(rows, columnCount), [columnCount, rows])

  const commit = (next: GridRow[]) => {
    const nextRows = next.map(row =>
      Array.from({ length: columnCount }, (_, column) => String(row[`c${column}`] ?? ''))
    )
    const nextLengths = nextRows.map((cells, index) => {
      let lastFilled = -1

      cells.forEach((value, column) => {
        if (value !== '') {
          lastFilled = column
        }
      })

      return Math.max(rowLengths[index] ?? 0, lastFilled + 1)
    })
    const serialized = serializeDelimited(nextRows, nextLengths, dialect.current)

    emitted.current = serialized
    setRows(nextRows)
    setRowLengths(nextLengths)
    onChange?.(serialized)
  }

  const addRow = () => {
    const nextRows = [...rows, Array.from({ length: columnCount }, () => '')]
    const nextLengths = [...rowLengths, 0]

    setRows(nextRows)
    setRowLengths(nextLengths)
    const serialized = serializeDelimited(nextRows, nextLengths, dialect.current)

    emitted.current = serialized
    onChange?.(serialized)
  }

  const addColumn = () => {
    setColumnCount(count => count + 1)
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!readOnly && (
        <div className="flex h-7 shrink-0 items-center justify-end gap-1 border-b border-border/40 px-2">
          <button
            aria-label={t.preview.addRow}
            className="flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[0.625rem] font-bold text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            onClick={addRow}
            title={t.preview.addRow}
            type="button"
          >
            <Plus className="size-3" />
            {t.preview.row}
          </button>
          <button
            aria-label={t.preview.addColumn}
            className="flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[0.625rem] font-bold text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            onClick={addColumn}
            title={t.preview.addColumn}
            type="button"
          >
            <Plus className="size-3" />
            {t.preview.column}
          </button>
        </div>
      )}
      <div className={cn('min-h-0 flex-1 overflow-hidden text-[0.72rem]')}>
        <DataGrid
          columns={columns}
          onRowsChange={commit}
          rowHeight={28}
          rows={displayedRows}
          style={{ blockSize: '100%' }}
        />
      </div>
    </div>
  )
}
