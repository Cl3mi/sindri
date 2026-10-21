import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EDIT_REASONS, DELETE_REASONS, commitWithReason } from '../../app/static/js/reasons.js';

test('vocabularies mirror journal.py exactly', () => {
  assert.deepEqual([...EDIT_REASONS].sort(),
    ['misread_digit', 'other', 'wrong_char_type', 'wrong_sign_or_unit', 'wrong_tolerance']);
  assert.deepEqual([...DELETE_REASONS].sort(),
    ['duplicate', 'not_a_characteristic', 'other', 'unballooned_real_dimension', 'wrong_region']);
});

test('the op is applied once, with the chosen reason', async () => {
  const applied = [];
  await commitWithReason('edit', (reason) => ({ reason }), async () => 'misread_digit',
                         (op) => applied.push(op));
  assert.deepEqual(applied, [{ reason: 'misread_digit' }]);
});

test('dismissing applies the op with no reason; an off-vocabulary answer counts as none', async () => {
  const applied = [];
  await commitWithReason('delete', (reason) => ({ reason }), async () => undefined,
                         (op) => applied.push(op));
  await commitWithReason('delete', (reason) => ({ reason }), async () => 'misread_digit',
                         (op) => applied.push(op));
  assert.deepEqual(applied, [{ reason: undefined }, { reason: undefined }]);
});

test('a failing prompt still applies the op: the edit is never lost', async () => {
  const applied = [];
  await commitWithReason('edit', (reason) => ({ reason }),
                         async () => { throw new Error('no DOM'); }, (op) => applied.push(op));
  assert.deepEqual(applied, [{ reason: undefined }]);
});
