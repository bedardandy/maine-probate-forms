# PP-406 — known gaps

Open follow-ups surfaced by the worked examples in this directory. These are
documented limitations, not bugs in the shipped artifacts.

## Addendum overflow vs. formula recompute

**Where:** `case.overflow.json` / `filled.overflow.json` (the 8-entity case that
exceeds the form's 6 real-property slots).

**Gap:** the formula DSL's `sum_slot` op only sums the in-form slots (rows 1–6).
When entities overflow onto an addendum, the deterministic
`recompute_from_dependencies` step computes the in-form subtotal
(e.g. $1,105,000) and flags the LLM's addendum-inclusive total
(e.g. $1,155,500) as a mismatch — a false positive.

**Resolution options (neither implemented yet):**

1. Add an `addendum_sum` op to the formula DSL so a slot total can include
   referenced addendum rows.
2. Add a per-form overflow override that disables `recompute` validation on
   `gross_value_*` when an addendum is referenced, deferring the total to the
   addendum.

Until then, treat a formula mismatch on an overflow case as expected and verify
the total by hand against the addendum.
