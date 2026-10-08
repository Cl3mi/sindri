// Optional shared access token (batch design §1, decision 23). A 401 from
// any /api/* call means a token is configured and this tab does not hold a
// valid one. Wrapping fetch once, here, is the single choke point every
// api.js/upload.js/journal.js call already goes through (all of them call
// `fetch`/`globalThis.fetch` directly, never a module-local alias) --
// cheaper and less error-prone than threading a retry through each of
// their own dozen endpoints.

export function isApiPath(url) {
  try {
    return new URL(url, 'http://local').pathname.startsWith('/api/');
  } catch {
    return false;
  }
}

export function needsToken(res) {
  return res.status === 401;
}

// How the token is ever supplied (app/auth.py's LOGIN_PATH). Its own 401
// means "wrong token", not "token required" -- the server exempts this
// route from the gate entirely (decision 9), so this 401 is submitToken's
// own failure path, surfaced inline by the prompt itself. It must never be
// re-intercepted by installAuthGate's wrapped fetch below: submitToken's
// request runs through that same wrapped `fetch`, so treating this 401 the
// same as any other would make the request await the very prompt promise
// it is itself trying to resolve -- a deadlock, not just a redundant prompt.
const LOGIN_PATH = '/api/auth';

function pathOf(url) {
  try {
    return new URL(url, 'http://local').pathname;
  } catch {
    return '';
  }
}

export async function submitToken(token, fetchImpl = fetch) {
  const res = await fetchImpl(LOGIN_PATH, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  return res.ok;
}

// promptForToken: () => Promise<boolean> -- shows the UI, resolves true once
// POST /api/auth has succeeded (the cookie is already set by then), false if
// the user gives up. realFetch: the underlying fetch to wrap (injected so
// this is testable with no DOM and no real network).
export function installAuthGate(promptForToken, realFetch) {
  const fetchImpl = realFetch || (typeof fetch !== 'undefined' ? fetch.bind(globalThis) : null);
  let waiting = null;   // one prompt shared by every call that hits 401 at once
  globalThis.fetch = async (input, init) => {
    const res = await fetchImpl(input, init);
    const url = typeof input === 'string' ? input : input.url;
    if (!needsToken(res) || !isApiPath(url) || pathOf(url) === LOGIN_PATH) {
      return res;
    }
    if (!waiting) {
      waiting = promptForToken().finally(() => { waiting = null; });
    }
    const ok = await waiting;
    return ok ? fetchImpl(input, init) : res;   // no token given: surface the original 401
  };
}
