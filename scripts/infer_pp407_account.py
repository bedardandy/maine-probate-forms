"""PP-407-specific: fill blank conservator's-account financial fields.

PP-407 (Conservator's Account) is the conservator-flavored analog of
DE-406 (Probate Account). Same 20+ blank-prone financial fields:
prior+current inventory values, period dates, income/expenses/distributions,
last-approved-account date.

Mirrors infer_de406_financials but with PP-407 tree field names:
  DE-406                                      | PP-407
  --------------------------------------------+--------------------------------
  period_begin / period_end                   | period_beginning / period_ending
  real_estate_value (current)                 | current_real_estate_balance
  tangible_personal_property_value (current)  | current_tangible_personal_property_balance
  intangible_personal_property_value (current)| current_intangible_personal_property_balance
  (no prior)                                  | prior_real_estate_value / prior_tangible_*
                                              | / prior_intangible_*
  accounting_number ("1st"/"2nd")             | accounting_type (free text)
  inventory_filed_date                        | inventory_filed_date (same)
  income_amount / expenses_amount             | income_amount / expenses_amount (same)
  distributions_amount                        | distributions_amount (same)

Place in the fix chain: form-aware, runs only when form_id == PP-407.
Position AFTER infer_notary_fields, BEFORE recompute_overwrite.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import random
import sys


def _seeded_rng(case_id: str, event: str) -> random.Random:
    h = hashlib.sha256(f"{case_id}|{event}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def _format_currency(amount: float) -> str:
    return f"{amount:,.2f}"


def _shift_date(iso_date: str, days: int) -> str:
    d = dt.date.fromisoformat(iso_date)
    return (d + dt.timedelta(days=days)).isoformat()


def _get(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None:
        return ""
    v = a.get("value") if isinstance(a, dict) else a
    return "" if v in (None, "", " ") else str(v).strip()


def _set(answers: dict, fid: str, value: str, source: str) -> bool:
    if fid not in answers:
        return False
    if _get(answers, fid):
        return False
    a = answers[fid]
    if isinstance(a, dict):
        a["value"] = value
        a["confidence"] = max(float(a.get("confidence") or 0), 0.75)
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": f"pp407-{source}"})
    else:
        answers[fid] = value
    return True


def _anniversary_index(event_type: str | None) -> int:
    if not event_type:
        return 1
    for tok in event_type.split("_"):
        if tok.startswith("e") and tok[1:].isdigit():
            return max(1, int(tok[1:]))
    return 1


def process(filled: dict, event_date: str | None,
            case_id: str | None, event_type: str | None
            ) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "PP-407":
        return new_filled, []

    answers = new_filled.get("answers") or {}
    case_id = case_id or "unknown-case"
    event_type = event_type or "unknown-event"
    rng = _seeded_rng(case_id, event_type)
    changes: list[tuple[str, str, str]] = []

    # ── Dates ────────────────────────────────────────────────────────
    if event_date:
        period_end = event_date
        period_begin = _shift_date(event_date, -365)
        inv_filed = _shift_date(event_date, -270)
        # Last approved account date — only exists if this is not the
        # 1st account. Approximated as 365 days before this period start.
        n = _anniversary_index(event_type)
        last_approved = _shift_date(event_date, -365 * max(1, n - 1) - 30) \
            if n > 1 else ""

        for fid, val, src in [
            ("period_beginning", period_begin, "period-from-event"),
            ("period_ending", period_end, "period-from-event"),
            ("inventory_filed_date", inv_filed, "inventory-from-event"),
        ]:
            if _set(answers, fid, val, src):
                changes.append((fid, val, src))
        if last_approved and _set(answers, "last_approved_account_date",
                                  last_approved, "last-approved-from-idx"):
            changes.append(("last_approved_account_date", last_approved,
                            "last-approved-from-idx"))

    # ── Accounting type (1st annual / 2nd annual / Final) ────────────
    n = _anniversary_index(event_type)
    ordinal = {1: "1st annual", 2: "2nd annual",
               3: "3rd annual"}.get(n, f"{n}th annual")
    if _set(answers, "accounting_type", ordinal, "accounting-from-event-idx"):
        changes.append(("accounting_type", ordinal, "accounting-from-event-idx"))

    # ── Component values — PRIOR (last approved balances) ────────────
    prior_real = round(rng.uniform(80_000, 425_000), -2)
    prior_tangible = round(rng.uniform(4_000, 28_000), -2)
    prior_intangible = round(rng.uniform(20_000, 220_000), -2)
    prior_total = prior_real + prior_tangible + prior_intangible

    for fid, amt, src in [
        ("prior_real_estate_value", prior_real, "prior-seeded"),
        ("prior_tangible_personal_property_value", prior_tangible, "prior-seeded"),
        ("prior_intangible_personal_property_value", prior_intangible, "prior-seeded"),
        ("prior_total_value", prior_total, "prior-total-computed"),
    ]:
        val = _format_currency(amt)
        if _set(answers, fid, val, src):
            changes.append((fid, val, src))

    # ── Activity during the period ───────────────────────────────────
    income = round(rng.uniform(4_000, 30_000), -2)
    expenses = round(rng.uniform(8_000, 28_000), -2)
    distributions = round(rng.uniform(0.05, 0.20) * prior_total, -2)
    # Net change ≈ market drift on intangibles + activity. Keep modest.
    net_change = round(rng.uniform(-0.05, 0.08) * prior_intangible, -2)

    for fid, amt, src in [
        ("income_amount", income, "income-seeded"),
        ("expenses_amount", expenses, "expenses-seeded"),
        ("distributions_amount", distributions, "distributions-seeded"),
        ("net_change_value", net_change, "net-change-seeded"),
    ]:
        val = _format_currency(amt)
        if _set(answers, fid, val, src):
            changes.append((fid, val, src))

    # ── CURRENT balances (after activity) ────────────────────────────
    # Simple model: real estate unchanged, tangible unchanged, intangible
    # adjusted by income - expenses - distributions + net_change.
    current_real = prior_real
    current_tangible = prior_tangible
    current_intangible = max(0, prior_intangible + income - expenses
                              - distributions + net_change)

    for fid, amt, src in [
        ("current_real_estate_balance", current_real, "current-from-prior"),
        ("current_tangible_personal_property_balance", current_tangible,
         "current-from-prior"),
        ("current_intangible_personal_property_balance", current_intangible,
         "current-from-prior+activity"),
    ]:
        val = _format_currency(amt)
        if _set(answers, fid, val, src):
            changes.append((fid, val, src))

    return new_filled, changes


def _extract(filled_path: pathlib.Path) -> tuple[str, str]:
    case_id = filled_path.parent.name
    parts = filled_path.name.split(".")
    event = parts[1] if len(parts) > 1 else ""
    return case_id, event


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--event-type", type=str, default=None)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()

    case_id_inf, event_inf = _extract(args.filled)
    case_id = args.case_id or case_id_inf
    event_type = args.event_type or event_inf
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, args.event_date, case_id, event_type)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_pp407_account: {len(changes)} field(s) populated "
          f"(case={case_id}, event={event_type}, form={filled.get('form_id')})")
    for fid, val, src in changes[:6]:
        print(f"  {fid} -> {val!r} ({src})")
    if len(changes) > 6:
        print(f"  ... and {len(changes) - 6} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
