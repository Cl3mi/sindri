// Review-bar navigation (batch design §3): Prev/Skip move through a batch's
// ready/in-review drawings in list order; after Finish, the next ready
// drawing in the SAME batch first, then any batch; Jump offers every
// ready/in-review drawing regardless of batch. Pure; the bar's DOM (label,
// buttons, Ctrl+K palette) is wired in main.js (Task 13).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { navOrder, prevId, nextId, nextAfterFinish, jumpCandidates, skip, nextReadyCopy, filterJump } =
  await import('../../app/static/js/reviewbar.js');
const { navigate } = await import('../../app/static/js/router.js');

const DOCS = [
  { id: 'a', batch_id: 'b1', state: 'ready' },
  { id: 'b', batch_id: 'b1', state: 'in_review' },
  { id: 'c', batch_id: 'b1', state: 'finished' },
  { id: 'd', batch_id: 'b2', state: 'ready' },
  { id: 'e', batch_id: 'b2', state: 'extracting' },
];

test('navOrder keeps only ready/in_review drawings of one batch, in list order', () => {
  assert.deepEqual(navOrder(DOCS, 'b1').map((d) => d.id), ['a', 'b']);
});

test('prevId returns the id before the current one in the batch, or null at the start', () => {
  assert.equal(prevId(DOCS, 'b1', 'b'), 'a');
  assert.equal(prevId(DOCS, 'b1', 'a'), null);
});

test('nextAfterFinish prefers the next ready/in_review drawing in the same batch', () => {
  assert.deepEqual(nextAfterFinish(DOCS, 'b1', 'a'), { status: 'next', id: 'b' });
});

test('nextAfterFinish falls back to any other batch once this one is exhausted', () => {
  const docs = [{ id: 'a', batch_id: 'b1', state: 'ready' }, ...DOCS.slice(3)];
  assert.deepEqual(nextAfterFinish(docs, 'b1', 'a'), { status: 'next', id: 'd' });
});

test('nextAfterFinish says "extracting" when nothing is ready but something is on its way', () => {
  const docs = [{ id: 'a', batch_id: 'b1', state: 'ready' }, { id: 'e', batch_id: 'b2', state: 'extracting' }];
  assert.deepEqual(nextAfterFinish(docs, 'b1', 'a'), { status: 'extracting' });
});

test('nextAfterFinish says "done" when nothing is ready and nothing is extracting or queued', () => {
  assert.deepEqual(nextAfterFinish([{ id: 'a', batch_id: 'b1', state: 'ready' }], 'b1', 'a'),
    { status: 'done' });
});

test('jumpCandidates is every ready/in_review drawing across every batch', () => {
  assert.deepEqual(jumpCandidates(DOCS).map((d) => d.id), ['a', 'b', 'd']);
});

// skip() is the fix for decision 10: a bare hash write (what "Skip for now"
// did before this plan's revision) never fires hashchange, so pressing it
// changed the URL and nothing on screen. skip() takes `navigate` itself
// (router.js's seam) rather than returning an id for the caller to
// setHash() with -- that is exactly the shape of mistake that caused the
// bug, and a caller cannot make it again if there is no id-only return path
// to misuse. The real `navigate` is used here with a stubbed `dispatch` in
// place of routeFromHash, so this test is the "a skip actually loads the
// next drawing (stub routeFromHash)" case directly.
test('skip navigates to the next drawing in the same batch', () => {
  const dispatched = [];
  const id = skip(DOCS, 'b1', 'a', {
    navigate: (url) => navigate(url, { dispatch: () => dispatched.push(url) }),
    docUrl: (i) => `#/doc/${i}`,
  });
  assert.equal(id, 'b');
  assert.deepEqual(dispatched, ['#/doc/b']);
});

test('skip falls back to any other batch once this one is exhausted', () => {
  const docs = [{ id: 'a', batch_id: 'b1', state: 'ready' }, ...DOCS.slice(3)];
  const dispatched = [];
  const id = skip(docs, 'b1', 'a', {
    navigate: (url) => navigate(url, { dispatch: () => dispatched.push(url) }),
    docUrl: (i) => `#/doc/${i}`,
  });
  assert.equal(id, 'd');
  assert.deepEqual(dispatched, ['#/doc/d']);
});

test('skip does nothing -- and never calls navigate -- when nothing is left to skip to', () => {
  const calls = [];
  const id = skip([{ id: 'a', batch_id: 'b1', state: 'ready' }], 'b1', 'a',
    { navigate: (url) => calls.push(url), docUrl: (i) => `#/doc/${i}` });
  assert.equal(id, null);
  assert.deepEqual(calls, []);
});

// nextReadyCopy: Finish's "extracting" chain-to-next toast (CLAUDE.md §4 --
// a number must have a basis; eta_seconds is cumulative over EVERY queued
// drawing, worker.py's queue_summary, so the toast divides by queued itself
// rather than quoting the whole queue's wait for one drawing).
test('nextReadyCopy with no queue history yet says so, not a bogus number', () => {
  assert.deepEqual(nextReadyCopy(null), {
    title: 'Next drawing is still extracting', msg: 'Estimating how long it will take.',
  });
  assert.deepEqual(nextReadyCopy({ queued: 2, eta_seconds: null }), {
    title: 'Next drawing is still extracting', msg: 'Estimating how long it will take.',
  });
});

test('nextReadyCopy divides the cumulative eta by the number queued', () => {
  const copy = nextReadyCopy({ queued: 3, eta_seconds: 600 });
  // 600s / 3 = 200s per drawing = ~3.3 min, rounded.
  assert.equal(copy.title, 'Next drawing ready in ≈ 3 min');
  assert.equal(copy.msg, 'Still extracting — the work list will show it.');
});

test('nextReadyCopy never divides by zero when nothing is queued yet', () => {
  const copy = nextReadyCopy({ queued: 0, eta_seconds: 120 });
  assert.equal(copy.title, 'Next drawing ready in ≈ 2 min');
});

test('nextReadyCopy never rounds down to 0 min', () => {
  const copy = nextReadyCopy({ queued: 5, eta_seconds: 10 });
  assert.equal(copy.title, 'Next drawing ready in ≈ 1 min');
});

// filterJump: the Jump (Ctrl+K) palette's type-to-filter, case-insensitive
// by file name over the same candidates jumpCandidates() already offers.
const JUMP_DOCS = [
  { id: 'a', batch_id: 'b1', state: 'ready', file_name: 'Bracket-Left.pdf' },
  { id: 'b', batch_id: 'b1', state: 'in_review', file_name: 'bracket-right.pdf' },
  { id: 'd', batch_id: 'b2', state: 'ready', file_name: 'Housing.pdf' },
];

test('filterJump with an empty query returns every candidate, unchanged order', () => {
  assert.deepEqual(filterJump(JUMP_DOCS, '').map((d) => d.id), ['a', 'b', 'd']);
  assert.deepEqual(filterJump(JUMP_DOCS, undefined).map((d) => d.id), ['a', 'b', 'd']);
});

test('filterJump matches case-insensitively by file name substring', () => {
  assert.deepEqual(filterJump(JUMP_DOCS, 'bracket').map((d) => d.id), ['a', 'b']);
  assert.deepEqual(filterJump(JUMP_DOCS, 'HOUSING').map((d) => d.id), ['d']);
});

test('filterJump with no match returns an empty list, not everything', () => {
  assert.deepEqual(filterJump(JUMP_DOCS, 'zzz'), []);
});
