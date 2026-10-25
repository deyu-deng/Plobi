// One unwrapping rule for every §5-enveloped endpoint the renderer reads.
// A reply that is not an envelope (FastAPI's `{detail}` for 401/404) is a
// failure, never data — otherwise a failed read renders as "nothing here".
export function unwrapEnvelope<T>(payload: unknown, label = 'request'): T {
  if (!payload || typeof payload !== 'object' || !('ok' in payload)) {
    throw new Error(`${label} failed: unexpected response`)
  }
  const envelope = payload as { ok: boolean; data?: T; error?: string }
  if (!envelope.ok) {
    throw new Error(envelope.error || `${label} failed`)
  }
  if (!('data' in envelope) || envelope.data === undefined) {
    throw new Error(envelope.error || `${label} failed`)
  }
  return envelope.data
}
