"""Form router v0 — deterministic case+event → ranked form candidates.

Architecture
------------
1. Read router/form_index.jsonl into memory.
2. For an incoming (case, event):
   a. Filter forms by `filing_deadline_anchor == event.type`. This is
      the primary candidate set.
   b. For each candidate, compute a confidence score from:
        - role overlap: |case.parties.keys() ∩ form.parties|
        - filer_role match: does `case.parties` contain a person with
          a role compatible with `form.filer_role`?
        - case_type compatibility: estate forms (DE-*) match estate_*
          cases; PP-* match guardianship/conservatorship; AD-* match
          adoption; NC-* match name_change.
   c. Emit reasons strings so the caller (and the user) can audit why
      each form was suggested.
3. Sort by (-confidence, n_fields ascending) so cheaper forms surface
   first when tied.

This is intentionally rule-driven, not LLM-driven. The LLM rerank
layer comes in v1 once we measure v0 precision against
router/seed_cases.yaml.

Usage
-----
    from router import router
    r = router.Router()
    results = r.route(case, event)
    for cand in results:
        print(cand.form_id, cand.confidence, cand.reasons)
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass
from typing import Iterable

from router.schemas import Case, Event


INDEX_PATH = pathlib.Path(__file__).resolve().parent / "form_index.jsonl"

# Form-id prefix → case_type compatibility. Used as a soft filter; a
# mismatch removes the form from candidates.
PREFIX_CASE_TYPES = {
    "DE": {"estate_intestate", "estate_testate", "small_estate"},
    "PP": {"guardianship_minor", "guardianship_adult", "conservatorship",
           "estate_intestate", "estate_testate"},  # PP overlaps probate
    # GS-* are the Guardian-of-Minor annual report forms. Letting them
    # fire on adult cases produces nonsense (e.g. GS-014 minor_present_age
    # gets the guardian's age and trips the range validator).
    "GS": {"guardianship_minor"},
    "AD": {"adoption"},
    "NC": {"name_change"},
    "AF": None,    # affidavits — case-type-agnostic
    "CN": None,    # consents — case-type-agnostic
    "N":  None,    # notices — case-type-agnostic
}

# filer_role canonical → set of case.parties keys that satisfy it. The
# router treats any element of the set as a positive filer match.
# Case-fact and event-payload signals that boost (or demote) specific
# forms. Each entry is keyed by (signal_source, key, expected) where:
#   signal_source ∈ {"fact", "payload"}
#   key           is the fact_name or payload_key
#   expected      is None (any truthy value triggers) or a lowercase
#                 substring that must appear in the value
# Value is a dict mapping form_id → score delta (negative demotes).
#
# Tuned for the seed cases; extend as new failure modes surface.
# Each entry: (signal_source, key, expected_value, boost_map, [event_type]).
#   signal_source ∈ {"fact", "payload", "fact_absent"}
#     - "fact" / "payload": fires when bag[key] is truthy (and matches
#       expected_value if given)
#     - "fact_absent": fires when case.facts[key] is missing/falsy.
#       Use to demote forms that need an explicit case signal to fire.
#   expected_value:
#     - None  → any truthy value triggers (or, for fact_absent, absence)
#     - True  → value must be truthy (bool true / non-empty)
#     - str   → exact case-insensitive match (enum-style facts)
# boost_map: {form_id: score_delta} (negative demotes).
# event_type (optional 5th element): if set, boost only fires when
#   event.type == event_type. Without this gate a fact like
#   `filing_type=standard` would boost the petition form at every
#   downstream event in the chain, swamping anchor-matched forms.
FACT_BOOSTS: list[tuple[str, str, object, dict[str, float]]] = [
    # Emergency markers → boost PP-507 (emergency affidavit), demote the
    # standard adult-guardianship petitions.
    ("fact", "emergency_basis", None,
        {"PP-507": 0.5, "PP-201": -0.2, "PP-205": -0.1}),
    ("payload", "emergency", True,
        {"PP-507": 0.4}),

    # Standard (non-emergency) guardianship posture → boost the
    # ordinary petitions. Triggered explicitly via filing_type=standard
    # so it doesn't fire on missing-fact cases. Gated to
    # petition_filing_date so it doesn't overpower anchor-matched
    # forms at downstream events.
    ("fact", "filing_type", "standard",
        {"PP-201": 0.4, "PP-205": 0.3, "PP-507": -0.2},
        "petition_filing_date"),

    # Initial conservatorship petition → boost the conservator-specific
    # petition (PP-401) and demote the adult-guardian petition that
    # otherwise wins by party-overlap. Also boost the conservator-flavored
    # acceptance/account/inventory forms downstream so PP-402/PP-406/PP-407
    # win over their guardian-flavored siblings (PP-203/PP-209) when the
    # case is on the conservatorship track.
    ("fact", "is_initial_petition", None,
        {"PP-401": 0.5, "PP-201": -0.3, "PP-205": -0.2,
         "PP-402": 0.4, "PP-203": -0.3,
         "PP-406": 0.3,
         "PP-407": 0.2, "PP-209": -0.2}),

    # Dual guardian-and-conservator petition prefers PP-205 over PP-201.
    ("fact", "dual_gc_petition", None,
        {"PP-205": 0.4, "PP-201": -0.2}),

    # Testacy disambiguates DE-101 (intestate) vs DE-201 (testate).
    # Exact match — without it, "testate" matches "intestate" as a
    # substring and both rules misfire on the same case.
    ("fact", "testacy", "intestate",
        {"DE-101": 0.3, "DE-201": -0.3}),
    ("fact", "testacy", "testate",
        {"DE-201": 0.3, "DE-101": -0.3}),

    # Real-estate facts → boost real-estate notice forms.
    ("fact", "real_estate_in_estate", None,
        {"DE-507": 0.1, "DE-502": 0.1}),

    # Niche-path scenarios. These fire at their dedicated sub-event
    # types (renunciation_filing etc.) — the boost reinforces the
    # routing for scenario-variant chains.
    ("fact", "renunciation_filed", None,
        {"DE-407": 0.4}),
    ("fact", "special_admin_requested", None,
        {"DE-301": 0.4}),
    ("fact", "elective_share_filed", None,
        {"DE-506": 0.4}),
    ("fact", "pr_removal_petitioned", None,
        {"DE-509": 0.4}),
    ("fact", "supervised_admin_requested", None,
        {"DE-501": 0.4}),
    ("fact", "bond_required", None,
        {"DE-403": 0.4, "DE-502": 0.2}),

    # Filing type at an anniversary event disambiguates the conservator
    # account (PP-407, financial) from the conservator report (PP-412,
    # status). Both ride the same anchor.
    ("fact", "filing_type", "financial_account",
        {"PP-407": 0.3, "PP-412": -0.2,
         # DE-406 (Probate Account) is the PR's annual financial filing;
         # boost it on financial_account, demote when the event isn't
         # accounting-flavored so it doesn't false-fire at appointment.
         "DE-406": 0.4}),
    ("fact", "filing_type", "status_report",
        {"PP-412": 0.3, "PP-407": -0.2}),

    # MISC-101 (Motion) anchors to hearing_date, but semantically a
    # motion *causes* a hearing — it shouldn't fire on every hearing.
    # Demote it unless the case has explicit motion_filed=true; this
    # pulls its score from 0.75 to 0.65, below the 0.7 multi-threshold,
    # so it only surfaces when a future case template marks the hearing
    # as motion-driven.
    ("fact_absent", "motion_filed", None,
        {"MISC-101": -0.1}),
]


FILER_ROLE_SATISFIERS = {
    "petitioner": {"petitioner", "applicant", "movant"},
    "applicant": {"applicant", "petitioner"},
    "personal_representative": {"personal_representative", "applicant"},
    "personal_representative_or_petitioner":
        {"personal_representative", "petitioner", "applicant"},
    "conservator": {"conservator"},
    "guardian": {"guardian"},
    "guardian_or_conservator": {"guardian", "conservator"},
    "affiant": {"affiant", "petitioner", "applicant"},
    "claimant_or_pr": {"claimant", "personal_representative"},
    "movant": {"movant", "petitioner"},
    "court": set(),    # court-filed; case need not have a filer party
}


@dataclass
class Candidate:
    form_id: str
    confidence: float
    reasons: list[str]
    form_title: str
    n_fields: int | None
    filer_role: str | None
    deadline_anchor: str | None


def _form_prefix(form_id: str) -> str:
    m = re.match(r"^([A-Z]+)", form_id)
    return m.group(1) if m else ""


def _normalize_role(p):
    """Form-index `parties` entries are mostly strings but can be dicts."""
    if isinstance(p, str):
        # strip optional `(...)` suffixes like "petitioner (surviving spouse)"
        return p.split(" ")[0].strip().lower()
    if isinstance(p, dict):
        return (p.get("role") or "").lower()
    return ""


def _case_filer_keys(case: Case) -> set[str]:
    return set(case.parties.keys())


def _case_role_keys(case: Case) -> set[str]:
    # parties + extra_parties give role coverage; party_lists are
    # plural variants we map back to singular by trimming trailing 's'.
    keys = set(case.parties.keys()) | set(case.extra_parties.keys())
    for k in case.party_lists.keys():
        keys.add(k.rstrip("s"))
    return keys


class Router:
    def __init__(self, index_path: pathlib.Path = INDEX_PATH):
        self.forms: list[dict] = [
            json.loads(l) for l in index_path.read_text().splitlines()
            if l.strip()
        ]

    # ---- public ----
    def route(self, case: Case, event: Event,
              top_k: int | None = None) -> list[Candidate]:
        candidates: list[Candidate] = []
        for f in self.forms:
            cand = self._score(case, event, f)
            if cand is not None:
                candidates.append(cand)
        candidates.sort(
            key=lambda c: (-c.confidence, c.n_fields or 9999))
        if top_k:
            return candidates[:top_k]
        return candidates

    # ---- scoring ----
    def _score(self, case: Case, event: Event, form: dict) -> Candidate | None:
        fid = form["form_id"]
        anchor = form.get("filing_deadline_anchor")
        reasons: list[str] = []
        score = 0.0

        # Primary gate: deadline anchor must match the event type.
        if anchor and anchor == event.type:
            score += 0.5
            reasons.append(f"anchor_match:{anchor}")
        elif anchor and anchor != event.type:
            return None
        # If anchor is None, the form rides alongside any case — we
        # don't reject, but we don't add primary score either.

        # Case-type compatibility (soft filter).
        prefix = _form_prefix(fid)
        compat = PREFIX_CASE_TYPES.get(prefix)
        if compat is not None and case.case_type not in compat:
            return None
        if compat is not None and case.case_type in compat:
            reasons.append(f"case_type_compat:{case.case_type}")
            score += 0.1

        # Filer-role satisfaction.
        filer = (form.get("filer_role") or "").lower()
        satisfiers = FILER_ROLE_SATISFIERS.get(filer)
        case_filer_keys = _case_filer_keys(case)
        if satisfiers is None:
            # Unknown filer role; weak positive if any case party
            # exists with the same key.
            if filer in case_filer_keys:
                reasons.append(f"filer_match:{filer}")
                score += 0.1
        elif satisfiers and (case_filer_keys & satisfiers):
            reasons.append(f"filer_match:{filer}")
            score += 0.2
        elif satisfiers:
            # required filer role not present — disqualify
            return None

        # Party-role overlap (the more case parties match form
        # expected parties, the higher the fit).
        form_party_roles = {
            _normalize_role(p) for p in (form.get("parties") or [])}
        form_party_roles.discard("")
        case_role_keys = _case_role_keys(case)
        overlap = form_party_roles & case_role_keys
        if overlap:
            reasons.append(f"party_overlap:{sorted(overlap)}")
            score += 0.05 * len(overlap)

        # Fact + payload boosts (v1 precision layer).
        for entry in FACT_BOOSTS:
            source, key, expected, boost_map = entry[:4]
            event_gate = entry[4] if len(entry) > 4 else None
            if event_gate is not None and event_gate != event.type:
                continue
            if fid not in boost_map:
                continue
            if source == "fact_absent":
                # Inverted: fires when the fact is missing/falsy.
                v = case.facts.get(key)
                if v is None or v == "" or v is False:
                    delta = boost_map[fid]
                    score += delta
                    sign = "+" if delta >= 0 else ""
                    reasons.append(
                        f"fact_absent_boost:{key}(missing,{sign}{delta})")
                continue
            if source == "fact":
                bag = case.facts
            else:
                bag = event.payload
            v = bag.get(key)
            if v is None or v == "" or v is False:
                continue
            if expected is not None and expected is not True:
                # Exact case-insensitive match for enum-style facts;
                # avoids "testate" ⊂ "intestate" cross-match.
                if str(expected).lower() != str(v).lower():
                    continue
            delta = boost_map[fid]
            score += delta
            sign = "+" if delta >= 0 else ""
            reasons.append(f"{source}_boost:{key}={v}({sign}{delta})")

        if score == 0:
            return None
        return Candidate(
            form_id=fid,
            confidence=round(score, 3),
            reasons=reasons,
            form_title=form.get("form_title", ""),
            n_fields=form.get("n_fields"),
            filer_role=form.get("filer_role"),
            deadline_anchor=anchor,
        )


# ── CLI for ad-hoc routing ─────────────────────────────────────────────────

def _cli():
    import argparse
    import yaml

    from router.schemas import from_dict_case, from_dict_event

    ap = argparse.ArgumentParser(description="Route a case+event to forms.")
    ap.add_argument("--seed", type=pathlib.Path,
                    default=pathlib.Path(__file__).parent / "seed_cases.yaml")
    ap.add_argument("--case-id", help="seed case id to route (omit to run all)")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    seed = yaml.safe_load(args.seed.read_text())
    r = Router()
    cases = seed.get("cases", [])
    if args.case_id:
        cases = [c for c in cases if c.get("id") == args.case_id]
        if not cases:
            raise SystemExit(f"no seed case with id {args.case_id}")

    for c in cases:
        case = from_dict_case(c["case"])
        event = from_dict_event(c["event"])
        expected = set(c.get("expected_forms") or [])
        negative = set(c.get("negative_forms") or [])
        results = r.route(case, event, top_k=args.top)
        print(f"\n=== {c['id']} — event {event.type} on {event.date} ===")
        print(f"expected: {sorted(expected) or '(none)'}")
        if negative:
            print(f"negative: {sorted(negative)}")
        for rank, cand in enumerate(results, 1):
            hit = " ✓" if cand.form_id in expected else ""
            anti = " ✗" if cand.form_id in negative else ""
            print(f"  {rank:2d}. {cand.form_id:8s} "
                  f"conf={cand.confidence:.3f} ({cand.filer_role}) "
                  f"reasons={cand.reasons}{hit}{anti}")

        proposed = {c.form_id for c in results}
        missing = expected - proposed
        fired = negative & proposed
        verdict = "OK" if not missing and not fired else "FAIL"
        print(f"  verdict: {verdict}"
              + (f"  missing={sorted(missing)}" if missing else "")
              + (f"  fired_negative={sorted(fired)}" if fired else ""))


if __name__ == "__main__":
    _cli()
