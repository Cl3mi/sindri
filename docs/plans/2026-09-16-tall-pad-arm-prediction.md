# The HEIGHT-DEPENDENT PAD arm — registered prediction, written BEFORE the run

Written 2026-09-16, before any GPU time. Evidence:
`docs/plans/2026-09-16-crop-height-diagnostic.md` §5.

**The arm.** `SINDRI_CROP_PAD_TALL=48` — the shipped pad of 24 everywhere, and
48 for read boxes **≥120 px tall**. Nothing else moves: same 72B AWQ checkpoint,
same image, same prompts (`aa7659f1929184ea`), no adapter, one card. Stage
`tallpad`, run `r3-tallpad`.

**Control: `r3-cropctx` (131.87), NOT `r3-awqcontrol`.** `r3-awqcontrol` ran at
pad 6, so a delta measured there would price the shipped pad change and the
height dependence together. `r3-cropctx` is pad 24 everywhere — this arm minus
its one variable. Since `_CROP_PAD` now defaults to 24, a fresh run with no
environment reproduces it exactly, `RunConfig` included.

---

## 1. Why this is not another knob

`CLAUDE.md` §2 requires a mechanism that predicts which bucket moves. This one
was measured, not assumed, across three doses of the global pad:

| bucket | n | pad 6 | pad 24 | pad 48 | 6 → 48 |
|---|---|---|---|---|---|
| `40-80` | 88 | 0.591 | 0.598 | 0.602 | +0.011 |
| `80-120` | 33 | 0.242 | 0.273 | 0.242 | +0.000 |
| **`120-200`** | **64** | 0.391 | 0.523 | **0.578** | **+0.187** |
| `>=200` | 29 | 0.586 | 0.586 | 0.586 | +0.000 |

**The entire pad response is one band**, it is monotone, and it was **still
climbing at 48** when every other band had stopped. That is also the explanation
for why a global pad 48 read better and cost more: it kept paying in `120-200`
while buying nothing in the other three and adding silent errors across all of
them.

---

## 2. Gates

**Identity gate — the arm is VOID otherwise: `n_pred == 569`, exactly.** The crop
is derived after detection and has been bit-identical at every dose (pads 6, 24
and 48 all gave 569 / `missed` 88 / `false_detection` 346). If it moves,
something other than the crop changed.

**Target bucket — the arm is REFUTED otherwise, whatever the cost does:**
`read_accuracy_by_crop_height` for `120-200` must rise from 0.523 toward 0.578,
while `40-80` and `>=200` stay where they are (0.598 and 0.586). This is the
rule that settled `readcenter`, whose target bucket was provably untouched at
64 → 64.

**Damage counter: `escaped_error`.** It moved 71 → 64 → 67 across the three
global doses, so it is demonstrably responsive — which is what §4 now requires
after the last arm's counter (`misplaced_matches`) turned out to be flat at
44 → 42 → 42 and could not have falsified anything. **`misplaced_matches` is
disqualified and must not be used here.**

---

## 3. The prediction

| quantity | control (`r3-cropctx`) | predicted |
|---|---|---|
| `n_pred` | 569 | **569 exact** |
| `120-200` accuracy | 0.523 | **0.560–0.580** |
| `40-80` accuracy | 0.598 | 0.595–0.600 |
| `>=200` accuracy | 0.586 | 0.586 |
| `80-120` accuracy | 0.273 | 0.24–0.28 (untouched; below the threshold) |
| fully-correct rows | 118 | **+3 to +4** |
| `escaped_error` | 64 | **64–67** |
| **cost** | **131.87** | **130.5–132.0** |

**Central estimate −1.0, band [−2.5, +0.5].** The gain is bounded and small by
construction: 64 rows × the 0.055 that pad 48 bought over pad 24 in that band ≈
**3.5 rows**. If each converts an escaped error to a correct read that is −17.5
over 15 documents, ≈ −1.17/doc.

**The honest risk, stated in advance.** A global pad 48 raised `escaped_error`
by 3 while raising accuracy — confidence outrunning correctness. This arm gives
48 to only 64 of 223 rows, so the same effect should be roughly a third the
size, but it is the same effect and it may eat the gain. **If `120-200` rises
and cost does not fall, the mechanism is confirmed and the trade is simply not
worth taking at today's weights** — which makes `weights.json` the deciding
number again, as it is for the adapter.

---

## 4. How it is judged, in order

1. `n_pred == 569`. Void otherwise.
2. `120-200` accuracy up, `40-80` and `>=200` unchanged. Refuted otherwise,
   whatever the cost says.
3. `escaped_rate` not up; `field_acc` up.
4. Review cost, with `ci95` and the 6-weighting robustness check.

**Ship nothing on this arm alone.** The shipped configuration is pad 24 flat and
it stays that way unless this clears (2) and (3) together.

## 5. Running it

```bash
ssh 4mehpc4_3 "tmux new -d -s tallpad '~/sindri/run_gpu_queue.sh 0 tallpad'"
# then, when .complete appears
./sync_client_data.sh pull 4mehpc4_3 '~/sindri-eval-data' r3-tallpad "$HOME/sindri-client-data"
./rescore_onepage.sh
```

Deploy first — the host must carry the height-dependent pad, and a run without
it would silently be a duplicate of `r3-cropctx` under this arm's run name. The
`launching:` line must show `SINDRI_CROP_PAD_TALL=48` and no `SINDRI_CROP_PAD`.
