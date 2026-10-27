import { useMemo } from 'react'
import { Streamdown } from 'streamdown'

interface NotebookOutput {
  data?: Record<string, unknown>
  ename?: string
  evalue?: string
  output_type?: string
  text?: unknown
  traceback?: string[]
}

interface NotebookCell {
  cell_type?: string
  execution_count?: number | null
  outputs?: NotebookOutput[]
  source?: string | string[]
}

function asText(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(item => String(item)).join('')
  }

  return typeof value === 'string' ? value : ''
}

function parseNotebook(text: string): { cells: NotebookCell[]; language: string } | null {
  try {
    const data = JSON.parse(text) as {
      cells?: NotebookCell[]
      metadata?: { language_info?: { name?: string }; kernelspec?: { language?: string } }
    }

    if (!Array.isArray(data.cells)) {
      return null
    }

    return {
      cells: data.cells,
      language: data.metadata?.language_info?.name || data.metadata?.kernelspec?.language || 'python'
    }
  } catch {
    return null
  }
}

function OutputView({ output }: { output: NotebookOutput }) {
  const data = output.data ?? {}
  const html = asText(data['text/html'])
  const png = asText(data['image/png'])
  const jpeg = asText(data['image/jpeg'])
  const plain = asText(data['text/plain'])
  const text = asText(output.text)

  if (output.output_type === 'error') {
    const trace = (output.traceback ?? []).join('\n')

    return (
      <pre className="whitespace-pre-wrap break-words text-[0.66rem] text-destructive">
        {trace || `${output.ename ?? 'Error'}: ${output.evalue ?? ''}`}
      </pre>
    )
  }

  if (png || jpeg) {
    return (
      <img
        alt="Notebook output"
        className="max-h-96 max-w-full rounded bg-white object-contain"
        src={`data:image/${png ? 'png' : 'jpeg'};base64,${png || jpeg}`}
      />
    )
  }

  if (html) {
    return (
      <iframe
        className="h-64 w-full rounded border-0 bg-white"
        sandbox=""
        srcDoc={html}
        title="Notebook HTML output"
      />
    )
  }

  const body = text || plain

  if (!body) {
    return null
  }

  return <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-[0.66rem]">{body}</pre>
}

export function NotebookPreview({ text }: { text: string }) {
  const parsed = useMemo(() => parseNotebook(text), [text])

  if (!parsed) {
    return (
      <pre className="mx-auto max-w-3xl overflow-auto px-4 py-3 text-[0.7rem] whitespace-pre-wrap">{text}</pre>
    )
  }

  return (
    <div className="preview-markdown mx-auto max-w-3xl px-4 py-3 text-sm text-foreground" data-selectable-text="true">
      {parsed.cells.map((cell, index) => {
        const source = asText(cell.source)

        if (cell.cell_type === 'markdown') {
          return (
            <div className="mb-4" key={index}>
              <Streamdown controls={false} mode="static" parseIncompleteMarkdown={false}>
                {source}
              </Streamdown>
            </div>
          )
        }

        if (cell.cell_type === 'code') {
          return (
            <div className="mb-4 overflow-hidden rounded-lg border border-border/60" key={index}>
              <div className="flex items-center gap-2 bg-muted/40 px-2 py-1 text-[0.6rem] text-muted-foreground">
                <span className="font-mono">{cell.execution_count != null ? `[${cell.execution_count}]` : '[ ]'}</span>
                <span className="truncate">{parsed.language}</span>
              </div>
              <pre className="overflow-auto bg-(--ui-bg-primary) p-3 text-[0.7rem] leading-relaxed">
                <code>{source}</code>
              </pre>
              {cell.outputs?.length ? (
                <div className="space-y-2 border-t border-border/60 bg-muted/20 p-2">
                  {cell.outputs.map((output, outputIndex) => (
                    <OutputView key={outputIndex} output={output} />
                  ))}
                </div>
              ) : null}
            </div>
          )
        }

        return (
          <pre className="mb-4 overflow-auto whitespace-pre-wrap text-[0.7rem]" key={index}>
            {source}
          </pre>
        )
      })}
    </div>
  )
}
