export type LegacyOfficeKind = 'sheet' | 'slides' | 'word'

export interface LegacyOfficeFormat {
  kind: LegacyOfficeKind
  targetExt: 'docx' | 'pptx' | 'xlsx'
}

const LEGACY_OFFICE: Record<string, LegacyOfficeFormat> = {
  // Word family, including macro-enabled and OpenDocument/RTF.
  doc: { kind: 'word', targetExt: 'docx' },
  docm: { kind: 'word', targetExt: 'docx' },
  dot: { kind: 'word', targetExt: 'docx' },
  dotm: { kind: 'word', targetExt: 'docx' },
  odt: { kind: 'word', targetExt: 'docx' },
  ott: { kind: 'word', targetExt: 'docx' },
  rtf: { kind: 'word', targetExt: 'docx' },
  wps: { kind: 'word', targetExt: 'docx' },

  // Presentation family.
  dps: { kind: 'slides', targetExt: 'pptx' },
  odp: { kind: 'slides', targetExt: 'pptx' },
  otp: { kind: 'slides', targetExt: 'pptx' },
  pot: { kind: 'slides', targetExt: 'pptx' },
  potm: { kind: 'slides', targetExt: 'pptx' },
  ppt: { kind: 'slides', targetExt: 'pptx' },
  pptm: { kind: 'slides', targetExt: 'pptx' },

  // Spreadsheet family. `.xls` keeps its read-only grid and uses this same
  // converter behind its "convert to .xlsx" action.
  et: { kind: 'sheet', targetExt: 'xlsx' },
  ods: { kind: 'sheet', targetExt: 'xlsx' },
  ots: { kind: 'sheet', targetExt: 'xlsx' },
  xls: { kind: 'sheet', targetExt: 'xlsx' },
  xlsm: { kind: 'sheet', targetExt: 'xlsx' },
  xlt: { kind: 'sheet', targetExt: 'xlsx' },
  xltm: { kind: 'sheet', targetExt: 'xlsx' }
}

export function legacyOfficeFormat(path: string): LegacyOfficeFormat | null {
  const clean = path.split(/[?#]/u)[0]?.toLowerCase() ?? ''
  const dot = clean.lastIndexOf('.')
  const ext = dot >= 0 ? clean.slice(dot + 1) : ''

  return LEGACY_OFFICE[ext] ?? null
}
