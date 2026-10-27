import { legacyOfficeFormat } from '@/lib/legacy-office'

export type FileKind = 'docx' | 'legacy' | 'other' | 'pdf' | 'pptx' | 'sheet'

export function fileKindForPath(path: string): FileKind {
  if (legacyOfficeFormat(path) && !/\.xls$/iu.test(path.split(/[?#]/u)[0] ?? path)) {
    return 'legacy'
  }

  const clean = path.split(/[?#]/u)[0]?.toLowerCase() ?? ''
  const dot = clean.lastIndexOf('.')
  const ext = dot >= 0 ? clean.slice(dot + 1) : ''

  if (ext === 'xlsx' || ext === 'xls') {
    return 'sheet'
  }

  if (ext === 'pdf') {
    return 'pdf'
  }

  if (ext === 'docx') {
    return 'docx'
  }

  if (ext === 'pptx') {
    return 'pptx'
  }

  return 'other'
}
