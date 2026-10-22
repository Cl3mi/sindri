// Pure geometry for editing a row's anchor box (campaign design §3).
// Plan-space pixels, [x0, y0, x1, y1]. The journal refuses an empty or
// inverted box (journal.py move_target), and a refused event fails the whole
// flush, so the drag can never emit one.
export const MIN_SIDE = 4;

// How far, in SCREEN pixels, the pointer must travel before a press becomes a
// drag. At fit-page zoom one screen pixel is ~5 plan pixels, so without this a
// trembling click journals a move_target/move_row, which the campaign grades
// as the reviewer correcting the anchor or moving the balloon.
export const DRAG_DEAD_ZONE_PX = 3;

export function beyondDeadZone(start, now, px = DRAG_DEAD_ZONE_PX) {
  return Math.hypot(now.x - start.x, now.y - start.y) >= px;
}

export function dragBox([x0, y0, x1, y1], handle, dx, dy) {
  if (handle === 'body') return [x0 + dx, y0 + dy, x1 + dx, y1 + dy];
  // The floor is MIN_SIDE, or the box's own side if the detector already
  // drew it thinner, so a corner drag never widens the axis it left alone.
  const minW = Math.min(MIN_SIDE, x1 - x0);
  const minH = Math.min(MIN_SIDE, y1 - y0);
  if (handle.includes('w')) x0 = Math.min(x0 + dx, x1 - minW);
  if (handle.includes('e')) x1 = Math.max(x1 + dx, x0 + minW);
  if (handle.includes('n')) y0 = Math.min(y0 + dy, y1 - minH);
  if (handle.includes('s')) y1 = Math.max(y1 + dy, y0 + minH);
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
