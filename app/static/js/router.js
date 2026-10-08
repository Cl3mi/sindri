// The hash router (batch design §3): #/ work list, #/staging/<batch>
// staging, #/doc/<id> review. Replaces B1's `#doc=<id>` (decision: this
// plan's header, "the hash scheme changes"). Pure parsing + the two builders
// that write it, so a write always round-trips through a read; the dispatcher
// that reacts to `hashchange` and swaps screens is DOM-only and lives in
// main.js (Task 13).
//
// `navigate` exists because `history.replaceState` -- what B1's setHash()
// called directly, and what parseRoute's own callers would reach for again
// here -- never fires `hashchange`. That event is the BROWSER's own
// notification of a navigation it performed (a link click, back/forward, a
// `location.hash =` assignment); a script writing history silently is
// invisible to it. So a button whose only effect was "change the URL" would
// change the address bar and nothing else (decision 10) -- `navigate` is the
// fix: it writes the url AND calls the dispatcher itself. `setUrl`/
// `dispatch` are injected (defaulting to no-ops) so this is testable without
// `window.history` or a real router; main.js's `go()` (Task 13) supplies the
// real `history.replaceState` and `routeFromHash`.

const DOC_ID_RE = /^[0-9a-f]{32}$/;

const LEGACY_DOC_RE = /^#doc=([0-9a-f]{32})$/;

export function parseRoute(hash) {
  // B1/B2 wrote `#doc=<id>` directly via setHash(); a bookmark or saved
  // link from before B3 still carries that shape, and it would otherwise
  // never match the new `#/...` grammar below and fall back to the work
  // list, silently breaking every pre-B3 link. Checked before the `#/?`
  // strip below, since that strip assumes the new scheme's leading slash.
  const legacy = LEGACY_DOC_RE.exec(hash || '');
  if (legacy) return { name: 'doc', id: legacy[1] };

  const h = (hash || '').replace(/^#\/?/, '');
  if (!h) return { name: 'worklist' };
  const parts = h.split('/').filter(Boolean);
  if (parts[0] === 'doc' && DOC_ID_RE.test(parts[1] || '')) {
    return { name: 'doc', id: parts[1] };
  }
  if (parts[0] === 'staging' && DOC_ID_RE.test(parts[1] || '')) {
    return { name: 'staging', batchId: parts[1] };
  }
  // An unrecognised route (a stale link, a typo, a future route this build
  // does not know) is never a blank screen -- it falls back to the one
  // place every session can always recover from.
  return { name: 'worklist' };
}

export const docUrl = (id) => `#/doc/${id}`;
export const stagingUrl = (batchId) => `#/staging/${batchId}`;
export const worklistUrl = () => '#/';

export function navigate(url, { setUrl = () => {}, dispatch = () => {} } = {}) {
  setUrl(url);
  dispatch();
}
