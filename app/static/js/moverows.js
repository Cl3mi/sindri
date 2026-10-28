// Move to OK / Move to Review (review UX plan task 3). Pure rules, so the
// table's buttons and its per-row action share one definition of "in the
// Review queue" -- the one state.js's isVisibleRow/counts/progress use.
// A suggestion (the tray) is not a balloon and moves nowhere: it is confirmed
// through Accept in the Low-confidence filter, never from here.

const inQueue = (r) => !!r.needs_review && !r.reviewed;

export function moveTargets(rows, ids) {
  const wanted = new Set(ids);
  const balloons = rows.filter((r) => wanted.has(r.id) && !r.suggested);
  return {
    toOk: balloons.filter(inQueue).map((r) => r.id),
    toReview: balloons.filter((r) => !inQueue(r)).map((r) => r.id),
  };
}

// The toolbar buttons act on the ticked rows, falling back to the selected one.
export function targetIds(tickedIds, selectedId) {
  if (tickedIds.length) return tickedIds;
  return selectedId ? [selectedId] : [];
}

// The per-row action: flip the row to the other side.
export function rowAction(row) {
  if (row.suggested) return null;
  return inQueue(row) ? { kind: 'ok', label: 'Move to OK' }
                      : { kind: 'review', label: 'Move to Review' };
}
