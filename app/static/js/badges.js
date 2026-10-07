// Staging badges (batch design §2): the warning each staged row carries.
// One function, so the interim confirm screen and B3's staging view say the
// same words for the same row. The facts come from the server
// (app/batch/staging.py staging_row); this only turns them into copy.

export function badgesFor(row) {
  if (row.unreadable) {
    // Nothing else is known about a file that cannot be opened, and it can
    // never be included -- this is the only thing worth saying.
    return [{ kind: 'unreadable', text: 'unreadable PDF' }];
  }
  const out = [];
  if ((row.pages || 0) > 1) {
    out.push({ kind: 'multi_page', text: 'multi-page: only sheet 1 is read; extraction quality may be poor' });
  }
  // `oversized` is null for a sheet never measured (staged before B2):
  // only a measured true earns the badge, never a guess.
  if (row.oversized === true) {
    out.push({ kind: 'oversized', text: 'oversized: read at reduced resolution; extraction quality may be poor' });
  }
  if (row.duplicate_of) {
    const d = row.duplicate_of;
    out.push({ kind: 'duplicate',
               text: `duplicate of "${d.file_name}" (batch ${d.batch_name}, ${String(d.state).replaceAll('_', ' ')})` });
  }
  return out;
}
