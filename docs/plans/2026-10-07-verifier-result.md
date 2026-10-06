# Verifier arm — result: CLOSED at train selection, nothing shipped

2026-10-07. Registration: `2026-10-07-verifier-registration.md` (committed
before any verdict existed). Verdicts: `runner verify` on the GPU host (train
221 rows asked, 0 without an answer). Digests:
`docs/eval/verifier-check-train.json`, `docs/eval/verifier-profile-train.json`.
Dev and test verdicts exist on the host but were **never priced**: the
registration allows no second pick, so both splits remain unseen for this
question.

## 1. Train (selection), against the current policy (stages 1 + 2)

Control: 196 delivered = 174 correct / 6 wrong / 16 phantom; delivered
precision 0.8878, matched precision 0.9667.

| candidate | delivered precision | phantoms removed | correct lost | matched precision | passes |
|---|---|---|---|---|---|
| `verifier_below_050` | 0.8918 | 1 | 1 | 0.9665 | no |
| `verifier_below_070` | 0.8912 | 1 | 2 | 0.9663 | no |
| `verifier_below_090` | 0.8953 | 2 | 3 | 0.9661 | no |

Every candidate raises delivered precision slightly, and every one **fails the
registered rule that matched precision must not fall**: each drops one to three
correct values and no wrong ones. Nothing is selected, so nothing is priced on
dev or test, and nothing ships.

## 2. Why: the verifier does not discriminate

`verifier_p` by delivered outcome on train (phantom profile, train only):

| P(yes) | correct | wrong | phantom |
|---|---|---|---|
| < 0.5 | 1 | 0 | 1 |
| 0.5-0.9 | 2 | 0 | 1 |
| 0.9-0.99 | 92 | 5 | 8 |
| ≥ 0.99 | 79 | 1 | 6 |

191 of 196 delivered values, **including 14 of the 16 phantoms**, get P(yes) ≥
0.9. The phantoms that survive stage 2 are, to the serving model and in
context, ordinary dimension callouts. That fits the registered caveat: gold
balloons a strict SUBSET of what the client inspects, so a phantom read at ≥
0.99 confidence is most likely a real dimension the client chose not to
balloon. No gold-free question about the drawing alone can see that choice.

Predicted 8-14 phantoms removed at the selected threshold on train; measured
1-2. The premise ("the phantoms do not look like characteristics in context")
is refuted.

## 3. What this means for the remaining dev phantoms

The 30 high-confidence dev phantoms are out of reach of both families tried:
read confidence (stage 2) and a yes/no verifier. The question they raise is not
"is this a characteristic?" but **"would THIS client balloon it?"**, which is
a property of the client's inspection practice, not of the drawing. Two routes
remain, and both are decisions rather than tuning:

1. **Ask the client** what they leave unballooned (for example reference
   dimensions, repeated features, or dimensions covered by a general note), and
   encode the stated policy as gold-free rules priced like every other rule.
2. **Learn the client's ballooning choice from train gold**: a classifier on
   gold-free features of each delivered detection, trained only on train,
   validated on dev, confirmed on test. It is the only data-driven route to
   these rows, and it carries the usual overfitting risk.

Meanwhile the suggestion tray (shipped 2026-10-07) means anything a future rule
drops stays one keystroke from the reviewer.
