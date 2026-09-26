/**
 * Timetable (.ics) import — the data side of the board's「导入课表」(WP-ICS-BOARD).
 *
 * Backed by the collector's `/api/collect/timetable*` routes (WP-COLLECTOR-ICS).
 * The **backend owns the whole truth**: it parses the ICS, counts the entries
 * and decides what is new. This module never reads the file, never counts and
 * never invents a clock — it only carries the payload through
 * `window.plobiDesktop.api` (the desktop's single data channel).
 *
 * Preview → confirm → import is deliberately two calls: the board shows the
 * calendar's own name and first/last date before anything reaches the agenda,
 * and a cancelled preview writes nothing.
 */

export interface TimetableSampleEvent {
  end_at: null | string
  location?: null | string
  start_at: string
  title: string
}

/** `POST /api/collect/timetable/preview` — read-only, no DB write. */
export interface TimetablePreview {
  calendar_name: string
  /** Total sessions in the file (a weekly course repeated N times counts N). */
  count: number
  /** Distinct courses, when the backend can tell them apart. */
  courses: number
  first: string
  last: string
  sample: TimetableSampleEvent[]
}

/** What the backend reports after an import (preview fields + the outcome). */
export interface TimetableImportResult extends TimetablePreview {
  created: number
  unchanged: number
  updated: number
}

/** `GET /api/collect/timetable` — null when nothing was ever imported. */
export interface TimetableStatus {
  calendar_name: string
  event_count: number
  imported_at: string
  path: string
}

export async function getTimetable(): Promise<null | TimetableStatus> {
  try {
    return await window.plobiDesktop.api<TimetableStatus>({ path: '/api/collect/timetable' })
  } catch {
    // 404 / empty is the normal "never imported" case — not an error to surface.
    return null
  }
}

export async function previewTimetable(path: string): Promise<TimetablePreview> {
  return window.plobiDesktop.api<TimetablePreview>({
    path: '/api/collect/timetable/preview',
    method: 'POST',
    body: { path }
  })
}

export async function importTimetable(path: string): Promise<TimetableImportResult> {
  return window.plobiDesktop.api<TimetableImportResult>({
    path: '/api/collect/timetable/import',
    method: 'POST',
    body: { path }
  })
}
