import { initWasm } from '@betteroffice/xlsx'
import JSZip from 'jszip'

import wasmUrl from '@/assets/betteroffice/xlsx_wasm_bg.wasm?url'

const MAX_COLUMN_WIDTH = 255
let wasmReady: Promise<void> | null = null

export function ensureXlsxWasm(): Promise<void> {
  wasmReady ??= initWasm(wasmUrl)

  return wasmReady
}

/**
 * WPS and some older exporters write column widths above Excel's 255 limit;
 * BetterOffice refuses the whole workbook for that. Clamp only those values so
 * every other part of the file stays untouched.
 */
export async function normalizeXlsxColumnWidths(bytes: Uint8Array): Promise<Uint8Array> {
  const zip = await JSZip.loadAsync(bytes)
  let changed = false

  for (const name of Object.keys(zip.files)) {
    if (!/^xl\/worksheets\/[^/]+\.xml$/iu.test(name)) {
      continue
    }

    const entry = zip.file(name)

    if (!entry) {
      continue
    }

    const xml = await entry.async('string')
    const next = xml.replace(
      /(<col\b[^>]*\bwidth=")([^"]*)(")/gu,
      (match: string, prefix: string, raw: string, suffix: string) => {
        const width = Number.parseFloat(raw)

        if (!Number.isFinite(width)) {
          return match
        }

        const clamped = Math.min(MAX_COLUMN_WIDTH, Math.max(1, width))

        if (clamped === width) {
          return match
        }

        changed = true

        return `${prefix}${clamped}${suffix}`
      }
    )

    if (next !== xml) {
      zip.file(name, next)
    }
  }

  if (!changed) {
    return bytes
  }

  return zip.generateAsync({ compression: 'DEFLATE', type: 'uint8array' })
}
