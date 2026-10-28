// Leader lines from a balloon to the value it numbers, drawn the way the
// export draws them (app/pipeline/ballooned_pdf.py::_leader_segment): a thin
// line from the balloon's rim to just outside the callout box, so it never
// crosses the number or touches the glyphs. tests/js/leader.test.mjs pins this
// port to the Python's own numbers. Pure: viewer.js owns the SVG.

// `b` is the balloon centre {x, y}; `box` is [x0, y0, x1, y1]; all in one
// coordinate space (the viewer passes screen px, with the marker's radius).
export function leaderSegment(b, box, radius, margin) {
  const x0 = box[0] - margin, y0 = box[1] - margin;
  const x1 = box[2] + margin, y1 = box[3] + margin;
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
  const dx = cx - b.x, dy = cy - b.y;
  const dist = Math.hypot(dx, dy);
  if (dist === 0) return null;
  // Slab clip of the ray b -> centre against the inflated box: the larger of
  // the per-axis entry times is where it first enters.
  let tEnter = 0;
  for (const [o, d, lo, hi] of [[b.x, dx, x0, x1], [b.y, dy, y0, y1]]) {
    if (d === 0) {
      if (!(lo <= o && o <= hi)) return null;
      continue;
    }
    const ta = (lo - o) / d, tb = (hi - o) / d;
    tEnter = Math.max(tEnter, Math.min(ta, tb));
  }
  if (tEnter >= 1) return null;
  const start = radius / dist;                 // rim of the balloon, as a ray fraction
  if (tEnter <= start) return null;            // circle already touches the margin
  return { x1: b.x + dx * start, y1: b.y + dy * start,
           x2: b.x + dx * tEnter, y2: b.y + dy * tEnter };
}

// Which rows get a line: the selected and hovered one, or all of them when the
// toolbar toggle is on (a drawing with 60 balloons would otherwise be a web).
// A row needs both a balloon and an anchor; a suggestion has no balloon.
export function leadersToDraw(rows, { selectedId, hoverId, all }) {
  return rows.filter((r) => r.balloon_xy && r.target_region && !r.suggested
    && (all || r.id === selectedId || r.id === hoverId));
}
