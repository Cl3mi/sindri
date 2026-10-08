import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isApiPath, needsToken, installAuthGate } from '../../app/static/js/auth.js';

test('isApiPath recognises an API url and not a static one', () => {
  assert.equal(isApiPath('/api/batches'), true);
  assert.equal(isApiPath('/static/main.js'), false);
  assert.equal(isApiPath('http://localhost/api/health'), true);
});

test('needsToken is true only for a 401', () => {
  assert.equal(needsToken({ status: 401 }), true);
  assert.equal(needsToken({ status: 200 }), false);
  assert.equal(needsToken({ status: 409 }), false);
});

test('installAuthGate retries once after a successful prompt', async () => {
  let calls = 0;
  const fakeFetch = async (url) => {
    calls += 1;
    if (calls === 1) return { status: 401, url };
    return { status: 200, url };
  };
  let prompted = 0;
  const prompt = async () => {
    prompted += 1;
    return true;   // the prompt "succeeded"
  };
  installAuthGate(prompt, fakeFetch);
  const res = await globalThis.fetch('/api/batches');
  assert.equal(res.status, 200);
  assert.equal(prompted, 1);
  assert.equal(calls, 2);
});

test('installAuthGate surfaces the original 401 if the prompt is cancelled', async () => {
  const fakeFetch = async (url) => ({ status: 401, url });
  const prompt = async () => false;   // cancelled
  installAuthGate(prompt, fakeFetch);
  const res = await globalThis.fetch('/api/batches');
  assert.equal(res.status, 401);
});

test('installAuthGate never prompts for a non-API 401 (there is none, but defensively)', async () => {
  let prompted = 0;
  const fakeFetch = async () => ({ status: 401, url: '/static/missing.js' });
  installAuthGate(async () => { prompted += 1; return true; }, fakeFetch);
  await globalThis.fetch('/static/missing.js');
  assert.equal(prompted, 0);
});

test('installAuthGate does not re-intercept a 401 from /api/auth itself', async () => {
  // submitToken's own POST to /api/auth is how the prompt resolves a wrong
  // token: that request runs through the SAME wrapped global fetch (its
  // default fetchImpl is `fetch`, resolved at call time). If the gate
  // treated THAT 401 as "token required" too, it would try to share the
  // very prompt promise it is currently inside of -- a deadlock, since the
  // prompt can only resolve once this request returns. The gate must let
  // /api/auth's own 401 (wrong token) through untouched.
  let prompted = 0;
  const fakeFetch = async (url) => ({ status: 401, url });
  installAuthGate(async () => { prompted += 1; return true; }, fakeFetch);
  const res = await Promise.race([
    globalThis.fetch('/api/auth'),
    new Promise((_, rej) => setTimeout(() => rej(new Error('deadlocked')), 200)),
  ]);
  assert.equal(res.status, 401);
  assert.equal(prompted, 0);
});

test('installAuthGate shares one in-flight prompt across concurrent 401s', async () => {
  let calls = 0;
  const fakeFetch = async (url) => {
    calls += 1;
    return calls <= 2 ? { status: 401, url } : { status: 200, url };
  };
  let prompted = 0;
  let resolvePrompt;
  const prompt = () => {
    prompted += 1;
    return new Promise((res) => { resolvePrompt = res; });
  };
  installAuthGate(prompt, fakeFetch);
  const p1 = globalThis.fetch('/api/a');
  const p2 = globalThis.fetch('/api/b');
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(prompted, 1);   // one prompt, not two, for two concurrent 401s
  resolvePrompt(true);
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(r1.status, 200);
  assert.equal(r2.status, 200);
});
