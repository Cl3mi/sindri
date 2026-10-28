// The Characteristic column's choices (operator feedback 2026-10-28: pick from
// a list, but keep free text). Exactly the names parser.py can emit --
// tests/test_campaign_static.py pins the mirror -- so a pick never introduces
// a label the scorer and the exports have not seen. The list OFFERS; it never
// restricts: any typed text still commits as before.

export const CHAR_TYPES = [
  'Distance', 'Diameter', 'Radius', 'Flatness', 'Position', 'Circularity',
  'Concentricity', 'Cylindricity', 'Parallelism', 'Perpendicularity',
  'Angularity', 'Theoretical', 'Reference', 'Material', 'Note',
];

// `typed` is the cell's text now, `current` what it held on focus. An
// untouched cell offers everything: showing only the value it already has
// would hide the alternatives the list was opened to find.
export function suggest(typed, current) {
  const q = String(typed ?? '').trim().toLowerCase();
  if (!q || typed === current) return [...CHAR_TYPES];
  const starts = CHAR_TYPES.filter((t) => t.toLowerCase().startsWith(q));
  const contains = CHAR_TYPES.filter((t) => !t.toLowerCase().startsWith(q) && t.toLowerCase().includes(q));
  return [...starts, ...contains];
}

// Highlight index after an arrow key; -1 is "none", so a plain Enter keeps
// committing whatever was typed.
export function moveHighlight(index, delta, n) {
  if (n <= 0) return -1;
  if (index < 0) return delta > 0 ? 0 : n - 1;
  return (index + delta + n) % n;
}
