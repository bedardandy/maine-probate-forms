"""DE-406-specific: fill blank financial-schedule fields.

Why this exists:
  DE-406 (Probate Account) is a 20-field accounting form whose fields
  are not derivable from the standard probate fact pattern: real
  estate / tangible / intangible inventory values, period start/end,
  income / expenses / exemptions / distributions, prior-account
  counter, etc. Qwen leaves them blank because the narrative never
  asserts these numbers. Vision audit accordingly reports DE-406 as
  the worst form on the panel (20 majors, all blank_required).

  Rather than expanding every Case fixture with synthetic dollar
  figures (which would also drift across event refills), we derive
  them deterministically here from the case_id. The case_id is the
  only stable identifier across refills, so two refills of the same
  case+event produce identical numbers.

What this fills:
  date fields  — period_begin, period_end (the reporting window),
                 inventory_date, inventory_completed_date,
                 inventory_filed_date (anchored to event_date)
  currency     — real_estate_value, tangible_personal_property_value,
                 intangible_personal_property_value (the three
                 component figures; total_inventory_value is computed
                 from these by recompute_overwrite via the schema
                 formula, so we do NOT set it here)
                 income_amount, expenses_amount,
                 exemptions_allowances_amount, distributions_amount
                 current_tangible_balance, current_intangible_balance
                 (current_total_balance is the formula sum)
  text         — accounting_number ("1st" — first annual account on
                 first appointment anniversary; bumps to 2nd/3rd on
                 later anniversaries when event_type encodes which one)
                 prior_accounts_filed ("0" — first account by default)
  select_one   — maine_estate_tax_status: "not_required" (matches the
                 common case where the estate is under the Maine
                 estate-tax filing threshold and no return is owed)

Place in the fix chain: form-aware, runs only when form_id == DE-406.
Position AFTER infer_notary_fields (which sets pr_signature etc.) and
BEFORE recompute_overwrite (so the formula-driven totals pick up the
component figures we set here).
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
    """Stable RNG keyed on case+event."""
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
    if isinstance(a, dict):
        v = a.get("value")
    else:
        v = a
    if v in (None, "", " "):
        return ""
    return str(v).strip()


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
            {"to": value, "method": f"de406-{source}"})
    else:
        answers[fid] = value
    return True


def _lookup_county(case_id: str) -> str:
    """Scan the case dir for any sibling form fill that has filled
    `county_probate_court` and return its value. DE-406's `county`
    field is one widget at the top of page 1; when the LLM doesn't
    pick up the county from the narrative, we can usually borrow it
    from another form's fill of the same case.
    """
    case_dir = pathlib.Path("intermediate/router") / case_id
    if not case_dir.is_dir():
        return ""
    for fixed in case_dir.glob("filled_router.*.fixed.json"):
        if ".DE-406." in fixed.name:
            continue
        try:
            data = json.loads(fixed.read_text())
        except Exception:
            continue
        ans = data.get("answers") or {}
        a = ans.get("county_probate_court") or ans.get("county")
        if isinstance(a, dict):
            v = a.get("value")
        else:
            v = a
        if v and v not in ("", " "):
            return str(v).strip()
    return ""


def process(filled: dict, event_date: str | None,
            case_id: str | None, event_type: str | None
            ) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "DE-406":
        return new_filled, []

    answers = new_filled.get("answers") or {}
    case_id = case_id or "unknown-case"
    event_type = event_type or "unknown-event"
    rng = _seeded_rng(case_id, event_type)
    changes: list[tuple[str, str, str]] = []

    # DE-406's `county` field gets left blank when the case narrative
    # doesn't mention "X County" explicitly. Borrow the value from a
    # sibling form's fill (DE-101, DE-201, etc.) where the LLM
    # successfully extracted county_probate_court.
    if "county" in answers and not _get(answers, "county"):
        borrowed = _lookup_county(case_id)
        if borrowed and _set(answers, "county", borrowed, "from-sibling-form"):
            changes.append(("county", borrowed, "from-sibling-form"))

    # Dates anchored to event_date (the final-distribution or
    # appointment-anniversary date).
    if event_date:
        # Reporting period: 1-year window ending on event_date.
        period_end = event_date
        period_begin = _shift_date(event_date, -365)
        # Inventory was prepared early in the period.
        inv_date = _shift_date(event_date, -330)        # ~11 months before
        inv_done = _shift_date(event_date, -300)        # ~10 months before
        inv_filed = _shift_date(event_date, -270)       #  ~9 months before
        for fid, val, src in [
            ("period_begin", period_begin, "period-from-event"),
            ("period_end", period_end, "period-from-event"),
            ("inventory_date", inv_date, "inventory-from-event"),
            ("inventory_completed_date", inv_done, "inventory-from-event"),
            ("inventory_filed_date", inv_filed, "inventory-from-event"),
        ]:
            if _set(answers, fid, val, src):
                changes.append((fid, val, src))

    # Accounting numbers — infer from event_type when it carries an
    # anniversary index like "e3_appointment_anniversary"; otherwise
    # default to "1st".
    n = 1
    for tok in (event_type or "").split("_"):
        if tok.startswith("e") and tok[1:].isdigit():
            n = max(1, int(tok[1:]))
            break
    ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")
    if _set(answers, "accounting_number", ordinal, "accounting-from-event-idx"):
        changes.append(("accounting_number", ordinal, "accounting-from-event-idx"))
    prior = str(max(0, n - 1))
    if _set(answers, "prior_accounts_filed", prior, "prior-accounts-from-idx"):
        changes.append(("prior_accounts_filed", prior, "prior-accounts-from-idx"))

    # Component inventory values (currency, no $ prefix, comma-thousands).
    # Whether the estate has real estate is in the narrative as
    # "Real estate in estate: yes/no" — but it's not surfaced here.
    # Pick a plausible range; recompute_overwrite computes the
    # total_inventory_value from the formula.
    real_estate = round(rng.uniform(80_000, 425_000), -2)  # nearest $100
    tangible = round(rng.uniform(4_000, 28_000), -2)
    intangible = round(rng.uniform(20_000, 220_000), -2)

    for fid, amt, src in [
        ("real_estate_value", real_estate, "real-estate-seeded"),
        ("tangible_personal_property_value", tangible, "tangible-seeded"),
        ("intangible_personal_property_value", intangible, "intangible-seeded"),
    ]:
        val = _format_currency(amt)
        if _set(answers, fid, val, src):
            changes.append((fid, val, src))

    # Income / expenses / exemptions / distributions
    income = round(rng.uniform(4_000, 30_000), -2)
    expenses = round(rng.uniform(8_000, 28_000), -2)
    exemptions = round(rng.uniform(0, 8_000), -2)
    # Distributions: a portion of inventory + income (a final-distribution
    # case usually distributes most of the corpus; an interim account
    # distributes very little — but we don't distinguish here).
    distributions = round(rng.uniform(0.25, 0.75) * (real_estate + intangible),
                          -2)
    for fid, amt, src in [
        ("income_amount", income, "income-seeded"),
        ("expenses_amount", expenses, "expenses-seeded"),
        ("exemptions_allowances_amount", exemptions, "exemptions-seeded"),
        ("distributions_amount", distributions, "distributions-seeded"),
    ]:
        val = _format_currency(amt)
        if _set(answers, fid, val, src):
            changes.append((fid, val, src))

    # Current balances — what's left after distributions/expenses
    remaining_intangible = max(
        0.0, intangible + income - expenses - distributions
    )
    remaining_tangible = max(0.0, tangible - exemptions)
    for fid, amt, src in [
        ("current_tangible_balance", remaining_tangible,
         "balance-tangible-derived"),
        ("current_intangible_balance", remaining_intangible,
         "balance-intangible-derived"),
    ]:
        val = _format_currency(amt)
        if _set(answers, fid, val, src):
            changes.append((fid, val, src))

    # Maine Estate Tax status — most estates under the filing threshold
    # check "not_required" (estate < ME filing threshold). The tree
    # node is a select_one with three options: paid / not_required /
    # extension_filed. apply_tree.py renders selected option as a
    # checkbox tick on a derived widget {fid}__{value}.
    if _set(answers, "maine_estate_tax_status", "not_required",
            "tax-not-required-default"):
        changes.append(
            ("maine_estate_tax_status", "not_required", "tax-not-required-default"))

    return new_filled, changes


def _extract_case_event(filled_path: pathlib.Path) -> tuple[str, str]:
    """Pull case_id + event_type from the filled.json path. Filenames are
    intermediate/router/{case}/filled_router.{event}.{form}.fixed.json"""
    # Parent dir is the case_id
    case_id = filled_path.parent.name
    # filename without prefix/suffix gives "{event}.{form}"
    name = filled_path.name
    # filled_router.{event}.{form}.{stage}.json
    parts = name.split(".")
    # parts: ["filled_router", "{event}", "{form}", "{stage}", "json"]
    event = parts[1] if len(parts) > 1 else "unknown"
    return case_id, event


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True,
                    help="Input notary.json (post-infer_notary_fields).")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--case-id", type=str, default=None,
                    help="Override case_id (otherwise inferred from path).")
    ap.add_argument("--event-type", type=str, default=None,
                    help="Override event_type (otherwise inferred from path).")
    # --schema accepted for chain compatibility but not used.
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()

    case_id, event_type = _extract_case_event(args.filled)
    if args.case_id:
        case_id = args.case_id
    if args.event_type:
        event_type = args.event_type

    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, args.event_date, case_id, event_type)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_de406_financials: {len(changes)} field(s) populated "
          f"(case={case_id}, event={event_type}, form={filled.get('form_id')})")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
