// Pure geometry for editing a row's anchor box (campaign design §3).
// Plan-space pixels, [x0, y0, x1, y1]. The journal refuses an empty or
// inverted box (journal.py move_target), and a refused event fails the whole
// flush, so the drag can never emit one.
export const MIN_SIDE = 4;

export function dragBox([x0, y0, x1, y1], handle, dx, dy) {
  if (handle === 'body') return [x0 + dx, y0 + dy, x1 + dx, y1 + dy];
  if (handle.includes('w')) x0 = Math.min(x0 + dx, x1 - MIN_SIDE);
  if (handle.includes('e')) x1 = Math.max(x1 + dx, x0 + MIN_SIDE);
  if (handle.includes('n')) y0 = Math.min(y0 + dy, y1 - MIN_SIDE);
  if (handle.includes('s')) y1 = Math.max(y1 + dy, y0 + MIN_SIDE);
  return [x0, y0, x1, y1];
}

// The box a finished drag journals, or null when it must not be journalled.
// Rounded to 0.1 px so the journal does not carry float noise from the
// view→plan transform; MIN_SIDE makes the rounding unable to collapse an
// edge, but the check is repeated here because this is the last line before
// the server, and a NaN (a zero-size viewport mid-resize) would otherwise
// sail past every comparison.
export function settleBox(box) {
  const b = box.map((v) => Math.round(v * 10) / 10);
  if (!b.every(Number.isFinite)) return null;
  if (!(b[0] < b[2] && b[1] < b[3])) return null;
  return b;
}
