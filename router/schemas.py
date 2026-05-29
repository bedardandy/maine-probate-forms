"""Case + event schemas — the router's input contract.

These are the primitives the form router consumes. Designed to be:
- JSON-serializable (so synthetic generators can emit cases on disk
  and Qwen runs can read them via the standard JSONL pipeline)
- Sparse-friendly (most cases populate only a handful of fields; the
  rest are None)
- Aligned with the canonical role/anchor vocabulary already in
  router/form_index.jsonl, so router matching is mechanical.

Canonical role vocabulary comes from the union of `parties` across
all 79 skill.md frontmatter blocks. See `CANONICAL_ROLES` below.

Canonical event vocabulary comes from the union of distinct
`filing_deadline_anchor` values across the same 79 forms. See
`CANONICAL_EVENT_TYPES` below.

Both vocabularies are *open* — the router will tolerate unknown role
or event tokens, but routing precision drops outside the canonical
set, so the synthetic generator should stay in-vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional


# ── Canonical vocabularies (derived from router/form_index.jsonl) ──────────

# Top-frequency party roles. Long tail (single-form roles like
# "refusal_parent", "register_attorney") is supported via the open
# `extra_parties` dict on Case.
CANONICAL_ROLES = {
    "attorney", "petitioner", "notary", "respondent", "decedent",
    "personal_representative", "individual_under_protection", "adoptee",
    "affiant", "conservator", "applicant", "guardian", "minor",
    "spouse", "claimant", "objector", "gal", "ward", "appellant",
    "movant", "witness", "evaluator", "surety", "agency",
}

# Event types match the form-index `filing_deadline_anchor` vocabulary.
# A case-level event with type X routes to all forms whose anchor == X.
CANONICAL_EVENT_TYPES = {
    "decedent_death_date",
    "will_admission_date",
    "appointment_order_date",
    "appointment_date",
    "appointment_hearing_date",
    "appointment_anniversary",
    "pr_appointment_date",
    "letters_issuance_date",
    "hearing_date",
    "judgment_entry_date",
    "court_order_date",
    "final_distribution_date",
    "final_account_filing_date",
    "claim_filing_date",
    "claim_disallowance_notice_date",
    "petition_filing_date",
    "circumstance_change_date",
    "change_event_date",
    "previous_report_or_appointment",
    "adoption_finalization_date",
    "case_open",
    # Sub-events introduced for scenario-variant routing — each anchors
    # a niche path form (DE-407 renunciation, DE-301 special admin,
    # etc.). These keep the niche forms in their own lane instead of
    # competing with the primary application form at the same date.
    "renunciation_filing",
    "special_admin_petition",
    "elective_share_filing",
    "pr_removal_filing",
    "supervised_admin_petition",
    "bond_filing",
    "objection_filing",
}

# Case-type taxonomy. Mostly drives which form families are even
# candidates (e.g. an adoption case doesn't route to PP-* probate
# forms). Free-form for now; will harden once router v1 lands.
CASE_TYPES = {
    "estate_intestate", "estate_testate", "guardianship_minor",
    "guardianship_adult", "conservatorship", "adoption", "name_change",
    "small_estate",
}


# ── Schemas ────────────────────────────────────────────────────────────────

@dataclass
class Person:
    """A natural or organizational person referenced by a case."""
    full_name: str
    address: Optional[str] = None
    dob: Optional[str] = None             # ISO date string
    dod: Optional[str] = None             # decedent only
    phone: Optional[str] = None
    email: Optional[str] = None
    relationship_to_subject: Optional[str] = None
    # Free slot for role-specific attrs (bar_number, ssn_last4, ...).
    attrs: dict = field(default_factory=dict)


@dataclass
class Case:
    """A probate-court case as known at a point in time.

    Sparsely populated: the router consumes whatever is present. Fields
    are joined into form-fill payloads downstream by `fill_form.py`.
    """
    case_id: str                           # internal identifier
    case_type: str                         # one of CASE_TYPES (or free-form)
    county: Optional[str] = None
    docket_number: Optional[str] = None
    opened_date: Optional[str] = None

    # Canonical-role parties: role → Person. Use the singular role name
    # (e.g. "petitioner", not "petitioners") even for cases with
    # multiple of the same role; for repeating parties use the lists
    # below.
    parties: dict[str, Person] = field(default_factory=dict)

    # Repeating-role parties (heirs, distributees, notice_recipients,
    # service_recipients). Keyed by role-plural name.
    party_lists: dict[str, list[Person]] = field(default_factory=dict)

    # Free-form extras for the long-tail single-form roles. Same shape
    # as `parties` but never referenced by the router's primary match.
    extra_parties: dict[str, Person] = field(default_factory=dict)

    # Case-level facts that drive form selection but aren't people:
    #   testacy: "testate"|"intestate"|"unknown"
    #   has_minor_heirs: bool
    #   real_estate_in_estate: bool
    #   bond_requested: bool
    #   spouse_surviving: bool
    facts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Event:
    """A discrete event in a case's life that triggers possible filings.

    `type` should match a CANONICAL_EVENT_TYPE token; the router will
    join `Event.type == form.filing_deadline_anchor` for primary
    candidate selection. `payload` is event-specific structured data
    that downstream form-fill consumes (e.g. {"deceased_party_role":
    "decedent"} for a death event).
    """
    type: str
    date: str                              # ISO date string
    case_id: str
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Convenience constructors ──────────────────────────────────────────────

def from_dict_case(d: dict) -> Case:
    parties = {k: Person(**v) for k, v in (d.get("parties") or {}).items()}
    plists = {k: [Person(**p) for p in vs]
              for k, vs in (d.get("party_lists") or {}).items()}
    extra = {k: Person(**v) for k, v in (d.get("extra_parties") or {}).items()}
    return Case(
        case_id=d["case_id"],
        case_type=d["case_type"],
        county=d.get("county"),
        docket_number=d.get("docket_number"),
        opened_date=d.get("opened_date"),
        parties=parties,
        party_lists=plists,
        extra_parties=extra,
        facts=d.get("facts") or {},
    )


def from_dict_event(d: dict) -> Event:
    return Event(
        type=d["type"], date=d["date"], case_id=d["case_id"],
        payload=d.get("payload") or {},
    )
