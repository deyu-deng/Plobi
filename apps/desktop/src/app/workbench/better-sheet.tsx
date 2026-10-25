import type { XlsxEditorApi } from '@betteroffice/xlsx-react'
import { XlsxEditor } from '@betteroffice/xlsx-react'
import zhCN from '@betteroffice/xlsx-i18n/zh-CN'
import { useCallback, useEffect, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Codicon } from '@/components/ui/codicon'

import { ensureXlsxWasm, normalizeXlsxColumnWidths } from './better-xlsx'
import { readFileBytes, useFileBytes } from './file-bytes'
import type { OfficePreviewLabels } from './office-preview'
import { registerXlsxSession } from './office-sessions'
import { sameBytes } from './styled-sheet-model'

type Notice = 'conflict' | 'failed' | 'saved'

export function BetterSheetEditor({ labels, path }: { labels: OfficePreviewLabels; path: string }) {
  const [reloadKey, setReloadKey] = useState(0)
  const { bytes, error, loading } = useFileBytes(path, reloadKey)
  const [readyBytes, setReadyBytes] = useState<null | Uint8Array>(null)
  const [opening, setOpening] = useState(true)
  const [openError, setOpenError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<null | Notice>(null)
  const [proposalCount, setProposalCount] = useState(0)
  const apiRef = useRef<null | XlsxEditorApi>(null)
  const baselineRef = useRef<null | Uint8Array>(null)
  const fileName = path.split(/[\\/]/u).filter(Boolean).pop() ?? 'workbook.xlsx'

  useEffect(() => {
    if (!bytes) {
      setReadyBytes(null)

      return
    }

    let active = true
    setReadyBytes(null)
    setOpenError(false)
    setOpening(true)
    setNotice(null)

    void ensureXlsxWasm()
      .then(() => normalizeXlsxColumnWidths(bytes))
      .then(normalized => {
        if (active) {
          baselineRef.current = bytes
          setReadyBytes(normalized)
        }
      })
      .catch(() => {
        if (active) {
          setOpenError(true)
        }
      })
      .finally(() => {
        if (active) {
          setOpening(false)
        }
      })

    return () => {
      active = false
    }
  }, [bytes])

  useEffect(() => {
    if (!readyBytes) {
      return
    }

    const timer = window.setInterval(() => {
      const api = apiRef.current

      if (api) {
        setProposalCount(api.handle.listProposals().length)
      }
    }, 1500)

    return () => window.clearInterval(timer)
  }, [readyBytes])

  const handleReady = useCallback(
    (api: XlsxEditorApi) => {
      apiRef.current = api
      setProposalCount(api.handle.listProposals().length)
      const unregister = registerXlsxSession(path, api)

      return () => {
        unregister()
        apiRef.current = null
      }
    },
    [path]
  )

  const handleSave = useCallback(
    async (saved: Uint8Array) => {
      if (saving) {
        return
      }

      setSaving(true)
      setNotice(null)

      try {
        const current = await readFileBytes(path)
        const baseline = baselineRef.current

        if (baseline && !sameBytes(current, baseline)) {
          setNotice('conflict')

          return
        }

        const write = window.plobiDesktop?.writeFileBuffer

        if (!write) {
          throw new Error('Save bridge unavailable')
        }

        await write(path, saved)
        baselineRef.current = saved
        setNotice('saved')
      } catch {
        setNotice('failed')
      } finally {
        setSaving(false)
      }
    },
    [path, saving]
  )

  const reload = () => {
    setNotice(null)
    setReloadKey(value => value + 1)
  }

  const review = () => {
    const api = apiRef.current

    if (api) {
      void api.commands.execute('proposalsPanel', null)
    }
  }

  const noticeLabel =
    notice === 'conflict'
      ? labels.conflict
      : notice === 'failed'
        ? labels.saveFailed
        : notice === 'saved'
          ? labels.saved
          : ''

  return (
    <div className="relative flex h-full min-h-0 flex-1 flex-col bg-white text-[#111]">
      {readyBytes ? (
        <div className="min-h-0 flex-1">
          <XlsxEditor
            className="h-full w-full"
            file={readyBytes}
            fileName={fileName}
            i18n={zhCN}
            onReady={handleReady}
            onSave={saved => void handleSave(saved)}
          />
        </div>
      ) : null}

      {opening && !openError ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-white">
          <PageLoader label={labels.loading} />
        </div>
      ) : null}

      {openError || error ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-white px-6 text-center">
          <Codicon name="warning" size="1.25rem" />
          <p className="text-[0.8rem] font-medium text-foreground">{labels.unavailable}</p>
          <button
            className="rounded border border-[#d1d5db] px-2 py-1 text-[0.68rem] text-[#333] hover:bg-[#f3f4f6]"
            onClick={reload}
            type="button"
          >
            {labels.reload}
          </button>
        </div>
      ) : null}

      {noticeLabel ? (
        <div className="absolute bottom-3 left-3 z-10 flex items-center gap-2 rounded-md bg-white/95 px-2 py-1 text-[0.68rem] shadow ring-1 ring-[#e5e7eb]">
          <span className={notice === 'saved' ? 'text-[#15803d]' : 'text-[#b91c1c]'}>{noticeLabel}</span>
          {notice === 'conflict' ? (
            <button className="text-[#2563eb] underline" onClick={reload} type="button">
              {labels.reload}
            </button>
          ) : null}
        </div>
      ) : null}

      {proposalCount > 0 ? (
        <button
          className="absolute right-3 bottom-3 z-10 flex items-center gap-1.5 rounded-full bg-[#1d4ed8] px-3 py-1.5 text-[0.7rem] font-medium text-white shadow-lg hover:bg-[#1e40af]"
          onClick={review}
          type="button"
        >
          <Codicon name="git-pull-request" size="0.8rem" />
          {labels.review} {proposalCount}
        </button>
      ) : null}
    </div>
  )
}
