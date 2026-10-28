// What the viewer logs for a scan-rectangle attempt (review UX plan task 4;
// journal.py LOG_ONLY_TYPES). The grader derives how well the scan worked from
// this plus what the reviewer does to the row afterwards, so every attempt is
// logged -- an empty read, a failed request, a scan whose row is undone.
//
// The server REFUSES a malformed event and a refusal stalls the reviewer's
// whole journal, so the event is built conformant whatever the OCR returned:
// bounded text, finite numbers or null.

export const LOG_ONLY_TYPES = new Set(['scan_read']);
export const TEXT_MAX = 500;   // mirrors journal.SCAN_TEXT_MAX

const finite = (v) => typeof v === 'number' && Number.isFinite(v);

export function scanEvent({ box, ms, row, failed }) {
  const e = {
    type: 'scan_read',
    row_id: row?.id ?? null,
    box: box.map((v) => (finite(v) ? v : 0)),
    text: '',
    conf: null,
    ms: finite(ms) ? Math.max(0, Math.round(ms)) : 0,
    empty: true,
  };
  if (row) {
    const text = String(row.raw_text ?? '').trim();
    e.text = text.slice(0, TEXT_MAX);
    e.conf = finite(row.confidence) ? row.confidence : null;
    e.empty = !text && !String(row.nominal ?? '').trim();
  }
  if (failed) e.failed = true;
  return e;
}
