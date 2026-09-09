# The HYBRID arm — registered prediction, written BEFORE the run

Written 2026-09-09, before any hybrid code existed and before any GPU time was
spent. Handoff: `docs/plans/2026-09-09-session-handoff.md` §3 and §6.3.

**The arm.** `Qwen/Qwen2.5-VL-32B-Instruct-AWQ` serves every *localisation*
pass; `Qwen/Qwen2.5-VL-72B-Instruct-AWQ` serves every *transcription* pass. One
model per H100. Judged against `r3-awqcontrol-scoped` (133.93), which is the
same read stack, the same quantisation, no adapter, under the same scope policy.

**Why it is the only lead on `missed`.** Rung 1 threw render resolution, tile
size and both merge knobs at missed values and every one lost. The detector's
WEIGHTS are the only thing that has ever moved the bucket, and the 32B is the
only cheaper set of weights that moves it the right way: 70 missed against the
72B's 88, at 890 predictions against 569.

---

## 1. The numbers this is predicted from

15 documents, 311 gold values, scope policy `--max-pages 1 --dpi 300
--exclude-clamped`, weights `miss=10 escaped=5 false=2 flag=1`.

| | 72B production | 32B | source |
|---|---|---|---|
| cost | 133.93 | 172.73 | handoff §2 |
| recall | 0.7170 | 0.7749 | handoff §2 |
| matched | 223 | 241 | recall x 311 |
| missed | 88 | 70 | handoff §3 |
| n_pred | 569 | 890 | handoff §2 |
| false_detection | 346 | 649 | n_pred - matched |
| field_acc | 0.4798 | 0.2614 | handoff §2 |
| escaped_error | 71 | 100 | silent-wrong x 311 |
| silent per MATCHED row | 31.8% | 41.5% | escaped / matched |

---

## 2. VOID gates — checked before the result is read as a result

An arm that fails any of these measured something other than what it claims,
and its cost delta means nothing. Same discipline as `loraread`'s `n_pred ==
926`, which is what proved the adapter scoping was real.

1. **`n_pred == 890`, exactly.** Detection is a pure function of the detect
   model under greedy decoding, and the masking that precedes it (notes, marks,
   title) is CV or detection-driven — no read reaches it. If `n_pred` is 569 the
   32B never served detection; anything between is worse, because it means the
   split is partial.
2. **`field_acc >= 0.40`.** The 32B reads at 0.2614. A hybrid landing there did
   not route reads to the 72B, and would otherwise be reported as "the 32B's
   boxes are unreadable" — a plausible, complete, wrong conclusion.
3. **`RunConfig` says so**: `serving_backend=hybrid`, `detect_model` = the 32B
   id, `model_id` = the 72B AWQ id, in every dump. `git_sha` is always
   `"unknown"` in the container, so this is the only record that the run is what
   its name says.

---

## 3. The prediction

| quantity | predicted | band | reasoning |
|---|---|---|---|
| `n_pred` | 890 | exact | pure function of the detect model |
| `missed` | 70 | 68-72 | matching is dominantly geometric; the value bonus is a tiebreaker |
| recall | 0.7749 | 0.770-0.780 | as above |
| `false_detection` | 649 | 630-660 | `n_pred - matched`, both near-fixed |
| `field_acc` | 0.4798 | 0.44-0.50 | 72B reads; the 18 recovered rows are rows the 72B's own detector never found, so slightly worse is expected |
| `escaped_error` | 77 | 70-85 | 241 matched x the 72B's 31.8% silent rate |
| **cost** | **164.60** | **160-170** | below |

**The cost arithmetic, decided in advance** (deltas against production, x15
documents):

```
missed      -18 x 10 = -180
false      +303 x  2 = +606
escaped      +6 x  5 =  +30
flagged      +4 x  1 =   +4
                      ------
                        +460  /15 = +30.67  ->  133.93 + 30.67 = 164.60
```

**So the hybrid is predicted to LOSE by about +30, and the loss is arithmetic
rather than empirical.** `n_pred` and `matched` are both near-fixed, so
`false_detection` is near-fixed at 649, and 303 extra spurious boxes at `w=2`
cost +606 against the +180 that 18 recovered values are worth. Saying this after
the run would be hindsight; it is registered here instead.

**What would have to be true for it to win:**

* `false_detection <= 419` — the 72B's reads would have to convert 230 of the
  32B's spurious boxes into matches. Nothing in the pipeline drops a detection
  on a bad read: `extract()` appends a `Characteristic` for every detection
  regardless of what came back, so this is not expected.
* or a missed value costs **>=17.8x** a spurious box. Today's `weights.json`
  says 5x. Equivalently, holding `miss=10`, a spurious box would have to cost
  **<=0.48** rather than 2.0.

That second line is the useful one, and it is a client question, not a model
question — handoff §5 already flags re-deriving `weights.json` as possibly worth
more than another arm. This arm produces the number that conversation needs.

---

## 4. What the arm buys even when it loses

The cost verdict is pre-computed; these three quantities are not, and none of
them is obtainable any other way:

1. **The ceiling on `missed` for any detector swap.** 22.5% missed *with
   72B-quality reads* is the best a 32B-detect pipeline can deliver. Every
   future filter's budget is measured against that number.
2. **Whether the 32B's extra recall survives good reading.** If `missed` lands
   near 88 rather than 70, the 32B's advantage was an artefact of its own reads
   feeding the matcher's `value_bonus`, and the lead closes for good — cheaply.
3. **The size of the filter that would make it pay.** The dumps record what the
   72B read on each of the ~649 boxes. If the spurious ones read empty, a
   drop-empty filter removes them at ~zero recall cost and the hybrid becomes
   the best arm in the campaign; if they read plausible values, no filter can
   separate them and the whole direction is closed. This is the arm's real
   payload, and it is why it is worth the night.

---

## 5. Judging it

`experiment.py` judges an arm against the control its comparison file names, so
the comparison is `r3-awqcontrol-scoped` -> `r3-hybrid-scoped`. Never against
`r3-32bawq-scoped`: that run differs in two variables at once.

Not on review cost alone (`CLAUDE.md` §4 — cost has been wrong three times on
this corpus). The arm's own metrics are `missed`, `field_acc` and
`escaped_error`, in that order, because reducing missed and silently-wrong
values is the stated product goal and review cost is a proxy for it that is
only as good as `weights.json`.

## 6. The device-placement risk, and why there is no separate control

The read model is loaded with `device_map={"": "cuda:N"}` instead of
`device_map="auto"`, because two models on two cards cannot both use `auto`.
With a single visible card `auto` places everything on `cuda:0`, so the
placement is equivalent and the dtype, kernels and weights are untouched.
`CLAUDE.md` §5 records that 16 unchanged documents gave per-document deltas of
exactly 0.0 across a GPU device change, which is the precedent that licenses
this.

The `hybridgate` stage exists to price it properly anyway — the hybrid machinery
serving the 72B on BOTH sides, which must reproduce `r3-awqcontrol` with all
per-document deltas exactly 0.0, exactly as `awqgate` priced the dependency
change. **Decision rule, registered now:** run `hybrid` first; run `hybridgate`
only if `field_acc` lands outside [0.40, 0.52], which is the band the device
change cannot explain.
