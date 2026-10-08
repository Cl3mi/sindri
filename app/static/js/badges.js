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

// Work-list badges (batch design §3): exported rN / a newer unexported
// revision / failed + error type / out-of-scope-but-included. Separate from
// badgesFor() above: that one describes a STAGED row's warnings, this one a
// row already past staging, and the two never apply to the same row.
export function workListBadges(doc) {
  const out = [];
  if (doc.final_revision) {
    if (doc.exported_revision === doc.final_revision) {
      out.push({ kind: 'exported', text: `exported r${doc.exported_revision}` });
    } else if (doc.exported_revision) {
      out.push({ kind: 'exported_stale',
                 text: `exported r${doc.exported_revision} (newer revision r${doc.final_revision} not exported)` });
    }
  }
  if (doc.state === 'failed') {
    out.push({ kind: 'failed', text: `failed${doc.error_type ? ` (${doc.error_type})` : ''}` });
  }
  if (doc.out_of_scope && doc.allowed_out_of_scope) {
    out.push({ kind: 'out_of_scope', text: 'out of scope (included)' });
  }
  // Retention's tombstone (batch design §4, decision 6/8): files gone,
  // review record kept. Appended last, after an export badge the row may
  // still carry from before it was purged -- what was exported and that it
  // is now gone are both true and both worth saying.
  if (doc.state === 'purged') {
    out.push({ kind: 'purged', text: 'deleted by retention' });
  }
  return out;
}

// The work-list state chip's own text (batch design §3): every other state
// reads fine as the raw word with underscores spaced out, but "purged" on
// its own reads like an error rather than the permanent, intentional
// tombstone it is -- a reviewer seeing it needs "this is gone for good and
// that is expected", not a state name.
export function stateLabel(state) {
  if (state === 'purged') return 'Deleted (retention)';
  return state.replaceAll('_', ' ');
}
