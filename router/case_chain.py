"""Event chains: model a case's lifecycle as a sequence of events.

A real probate case doesn't fire one form at one event — it produces a
**stream** of filings across months or years:

  estate_intestate:
    [death] → DE-101(I) application
    [appointment_order_date, +30 days] → DE-104 PR acceptance
    [pr_appointment_date, +90 days] → DE-405 inventory
    [appointment_anniversary, +365 days] → annual account
    [final_distribution_date] → DE-507 closing statement

This module models that lifecycle. Given a Case + a `case_type`-specific
chain template, it produces the full Event sequence, dated relative to
the case's primary anchor (typically the death date or appointment date).
Each event gets routed independently via the existing router.

The chain templates are hand-curated (see LIFECYCLE_TEMPLATES). Without
them, Qwen would have to invent realistic timing — the legal-knowledge
gap. With them, the router exercise becomes "given this real-world
lifecycle, does every event produce a valid form fill?"
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

import yaml

from router.run_case import run_case
from router.schemas import Case, Event, from_dict_case, from_dict_event


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── Lifecycle templates ────────────────────────────────────────────────────
# Each entry is a list of (event_type, offset_days_from_anchor, payload).
# `offset_days_from_anchor` is measured from the case's "anchor date,"
# which is case-type-specific (death date, appointment date, etc.).
# `facts_override` lets a downstream event mutate the case facts (e.g.
# set filing_type=financial_account for the anniversary account event).

LIFECYCLE_TEMPLATES: dict[str, dict] = {
    # Only events that trigger a PR/petitioner FILING are listed.
    # Court-issued events (appointment_order_date) are intentionally
    # omitted because no form is due from the filer at that moment.
    "estate_intestate": {
        "anchor_source": ("parties", "decedent", "dod"),
        "anchor_fallback_source": ("event_date",),
        "events": [
            {"type": "decedent_death_date",        "offset": 0,    "payload": {}},
            # Scenario events — fire only if the case carries the
            # matching fact. The generator toggles these via scenario
            # variants ("renunciation", "special_admin", etc.).
            {"type": "special_admin_petition",     "offset": 3,    "payload": {},
             "requires_fact": "special_admin_requested"},
            {"type": "renunciation_filing",        "offset": 7,    "payload": {},
             "requires_fact": "renunciation_filed"},
            {"type": "bond_filing",                "offset": 30,   "payload": {},
             "requires_fact": "bond_required"},
            {"type": "elective_share_filing",      "offset": 45,   "payload": {},
             "requires_fact": "elective_share_filed"},
            {"type": "pr_appointment_date",        "offset": 90,   "payload": {}},
            # Creditor claim sub-flow. Triggered only when scenario
            # marks `creditor_claim_filed`. The PR either pays or
            # disallows; on disallowance the claimant may file
            # PP-409 (Petition to Resolve Disputed Claim).
            {"type": "claim_filing_date",          "offset": 120,  "payload": {},
             "requires_fact": "creditor_claim_filed"},
            {"type": "claim_disallowance_notice_date", "offset": 150, "payload": {},
             "requires_fact": "claim_disallowed"},
            {"type": "appointment_anniversary",    "offset": 365,  "payload": {},
             "facts_override": {"filing_type": "financial_account"}},
            {"type": "pr_removal_filing",          "offset": 400,  "payload": {},
             "requires_fact": "pr_removal_petitioned"},
            {"type": "final_distribution_date",    "offset": 730,  "payload": {}},
        ],
    },
    "estate_testate": {
        "anchor_source": ("parties", "decedent", "dod"),
        "anchor_fallback_source": ("event_date",),
        "events": [
            {"type": "decedent_death_date",        "offset": 0,    "payload": {}},
            {"type": "supervised_admin_petition", "offset": 7,    "payload": {},
             "requires_fact": "supervised_admin_requested"},
            {"type": "renunciation_filing",        "offset": 10,   "payload": {},
             "requires_fact": "renunciation_filed"},
            {"type": "will_admission_date",        "offset": 14,   "payload": {}},
            {"type": "bond_filing",                "offset": 30,   "payload": {},
             "requires_fact": "bond_required"},
            {"type": "elective_share_filing",      "offset": 30,   "payload": {},
             "requires_fact": "elective_share_filed"},
            {"type": "pr_appointment_date",        "offset": 90,   "payload": {}},
            # Creditor claim sub-flow (mirrors intestate). DE-503 (Notice
            # of Disallowance) and PP-409/DE-504 (Petition to Resolve)
            # ride these two events.
            {"type": "claim_filing_date",          "offset": 120,  "payload": {},
             "requires_fact": "creditor_claim_filed"},
            {"type": "claim_disallowance_notice_date", "offset": 150, "payload": {},
             "requires_fact": "claim_disallowed"},
            {"type": "appointment_anniversary",    "offset": 365,  "payload": {},
             "facts_override": {"filing_type": "financial_account"}},
            {"type": "final_distribution_date",    "offset": 730,  "payload": {}},
        ],
    },
    "guardianship_minor": {
        "anchor_source": ("facts", "appointment_date"),
        "anchor_fallback_source": ("event_date",),
        "events": [
            {"type": "petition_filing_date",       "offset": 0,    "payload": {},
             "case_updates": [
                {"op": "set_docket", "value": "PC-{event_date}-GM"},
             ]},
            {"type": "hearing_date",               "offset": 21,   "payload": {},
             "case_updates": [
                {"op": "add_party", "from": "petitioner", "to": "guardian"},
                {"op": "set_facts",
                 "facts": {"appointment_date": "{event_date}"}},
             ]},
            {"type": "appointment_order_date",     "offset": 28,   "payload": {}},
            {"type": "appointment_anniversary",    "offset": 365,  "payload": {}},
        ],
    },
    "adoption": {
        "anchor_fallback_source": ("event_date",),
        "events": [
            {"type": "petition_filing_date",       "offset": 0,    "payload": {}},
            {"type": "hearing_date",               "offset": 60,   "payload": {}},
            {"type": "adoption_finalization_date", "offset": 180,  "payload": {}},
        ],
    },
    "name_change": {
        "anchor_fallback_source": ("event_date",),
        "events": [
            {"type": "petition_filing_date",       "offset": 0,    "payload": {}},
            {"type": "hearing_date",               "offset": 30,   "payload": {}},
            {"type": "judgment_entry_date",        "offset": 45,   "payload": {}},
        ],
    },
    "small_estate": {
        "anchor_source": ("parties", "decedent", "dod"),
        "events": [
            {"type": "decedent_death_date",        "offset": 30,   "payload": {}},
        ],
    },
    "guardianship_adult": {
        "anchor_source": ("facts", "appointment_date"),
        "anchor_fallback_source": ("event_date",),
        # State evolution: at the hearing the case moves out of ex-parte
        # emergency posture (clear emergency_basis), and the court then
        # appoints the petitioner as guardian — so downstream events see
        # `parties.guardian` and the annual report (PP-209) can fire.
        "events": [
            {"type": "petition_filing_date",       "offset": 0,    "payload": {},
             "case_updates": [
                {"op": "set_docket", "value": "PC-{event_date}-GA"},
             ]},
            {"type": "hearing_date",               "offset": 21,   "payload": {},
             "facts_override": {"emergency_basis": None},
             "case_updates": [
                {"op": "add_party", "from": "petitioner", "to": "guardian"},
                {"op": "set_facts",
                 "facts": {"appointment_date": "{event_date}"}},
             ]},
            # Appointment-issuance event — PP-203/PP-207 acceptance
            # forms anchor here (per anchor_overrides.json).
            {"type": "appointment_order_date",     "offset": 28,   "payload": {}},
            {"type": "appointment_anniversary",    "offset": 365,  "payload": {}},
        ],
    },
    "conservatorship": {
        "anchor_source": ("facts", "appointment_date"),
        "anchor_fallback_source": ("event_date",),
        # Two flavors share the same template:
        # - initial_petition scenario fires the petition + acceptance chain
        # - account/report scenarios fire only the anniversary events
        # `requires_fact` gates each event so the chain stays
        # situation-appropriate.
        "events": [
            {"type": "petition_filing_date",       "offset": 0,    "payload": {},
             "requires_fact": "is_initial_petition"},
            {"type": "hearing_date",               "offset": 21,   "payload": {},
             "requires_fact": "is_initial_petition"},
            {"type": "appointment_order_date",     "offset": 28,   "payload": {},
             "requires_fact": "is_initial_petition"},
            # Accounting/report cycle — present in initial_petition
            # variant (after appointment) AND in the year-1 / year-2
            # account-only scenarios.
            {"type": "appointment_anniversary",    "offset": 365,  "payload": {}},
            {"type": "appointment_anniversary",    "offset": 730,  "payload": {},
             "facts_override": {"filing_type": "status_report"}},
        ],
    },
}


# ── Utilities ──────────────────────────────────────────────────────────────

def _dig(d: dict, path: tuple) -> object | None:
    cur: object = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    return cur


def _add_days(iso: str, days: int) -> str:
    d = datetime.date.fromisoformat(iso)
    return (d + datetime.timedelta(days=days)).isoformat()


def _apply_case_update(running: dict, upd: dict, event_date: str) -> None:
    """Mutate `running` (case dict) in place per a case_updates entry.

    Supported ops:
      - add_party: {from: src_role, to: dst_role}
        Copies parties[src_role] into parties[dst_role]. Common pattern:
        after appointment, the petitioner becomes the guardian.
      - set_facts: {facts: {key: value, ...}}
        Sets persistent facts. Values may contain {event_date} which
        will be substituted with the current event's ISO date.
      - set_docket: {value: "PC-2026-GA-001"}
        Assign a docket number if none exists yet.
    """
    case = running.setdefault("case", {})
    op = upd.get("op")
    if op == "add_party":
        parties = case.setdefault("parties", {})
        src = parties.get(upd["from"])
        if src is not None and upd["to"] not in parties:
            parties[upd["to"]] = json.loads(json.dumps(src))
    elif op == "set_facts":
        facts = case.setdefault("facts", {})
        for k, v in (upd.get("facts") or {}).items():
            if isinstance(v, str):
                v = v.replace("{event_date}", event_date)
            facts[k] = v
    elif op == "set_docket":
        if not case.get("docket_number"):
            val = upd.get("value", "auto-assigned")
            if isinstance(val, str):
                val = val.replace("{event_date}", event_date)
            case["docket_number"] = val


def expand_chain(case_dict: dict, anchor_date: str | None = None
                 ) -> list[dict]:
    """Expand a Case dict into a list of (case_dict, event_dict) pairs.

    State is rolled forward across events: each event sees the case AS
    OF that event, and after the snapshot is captured, the event's
    `case_updates` mutate the running state for downstream events.

    `facts_override` is applied PRE-snapshot and PERSISTS forward (set
    a key to None to clear it).
    `case_updates` is applied POST-snapshot so it only affects later
    events (e.g. add_party guardian=petitioner at the appointment-issuing
    event becomes visible at the next event, not at the appointment
    itself).
    """
    case_data = case_dict.get("case", {}) or {}
    case_type = case_data.get("case_type")
    template = LIFECYCLE_TEMPLATES.get(case_type)
    if not template:
        raise ValueError(
            f"no lifecycle template for case_type {case_type!r}; "
            f"add one to LIFECYCLE_TEMPLATES")

    # Resolve anchor date
    if anchor_date is None:
        path = template.get("anchor_source")
        if path:
            anchor_date = _dig(case_data, path)
        if not anchor_date and "anchor_fallback_source" in template:
            # Fall back: use the event date already on the case dict
            anchor_date = (case_dict.get("event") or {}).get("date")
    if not anchor_date:
        raise ValueError(
            f"could not resolve anchor date for case "
            f"{case_data.get('case_id', '?')}; provide --anchor-date")

    cid = case_data.get("case_id", "?")
    # Running mutable state — accumulates across events.
    running = json.loads(json.dumps(case_dict))
    running.setdefault("case", {}).setdefault("facts", {})

    chain = []
    for spec in template["events"]:
        # Conditional event: skip if its required fact is not set/truthy
        # in the running state. Lets a single template carry both
        # baseline and scenario-variant events; the generator decides
        # which variants fire by toggling case.facts.
        rf = spec.get("requires_fact")
        if rf and not running["case"].get("facts", {}).get(rf):
            continue

        ev_date = _add_days(anchor_date, spec["offset"])

        # 1. Apply pre-event facts override (persists forward).
        for k, v in (spec.get("facts_override") or {}).items():
            facts = running["case"].setdefault("facts", {})
            if v is None:
                facts.pop(k, None)
            else:
                facts[k] = v

        # 2. Snapshot the case AS OF this event.
        snap = json.loads(json.dumps(running))
        snap["event"] = {
            "type": spec["type"],
            "date": ev_date,
            "case_id": cid,
            "payload": dict(spec.get("payload") or {}),
        }
        chain.append(snap)

        # 3. Apply post-event case mutations so downstream events see
        #    the post-event world.
        for upd in spec.get("case_updates") or []:
            _apply_case_update(running, upd, ev_date)

    return chain


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=pathlib.Path,
                    default=pathlib.Path(__file__).parent / "seed_cases.yaml")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--anchor-date",
                    help="Override the case-type template's anchor lookup.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Expand chain + route only; skip Qwen fills.")
    ap.add_argument("--url", default="http://localhost:8088")
    ap.add_argument("--model", default="Qwen3.6-27B-FP8")
    args = ap.parse_args()

    seed = yaml.safe_load(args.seed.read_text())
    matches = [c for c in seed.get("cases", []) if c.get("id") == args.case_id]
    if not matches:
        print(f"no seed case '{args.case_id}'", file=sys.stderr)
        return 2

    chain = expand_chain(matches[0], anchor_date=args.anchor_date)
    print(f"=== chain for {args.case_id} ({len(chain)} events) ===")
    for i, step in enumerate(chain, 1):
        ev = step["event"]
        facts = step["case"].get("facts") or {}
        ft = facts.get("filing_type", "")
        print(f"  {i}. {ev['date']}  {ev['type']:30s} "
              + (f"facts.filing_type={ft}" if ft else ""))

    results: list[dict] = []
    for i, step in enumerate(chain, 1):
        ev = step["event"]
        print(f"\n[{i}/{len(chain)}] {ev['date']} {ev['type']}")
        if args.dry_run:
            # Just route — show what would fire
            from router.router import Router
            from router.schemas import from_dict_case, from_dict_event
            r = Router()
            case = from_dict_case(step["case"])
            event = from_dict_event(step["event"])
            cands = r.route(case, event, top_k=3)
            if cands:
                print(f"  → {cands[0].form_id} (conf={cands[0].confidence}, "
                      f"reasons={cands[0].reasons})")
            else:
                print(f"  → (no candidates)")
            results.append({"event": ev, "form_id":
                cands[0].form_id if cands else None,
                "confidence": cands[0].confidence if cands else None})
            continue

        # tag preserves per-event outputs: filled_router.e1_decedent_death_date.fixed.json
        tag = f"e{i}_{ev['type']}"
        result = run_case(step, qwen_url=args.url, qwen_model=args.model,
                          tag=tag)
        results.append({"event": ev, **result})

    # Summary
    print(f"\n=== chain summary for {args.case_id} ===")
    for i, r in enumerate(results, 1):
        ev = r["event"]
        fid = r.get("form_id", "-")
        errs = r.get("errors", "")
        conf = r.get("confidence", "")
        print(f"  {i}. {ev['date']}  {ev['type']:30s} → "
              f"{fid:8s} conf={conf} errors={errs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
