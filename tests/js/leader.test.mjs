// Leader lines in the viewer must be the export's: same start on the balloon's
// rim, same stop just outside the callout box. The expected numbers were
// produced by app/pipeline/ballooned_pdf.py::_leader_segment (the exporter's
// geometry) for the same inputs; if the Python changes, regenerate them.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { leaderSegment, leadersToDraw } = await import('../../app/static/js/leader.js');

const close = (seg, want) => {
  if (want === null) { assert.equal(seg, null); return; }
  const got = [seg.x1, seg.y1, seg.x2, seg.y2];
  want.forEach((w, i) => assert.ok(Math.abs(got[i] - w) < 1e-5, `${got} vs ${want}`));
};

test('leaderSegment matches _leader_segment on every reference case', () => {
  close(leaderSegment({ x: 0, y: 0 }, [100, -10, 140, 10], 9, 2.5), [9, 0, 97.5, 0]);
  close(leaderSegment({ x: 50, y: 50 }, [60, 60, 80, 70], 9, 2.5), [57.2, 55.4, 60, 57.5]);
  close(leaderSegment({ x: 10, y: 100 }, [0, 0, 40, 20], 9, 2.5),
        [10.993884, 91.055046, 18.611111, 22.5]);
  close(leaderSegment({ x: 100, y: 0 }, [0, -5, 20, 5], 9, 2.5), [91, 0, 22.5, 0]);
  close(leaderSegment({ x: 0, y: 0 }, [30, -10, 50, 10], 12, 3), [12, 0, 27, 0]);
});

test('no line when the balloon sits on the callout or touches its margin', () => {
  assert.equal(leaderSegment({ x: 0, y: 0 }, [-5, -5, 5, 5], 9, 2.5), null);
  assert.equal(leaderSegment({ x: 0, y: 0 }, [10, -2, 14, 2], 9, 2.5), null);
});

test('leadersToDraw: selected or hovered only, unless "all" is on', () => {
  const rows = [
    { id: 'a', balloon_xy: [0, 0], target_region: [100, -10, 140, 10] },
    { id: 'b', balloon_xy: [0, 50], target_region: [100, 40, 140, 60] },
    { id: 's', balloon_xy: [0, 90], target_region: [100, 80, 140, 100], suggested: true },
    { id: 'n', balloon_xy: [0, 120], target_region: null },
  ];
  const ids = (o) => leadersToDraw(rows, o).map((l) => l.id);
  assert.deepEqual(ids({ selectedId: 'a', hoverId: null, all: false }), ['a']);
  assert.deepEqual(ids({ selectedId: 'a', hoverId: 'b', all: false }), ['a', 'b']);
  assert.deepEqual(ids({ selectedId: null, hoverId: null, all: false }), []);
  // every row with both a balloon and an anchor; suggestions have no balloon
  assert.deepEqual(ids({ selectedId: null, hoverId: null, all: true }), ['a', 'b']);
  assert.deepEqual(ids({ selectedId: 'n', hoverId: 's', all: false }), []);
});
