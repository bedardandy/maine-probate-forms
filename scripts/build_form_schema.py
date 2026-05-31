#!/usr/bin/env python3
"""Build per-field schema.json + fields.csv for a single form.

v0.2 schema:
  - 8-category taxonomy (incl. `other` sweep-up bucket)
  - Numeric risk_score (0-100) + 4-tier risk_tier
  - fill_strategy: deterministic | llm_eligible | human_required
  - data_type + data_constraints (currency, date, person_name, ...)
  - writable_when / required_when as JSON boolean trees
  - choice_group + choice_value for radios/checkboxes

Inputs:
  trees/<form>.yaml                             — widget map + field labels
  intermediate/fact_eval/<form>/eval_*.yaml     — eval evidence for risk

Output:
  repo/forms/<form>/schema.json
  repo/forms/<form>/fields.csv
"""
from __future__ import annotations
import argparse
import csv
import json
import pathlib
import re
import shutil
import sys
from collections import defaultdict

import yaml


# ──────────────────────────────────────────────────────────────────────────────
# Categorization rules — order matters; first match wins.
# Any field not matched by an explicit rule lands in `other` (low-tolerance
# sweep-up bucket, auto-flagged for hand review).
# ──────────────────────────────────────────────────────────────────────────────
CASE_CONSTANT_PATTERNS = [
    r"^county(_probate_court|_name|_of_residence|_for_order)?$",
    r"^court_name$",
    r"^location$",
    r"^in_re$",
    r"^case_(no|number|name|caption|caption_in_re)$",
    r"^docket_(no|number)(_.*)?$",
    r"^estate_of(_decedent)?$",
    r"^estate_name$",
    r"^decedent_name$",
    r"^probate_(county|docket_number|register_name|date|court_county|court_address)$",
    r"^district_(clerk_name|case_docket|case_name|docket_number)$",
]
PARTY_ATTR_PATTERNS = [
    (r"^attorney_(name|address|phone|email|bar.*|fax|full_name)$", "attorney"),
    (r"^pr_(name|address|phone|email|bar.*|full_name)$",
     "personal_representative"),
    (r"^applicant_(name|address|phone|email|full_name|legal_interest.*)$",
     "applicant"),
    (r"^petitioner_(name|address|phone|email|full_name|caption)$",
     "petitioner"),
    (r"^co_?petitioner_(name|address|phone|email|full_name|caption)$",
     "co_petitioner"),
    (r"^movant_(name|address|phone|email|full_name)$", "movant"),
    (r"^respondent_(name|address|phone|email|full_name)$", "respondent"),
    (r"^adoptee_(name|address|phone|email|full_name|name_caption|"
     r"legal_residence|mailing_address|place_of_birth|birth_name|"
     r"other_names|caption)$", "adoptee"),
    (r"^affiant_(name|address|residence|phone|email|name_jurat)$",
     "affiant"),
    (r"^minor_(name|address|phone|email|full_name|address)$", "minor"),
    (r"^decedent_(name|full_name|address|name_caption|caption)$",
     "decedent"),
    (r"^gal_(name|address|phone|email|full_name)$", "gal"),
    (r"^ward_(name|address|phone|email|full_name)$", "ward"),
    (r"^witness_(name|address|phone|email|full_name)$", "witness"),
    (r"^interested_party_(name|address|phone|email|full_name)$",
     "interested_party"),
    # Notary block (notary_state, notary_county, notary_public_name,
    # notary_appearer_name, notary_petitioner_name, notary_officer_name);
    # notary_date / notarization_date / notary_signed_* are handled as
    # signatures via SIGNATURE_PATTERNS instead.
    (r"^notary_(state|county|public_name|appearer_name|appearer|"
     r"petitioner_name|officer_name|name|day|month|year|"
     r"title|printed_name|print_name|officer_printed_name|"
     r"appearer_role)$", "notary"),
    (r"^attorney_(phone_number|email_address|email|fax_number)$",
     "attorney"),
    # Composite party-attr fields (single widget holding multiple sub-values
    # like name + address + email packed into one cell)
    (r"^petitioner_name_address(_email)?$", "petitioner"),
    (r"^petitioner_(first_name|middle_name|last_name|email_address|"
     r"city|legal_residence|full_name_address_phone|"
     r"address_email_phone|date_of_birth|contact)$", "petitioner"),
    (r"^applicant_(full_legal_name|address_email_phone|full_name|"
     r"date_of_birth|legal_residence)$", "applicant"),
    (r"^movant_name_address$", "movant"),
    (r"^movant_printed_name$", "movant"),
    (r"^decedent_(full_legal_name|date_of_death|date_of_birth|domicile)$",
     "decedent"),
    # name_of_<party> / address_of_<party> — common adoption / probate forms
    (r"^name_of_(adoptee|claimant|child|removing_party|.+)$", "name_of_other"),
    (r"^address_of_(claimant|.+)$", "address_of_other"),
    (r"^birth_mother_name$", "birth_mother"),
    (r"^putative_parent_(name|address)$", "putative_parent"),
    # Notary block — alternative word ordering (`state_notary`, `day_notary`)
    (r"^(state|county|day|month|year)_notary$", "notary"),
    # Composite party-attr fields (single widget holding multiple sub-values
    # like name + address + email packed into one cell)
    (r"^.*_name_address$", "form_subject"),
]
SIGNATURE_PATTERNS = [
    r".*_signature$",
    r"^signature.*$",
    r".*_dated$",
    r".*_signed$",
    r"^date_signed$",
    r"^dated$",
    r"^.*notary_(signature|seal)$",
    r"^notary_signed_.*$",
    r"^notary_date$",
    r"^notarization_date$",
    r".*_signed_date$",
    r"^pr_date$",
    r"^copr_date$",
    r"^affiant_date$",
    r"^petitioner_date$",
    r"^movant_date$",
    r"^applicant_date$",
    r"^party_date$",
    r"^plan_date$",
    r"^bond_date$",
    r"^statement_date$",
    r"^certificate_date$",
]
COMPUTED_PATTERNS = [
    r"^gross_value_.*$",
    r"^calc_.*$",
    r"^total_.*$",
    r"^net_.*$",
    r"^sum_.*$",
    r"^subtotal_.*$",
]
EXTERNAL_PATTERNS = [
    r"^court_assigned_.*$",
    r"^clerk_.*$",
    r"^judge_.*$",
    r"^filed_stamp_.*$",
]
LEGAL_CHOICE_PATTERNS = [
    # Affidavit / oath yes-no patterns
    r"^oath_.*$",
    r"^.*_pending_.*$",
    r"^.*_transferred$",
    # Checkbox marker fields
    r"^checked_.*$",
    r"^.*_marker$",
    r"^.*_attached$",
    r"^.*_in_maine$",
    r"^.*_outside_maine$",
    r"^demand_for_.*$",
    r"^request_.*$",
    r"^will_.*$",
    r"^elect_.*$",
    r"^.*_election$",
    r"^.*_consent$",
    r"^.*_yn$",
    r"^.*_yes_no$",
    r"^.*_with_bond$",
    r"^.*_without_bond$",
    r"^check_.*$",
    r".*_checkbox$",
    r".*_enabler$",       # section-header checkbox; siblings share its prefix
    # yes/no requests
    r"^.*_requested$",
    r"^.*_required$",
    # enumerated choices
    r"^.*_type$",
    r"^.*_scope$",
    r"^.*_bankruptcy$",
    r"^.*_conviction$",
    # affirmative declarations
    r"^anticipate_.*$",
]

CASE_CAPTION_PATTERNS = [
    # X_caption (case caption variants) → case_constant
    r"^.*_caption$",
    r"^case_caption.*$",
]
NARRATIVE_FREETEXT_PATTERNS = [
    # Explicit free-text body fields — narrative_derived, not `other`.
    r".*_info$",
    r".*_notes$",
    r".*_details$",
    r".*_description$",
    r".*_explanation$",
    r".*_reasons$",
    r".*_basis$",
    r"^narrative_.*$",
    r"^items_appraised_by_pr$",  # form-specific known free-text
    # Single-row financial declarations & status fields
    r".*_status$",
    r".*_amount$",
    r".*_institution$",
    r".*_arrangement$",
    r"^cash_.*$",
    r"^checking_.*$",
    r"^savings_.*$",
    r"^retirement_.*$",
    r"^investment_.*$",
    r"^debts?_.*$",
    r"^income_.*$",
    r"^expense_.*$",
    r"^marital_status$",
    r"^date_of_birth$",
    r"^age$",
    # bespoke narrative fields seen in petitions
    r"^.*_specify$",
    r"^.*_justification$",
    r"^.*_relationship$",
    r"^.*_period$",
    r"^.*_list$",
    r"^.*_location$",
    r"^.*_necessity_.*$",
    # additional currency-ish + accounting fields
    r".*_value$",
    r".*_fee$",
    r".*_balance$",
    r".*_payments$",
    r".*_total$",
    r".*_total_owed$",
    r".*_monthly_payment$",
    r".*_purpose$",
    r".*_count$",
    r".*_dob$",
    r"^loan_.*$",
    r"^in_re$",
    r"^in_re_.*$",
    # accounting / financial-affidavit context patterns
    r"^employer_.*$",
    r"^last_employment_.*$",
    r"^spouse_.*$",
    r".*_employed$",
    r".*_compensation_.*$",
    # bond / surety filler fields
    r"^condition_.*$",
    r"^penal_sum_.*$",
    r"^bond_.*$",
    r"^witness_.*$",
    # acknowledgment / appearance fields
    r"^.*_appearance_.*$",
    r"^.*_acknowledgment.*$",
    # generic question / answer fields (parent questionnaires, etc.)
    r"^.*_q[0-9]+$",
    # Date-shaped fields land as narrative_derived dates (data_type detector
    # already returns "date" for these; here we just rescue from `other`).
    r"^.*_date$",
    r"^date$",
    r"^date_of_.*$",
    r"^period_(begin|beginning|end|ending|start)$",
    # Free-text body fields with bespoke names but obviously narrative
    r"^.*_detail$",
    r"^.*_reason.*$",
    r"^.*_orders?$",
    r"^.*_circumstances$",
    r"^circumstances$",
    r"^circumstances_.*$",
    r"^.*_interest$",
    r"^.*_interest_other$",
    r"^.*_contact$",
    r"^.*_inquiries$",
    r"^.*_actions$",
    r"^.*_methods?$",
    r"^.*_authority$",
    r"^.*_powers$",
    r"^.*_filed$",
    r"^.*_recipients?$",
    r"^.*_results$",
    r"^.*_contacted$",
    r"^.*_intent$",
    r"^.*_facts$",
    r"^.*_status_.*$",
    r"^.*_residence$",
    r"^.*_age$",
    r"^.*_property$",
    r"^.*_attorney$",  # bare "attorney" reference, narrative_derived
    r"^.*_role$",
    r"^.*_role_if_.*$",
    r"^certification_.*$",
    r"^disbursements$",
    r"^other_disbursements$",
    r"^statement_of_.*$",
    r"^reason_for_.*$",
    r"^reasons_for_.*$",
    r"^interest_in_.*$",
    r"^copy_of_.*$",
    r"^.*_supervision_.*$",
    r"^supervision_.*$",
    r"^.*_prayer$",
    r"^.*_requests$",
    r"^.*_request$",
    r"^limit_contact_.*$",
    r"^by_signer_.*$",
    r"^.*_relationships?$",
    r"^.*_topic$",
    r"^.*_specification$",
    r"^.*_signer.*$",
    r"^proof_and_evidence$",
    r"^factual_legal_.*$",
    r"^grounds_for_.*$",
    r"^.*_disposition$",
    r"^.*_decision$",
    r"^.*_decisions?$",
    r"^.*_motion_for$",
    r"^motion_for$",
    r"^.*_footnote.*$",
    r"^footnote_.*$",
    r"^transferees_control$",
    r"^petitioner_entitled$",
    r"^.*_exception.*$",
    r"^.*_share_determined$",
    r"^elective_.*$",
    r"^individual_requesting$",
    r"^individual_current_address$",
    r"^individual_name$",
    r"^marp$",
    r"^hearing_request$",
    r"^transferring_state$",
    r"^.*_state$",
    r"^.*_town$",
    r"^.*_county$",
    r"^accounting_number$",
    r"^new_name$",
    r"^new_name_is_.*$",
    r"^budget_expenses$",
    r"^respondent_involvement$",
    r"^restore_ability_steps$",
    r"^conservatorship_duration$",
    r"^plan_version$",
    r"^.*_ability_.*$",
    r"^appointee_name$",
    r"^subject_of_.*$",
    r"^subject_name$",
    r"^appointment_role$",
    r"^pregnancy_.*$",
    r"^putative_parent_.*$",
    r"^likely_address$",
    r"^.*_likely_address$",
    r"^applicant_role_if_.*$",
    r"^applicant_.*_role$",
    r"^cert_.*$",
    r"^reference_specification$",
    r"^other_.*_specification$",
    r"^heirs_.*$",
    r"^non_registered_.*$",
    r"^prior_personal_.*$",
    r"^testamentary_.*$",
    r"^date_commission_expires$",
    r"^name_and_location_of_court$",
    r"^court_requests$",
    r"^.*_capacity$",
    r"^interested_capacity$",
    r"^fiduciary_role$",
    r"^minor_child_name$",
    r"^affidavit_(day|month|year)$",
    r"^address$",       # bare address
    r"^plaintiff$",
    r"^defendant$",
    r"^removing_party.*$",
    r"^declarant_(name|address)$",
    r"^person_waiving_notice$",
    r"^guardian_(by|its)$",
    r"^conservator_(by|its)$",
    r"^its_title$",
    r"^.*_inheritance_request$",
    r"^.*_telephone$",
    r"^guardian_or_conservator$",
    r"^.*_widget.*$",
    r"^.*_new_name$",
    r"^name_of_.*$",
    r"^.*_recipient$",
    r"^residence_of_.*$",
    r"^appellant_(name|address)$",
    r"^copr_name$",
    r"^pr_to_remove_name$",
    r"^attorney_maine_bar_number$",
    r"^personal_representative_(name|address)$",
    r"^nominate_pr_name$",
    r"^concur_in_nominating_name$",
    r"^printed_or_typed_name$",
    r"^declarant_.*$",
    r"^interim_order_.*$",
    r"^appointment_type_.*$",
    r"^appointment_explanation_.*$",
    r"^limit_contact_.*$",
    r"^restrictions_on_.*$",
    r"^.*_role$",
    r"^.*_relief$",
    # Final-residual sweep
    r"^county_of_affidavit$",
    r"^relationship_to_.*$",
    r"^exceptions$",
    r"^petitioner_full_legal_name$",
    r"^certificate_of_.*$",
    r"^.*_rights$",
    r"^.*_consistency.*$",
    r"^consistency_.*$",
    r"^emergency_(guardian|conservator)$",
    r"^guardian_address$",
    r"^conservator_(name|address)$",
    # Name-change-form specific
    r"^desired_(first|middle|last)_name$",
    r"^current_legal_name$",
    r"^petitioner_prior_names$",
    r"^.*_relatives$",
    # Probate report freetext (PP-502, PP-504, PP-506)
    r"^.*_conditions$",
    r"^.*_activities$",
    r"^.*_visits$",
    r"^.*_visits_communication$",
    r"^.*_understanding$",
    r"^medical_.*$",
    r"^functional_.*$",
    r"^cognition_.*$",
    r"^values_.*$",
    r"^risk_.*$",
    r"^enhancements_.*$",
    r"^self_care_.*$",
    r"^attend_.*$",
    r"^wish_.*$",
    r"^participate_.*$",
    r"^.*_proceedings?$",
    r"^.*_appropriateness$",
    r"^.*_alternative$",
    r"^.*_functioning$",
    r"^.*_powers$",
    r"^.*_qualifications$",
    r"^.*_needs$",
    r"^further_.*$",
    r"^challenge_.*$",
    r"^other_matters$",
    r"^proposed_.*$",
    r"^recommend_.*$",
    r"^communication_.*$",
    r"^attitude_.*$",
    r"^contest_.*$",
    r"^persons_.*$",
    r"^visitation_.*$",
    r"^nature_.*$",
]

# Repeating-slot detection.
# Pattern A — index in the middle:   <prefix>_<index>_<role>     e.g. tang_13_desc
# Pattern B — index at the end:      <prefix>_<role>_<index>     e.g. funds_received_amount_1,
#                                                                     heirs_page1_name_1
# Prefix may contain digits (e.g. `page1`); role is alpha-only so we don't
# get confused matches like `field_1_2` → (field_1, , 2).
SLOT_RE_MID = re.compile(r"^([a-z]+(?:_[a-z0-9]+)*?)_([0-9]+)_([a-z]+(?:_[a-z]+)*)$")
SLOT_RE_END = re.compile(r"^([a-z]+(?:_[a-z0-9]+)*?)_([a-z]+)_([0-9]+)$")


def match_slot(fid: str) -> tuple[str, int, str] | None:
    """Return (group, index, role) if fid matches either slot pattern,
    else None. Pattern A is tried first; Pattern B requires the field
    to end in `_<digits>`."""
    m = SLOT_RE_MID.match(fid)
    if m:
        return (m.group(1), int(m.group(2)), m.group(3))
    m = SLOT_RE_END.match(fid)
    if m:
        return (m.group(1), int(m.group(3)), m.group(2))
    return None


SLOT_RE_BARE = re.compile(r"^([a-z]+(?:_[a-z]+)*?)_(\d+)$")


def detect_bare_slot_groups(all_ids: list[str]) -> set[str]:
    """Return the set of prefixes for `<prefix>_<n>` patterns where the
    same prefix appears with 2+ indices. e.g. `distributee_1`,
    `distributee_2` → prefix `distributee` is a bare slot group.

    We require >= 2 indices to avoid mis-classifying things like
    `case_no_2025` (a docket number that happens to end in a digit run)
    as slot-bearing.
    """
    from collections import defaultdict
    by_prefix: dict[str, set[int]] = defaultdict(set)
    for fid in all_ids:
        m = SLOT_RE_BARE.match(fid)
        if m:
            by_prefix[m.group(1)].add(int(m.group(2)))
    return {p for p, idx in by_prefix.items() if len(idx) >= 2}


# Backward-compat alias for any caller still using SLOT_RE
SLOT_RE = SLOT_RE_MID


# ──────────────────────────────────────────────────────────────────────────────
# Data-type heuristics — by field-name suffix/keyword. Default text.
# ──────────────────────────────────────────────────────────────────────────────
def detect_data_type(field_id: str, classify: dict) -> tuple[str, dict]:
    fid = field_id.lower()
    sub = classify.get("subcategory", "")
    cat = classify.get("category")

    if cat == "signature" and "_dated" in fid:
        return "date", {"format": "iso8601_or_us"}
    if cat == "signature":
        return "signature", {}

    if cat == "computed":
        return "currency", {"min": 0, "decimals": 2}

    # Composite contact fields that pack name + address + contact info
    # into a single widget — treat as free text so the phone/email
    # regex doesn't reject the surrounding name + address content.
    if re.search(r"_(name_address(_email|_phone|_email_phone)?|"
                 r"address_email_phone|email_phone)$", fid):
        return "text", {"composite": True}

    if re.search(r"_(val|value|amount|enc|encumbrance|fee|cost|price)$", fid):
        return "currency", {"min": 0, "decimals": 2}
    if re.search(r"_(date|dated|on)$", fid):
        return "date", {"format": "iso8601_or_us"}
    if re.search(r"_(phone|fax|telephone)$", fid):
        return "phone", {}
    if re.search(r"_email$", fid):
        return "email", {}
    if re.search(r"_(address|street)$", fid):
        return "address", {}
    if re.search(r"_bar(_no|_number|)?$", fid):
        return "bar_number", {"jurisdiction": "ME"}
    if re.search(r"^docket_(no|number)$", fid):
        return "docket_number", {"jurisdiction": "ME"}
    if re.search(r"^(decedent|estate_of_decedent|movant_name|petitioner_name|"
                 r"applicant_name|pr_name|attorney_name)$", fid):
        return "person_name", {}
    if re.search(r"^(county_probate_court|court_name|firm_name)$", fid):
        return "entity_name", {}
    if classify.get("category") == "legal_choice":
        return "checkbox", {}

    return "text", {}


# ──────────────────────────────────────────────────────────────────────────────
# Risk scoring (0-100 capped). Tunable constants; downstream may rebucket.
# ──────────────────────────────────────────────────────────────────────────────
CATEGORY_BASE = {
    "case_constant":     0,
    "party_attr":        0,
    "computed":          0,
    "signature":         5,
    "external":          5,
    "narrative_derived": 5,
    "legal_choice":      20,
    "other":             30,
}
SUBCATEGORY_BONUS = {
    "repeating_slot": 15,
}
EVAL_WEIGHTS = {
    "wrong":         20,
    "overconfident":  8,
    "misunderstood": 12,
}
TIER_BANDS = [
    ("green",   0,  15),
    ("yellow", 16,  35),
    ("orange", 36,  65),
    ("red",    66, 100),
]


def score_risk(classify: dict, eval_risk: dict) -> dict:
    cat = classify.get("category", "other")
    sub = classify.get("subcategory")
    base = CATEGORY_BASE.get(cat, 30)
    bonus = SUBCATEGORY_BONUS.get(sub, 0)
    cat_total = base + bonus
    ev_w = (eval_risk.get("wrong") or 0) * EVAL_WEIGHTS["wrong"]
    ev_oc = (eval_risk.get("overconfident") or 0) * EVAL_WEIGHTS["overconfident"]
    ev_mc = (eval_risk.get("misunderstood") or 0) * EVAL_WEIGHTS["misunderstood"]
    raw = cat_total + ev_w + ev_oc + ev_mc
    score = min(100, max(0, raw))
    tier = next((name for name, lo, hi in TIER_BANDS if lo <= score <= hi), "red")
    return {
        "risk_score": score,
        "risk_tier": tier,
        "risk_breakdown": {
            "category_base": cat_total,
            "eval_wrong": ev_w,
            "eval_oc": ev_oc,
            "eval_miscompr": ev_mc,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Field classification.
# ──────────────────────────────────────────────────────────────────────────────
def classify_field(field_id: str) -> dict:
    fid = field_id.lower()
    for pat in CASE_CONSTANT_PATTERNS:
        if re.match(pat, fid):
            return {"category": "case_constant", "subcategory": fid}
    for pat in CASE_CAPTION_PATTERNS:
        if re.match(pat, fid):
            return {"category": "case_constant", "subcategory": "caption"}
    for pat, party in PARTY_ATTR_PATTERNS:
        if re.match(pat, fid):
            return {"category": "party_attr", "subcategory": party,
                    "party": party}
    for pat in SIGNATURE_PATTERNS:
        if re.match(pat, fid):
            return {"category": "signature", "subcategory": fid}
    for pat in COMPUTED_PATTERNS:
        if re.match(pat, fid):
            return {"category": "computed", "subcategory": fid}
    for pat in EXTERNAL_PATTERNS:
        if re.match(pat, fid):
            return {"category": "external", "subcategory": fid}
    for pat in LEGAL_CHOICE_PATTERNS:
        if re.match(pat, fid):
            return {"category": "legal_choice", "subcategory": fid}
    m = match_slot(fid)
    if m:
        prefix, idx, suffix = m
        return {
            "category": "narrative_derived",
            "subcategory": "repeating_slot",
            "slot_group": prefix,
            "slot_index": idx,
            "slot_field": suffix,
        }
    for pat in NARRATIVE_FREETEXT_PATTERNS:
        if re.match(pat, fid):
            return {"category": "narrative_derived", "subcategory": "free_text"}
    return {"category": "other", "subcategory": "unclassified"}


# ──────────────────────────────────────────────────────────────────────────────
# Fill strategy: which pipelines (deterministic / LLM / human) can handle this.
# ──────────────────────────────────────────────────────────────────────────────
def fill_strategy(classify: dict) -> dict:
    cat = classify.get("category")
    sub = classify.get("subcategory")
    if cat == "case_constant":
        return {"deterministic": True, "llm_eligible": False,
                "human_required": False,
                "source": f"case_dict.{sub}"}
    if cat == "party_attr":
        return {"deterministic": True, "llm_eligible": False,
                "human_required": False,
                "source": f"{classify.get('party')}_record.{sub}"}
    if cat == "computed":
        return {"deterministic": True, "llm_eligible": False,
                "human_required": False,
                "source": "recompute_from_dependencies"}
    if cat == "signature":
        return {"deterministic": False, "llm_eligible": False,
                "human_required": True, "source": "wet_ink"}
    if cat == "external":
        return {"deterministic": False, "llm_eligible": False,
                "human_required": False, "source": "left_blank"}
    if cat == "legal_choice":
        return {"deterministic": False, "llm_eligible": False,
                "human_required": True, "source": "human_decision"}
    if cat == "narrative_derived":
        return {"deterministic": False, "llm_eligible": True,
                "human_required": False, "source": "llm_over_narrative"}
    # other
    return {"deterministic": False, "llm_eligible": True,
            "human_required": True, "source": "triage"}


# ──────────────────────────────────────────────────────────────────────────────
# Validators — declarative tags; the universal validator interprets them.
# ──────────────────────────────────────────────────────────────────────────────
def field_validators(classify: dict, all_field_ids: list[str]) -> list[str]:
    out: list[str] = []
    sub = classify.get("subcategory")
    cat = classify.get("category")
    # Yes/no legal_choice fields get an auto value_in. The validator
    # treats truthy/falsy aliases ("true", "X", "checked", ...) as
    # matching "yes" / "no", so this doesn't reject legitimate checkbox
    # representations — it only catches hallucinated alternatives.
    if cat == "legal_choice" and sub == "yes_no":
        out.append("value_in(yes, no)")
    if sub == "repeating_slot":
        slot_group = classify.get("slot_group")
        slot_field = classify.get("slot_field")
        if slot_field in ("desc", "description"):
            out.append(f"dedupe_within({slot_group}_desc)")
            # cross-section dedupe (any other prefix that has _desc fields)
            other_prefixes = sorted({
                match_slot(f)[0]
                for f in all_field_ids
                if match_slot(f)
                and match_slot(f)[2] in ("desc", "description")
                and match_slot(f)[0] != slot_group
            })
            if other_prefixes:
                out.append("cross_section_dedupe("
                          + ",".join(f"{p}_desc" for p in other_prefixes)
                          + ")")
        if slot_field in ("val", "value", "amount", "enc", "encumbrance"):
            out.append("nonempty_if_desc")
    if cat == "computed":
        out.append("recompute_from_dependencies")
    if cat == "case_constant":
        out.append("populate_from_case_dict")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Eval-evidence loader (Qwen v2 side).
# ──────────────────────────────────────────────────────────────────────────────
def load_eval_evidence(form_id: str) -> dict[str, dict]:
    base = pathlib.Path("intermediate/fact_eval") / form_id
    agg: dict[str, dict] = defaultdict(
        lambda: {"wrong": 0, "overconfident": 0, "underconfident": 0,
                 "misunderstood": 0, "matches": 0, "not_applicable": 0,
                 "patterns_scored": 0}
    )
    for pid in range(1, 6):
        path = base / f"eval_{pid}.yaml"
        if not path.exists():
            continue
        try:
            d = yaml.safe_load(path.read_text()) or {}
        except Exception:
            continue
        for fid, v in (d.get("per_field") or {}).items():
            if not isinstance(v, dict):
                continue
            slot = agg[fid]
            slot["patterns_scored"] += 1
            acc = v.get("accuracy")
            cal = v.get("calibration")
            cmp_ = v.get("comprehension")
            if acc in ("wrong", "matches", "not_applicable"):
                slot[acc] += 1
            if cal in ("overconfident", "underconfident"):
                slot[cal] += 1
            if cmp_ == "misunderstood":
                slot["misunderstood"] += 1
    return dict(agg)


# ──────────────────────────────────────────────────────────────────────────────
# Schema builder.
# ──────────────────────────────────────────────────────────────────────────────
def _load_formulas(form_id: str, out_dir: pathlib.Path) -> dict:
    """Load per-form formulas.yaml override if present.
    Returns {field_id: <expr>} or {}."""
    path = out_dir / form_id / "formulas.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("formulas", {}) or {}


def _load_classifications(form_id: str, out_dir: pathlib.Path) -> dict:
    """Load per-form classifications.yaml override if present.
    Schema:
      overrides:
        <field_id>:
          category: ...
          subcategory: ...
          data_type: ...
          party: ...
          writable_when: {...}
          choice_group: ...
          choice_value: ...
      skill_metadata:        # optional — overrides skill.md frontmatter
        filer_role: ...
        statutes: [...]
        service_required: true|false
        filing_deadline_days: <int>
    Each key in an entry shallow-merges into the auto-classified field.
    """
    path = out_dir / form_id / "classifications.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("overrides", {}) or {}


def _load_skill_metadata(form_id: str, out_dir: pathlib.Path) -> dict:
    """Load skill_metadata override if present in classifications.yaml.

    Also folds in the per-form statutes.json sidecar (authored by
    scripts/author_statutes.py) under `statute_considerations`, so the
    statute-consideration layer flows into the built schema's
    `_skill_metadata_override` and is available to tools/fill_plan.py at fill time.
    """
    meta: dict = {}
    path = out_dir / form_id / "classifications.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        meta = data.get("skill_metadata", {}) or {}
    statutes_path = out_dir / form_id / "statutes.json"
    if statutes_path.exists():
        try:
            sidecar = json.loads(statutes_path.read_text())
        except Exception:
            sidecar = None
        if sidecar:
            meta["statute_considerations"] = {
                "governing": sidecar.get("governing", []),
                "per_question": sidecar.get("per_question", []),
                "transition_18a": sidecar.get("transition_18a", ""),
                "cross_refs": sidecar.get("cross_refs", []),
                "caselaw": sidecar.get("caselaw", []),
                "disclaimer": sidecar.get("disclaimer", ""),
            }
    return meta


def detect_conditional_sections(all_ids: list[str]) -> dict:
    """Detect <parent> + <parent>_<child>* patterns.

    Returns:
      {parent_field_id: [child_field_id, ...]}
    Parents become legal_choice (checkbox); children get
    writable_when: {field: parent, equals: true}.

    Two flavors of "parent":
      (1) Direct prefix:  field `appointment_of_guardian` plus siblings
          `appointment_of_guardian_court_name`, `appointment_of_guardian_order_date`.
      (2) `_enabler` suffix: field `appear_probate_court_enabler` gates siblings
          `appear_probate_court_name`, `appear_probate_court_date`, etc.
          Here the enabler is the parent, and the siblings share its
          stripped-of-`_enabler` prefix.
    """
    id_set = set(all_ids)
    sections: dict[str, list[str]] = {}

    # Flavor 1: direct prefix
    for parent in all_ids:
        prefix = parent + "_"
        kids = [f for f in all_ids if f.startswith(prefix) and f != parent
                and not f.endswith("_enabler")]
        if len(kids) >= 2:
            sections[parent] = kids

    # Flavor 2: <base>_enabler is the parent for <base>_<sibling> fields
    for fid in all_ids:
        if not fid.endswith("_enabler"):
            continue
        base = fid[:-len("_enabler")]
        prefix = base + "_"
        kids = [f for f in all_ids
                if f.startswith(prefix) and f != fid and f != base
                and not f.endswith("_enabler")]
        if len(kids) >= 2:
            sections[fid] = kids

    # Drop nested parents: prefer longer parents (so a deeper specific
    # prefix wins over a shorter generic one for any given child).
    final: dict[str, list[str]] = {}
    parents_sorted = sorted(sections, key=lambda p: -len(p))
    seen_kids: set[str] = set()
    for p in parents_sorted:
        kids = [k for k in sections[p] if k not in seen_kids]
        if len(kids) >= 2:
            final[p] = kids
            seen_kids.update(kids)
    return final


def _depends_on(expr) -> list[str]:
    """Walk a formula expression, collect referenced field_ids
    (both `field` ops and `sum_slot` expansions)."""
    deps: list[str] = []
    if not isinstance(expr, dict):
        return deps
    op = expr.get("op")
    if op == "field":
        deps.append(expr["id"])
    elif op == "sum_slot":
        prefix = expr["prefix"]; suffix = expr["suffix"]
        for i in range(expr["from"], expr["to"] + 1):
            deps.append(f"{prefix}_{i}_{suffix}")
    elif op in ("add", "sub", "mul", "div", "min", "max"):
        for a in expr.get("args", []):
            deps.extend(_depends_on(a))
    elif op == "abs":
        deps.extend(_depends_on(expr.get("arg")))
    elif op == "if":
        deps.extend(_depends_on(expr.get("cond")))
        deps.extend(_depends_on(expr.get("then")))
        deps.extend(_depends_on(expr.get("else")))
    # dedupe preserving order
    seen = set(); out = []
    for d in deps:
        if d not in seen:
            seen.add(d); out.append(d)
    return out


def build_schema(form_id: str, out_dir: pathlib.Path) -> dict:
    tree_path = pathlib.Path("trees") / f"{form_id}.yaml"
    if not tree_path.exists():
        print(f"missing tree: {tree_path}", file=sys.stderr)
        sys.exit(2)
    tree = yaml.safe_load(tree_path.read_text()) or {}
    nodes = tree.get("nodes") or []
    if not nodes:
        print("tree has no 'nodes:' section", file=sys.stderr)
        sys.exit(3)

    eval_risk = load_eval_evidence(form_id)
    formulas = _load_formulas(form_id, out_dir)
    overrides = _load_classifications(form_id, out_dir)
    skill_metadata = _load_skill_metadata(form_id, out_dir)
    all_ids = [n["id"] for n in nodes if isinstance(n, dict) and "id" in n]
    sections = detect_conditional_sections(all_ids)
    bare_slot_groups = detect_bare_slot_groups(all_ids)
    # Reverse map: child → parent
    child_to_parent: dict[str, str] = {}
    for parent, kids in sections.items():
        for k in kids:
            child_to_parent[k] = parent

    fields = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        fid = node.get("id")
        if not fid:
            continue
        classify = classify_field(fid)
        # Bare-slot promotion: `<prefix>_<n>` where the prefix has 2+
        # indices. e.g. distributee_1, distributee_2, distributee_3.
        if classify["category"] == "other":
            bm = SLOT_RE_BARE.match(fid)
            if bm and bm.group(1) in bare_slot_groups:
                classify = {
                    "category": "narrative_derived",
                    "subcategory": "repeating_slot",
                    "slot_group": bm.group(1),
                    "slot_index": int(bm.group(2)),
                    "slot_field": "value",
                }
        # Section-header promotion: parent → legal_choice checkbox.
        # ONLY if the auto-classify result was `other` (truly unclassified)
        # OR `legal_choice` (already known to be a choice field). For
        # `party_attr` / `case_constant` / `computed` / etc., leave the
        # parent's category alone — the prefix-sharing children are
        # incidental, not gated subordinates.
        is_legitimate_parent = (
            fid in sections
            and classify["category"] in ("other", "legal_choice")
        )
        if is_legitimate_parent:
            classify = {
                "category": "legal_choice",
                "subcategory": "section_header",
                "choice_group": fid,
            }
        # Child of section header → narrative_derived (was likely `other`)
        # with writable_when binding to parent.
        # Only treat as a true child if the parent was a legitimate section
        # header (passed the filter above).
        writable_when = None
        parent = child_to_parent.get(fid)
        if parent is not None and parent in sections:
            # Re-check whether the parent's classification qualifies it
            # as a section header (same filter as above).
            parent_classify = classify_field(parent)
            parent_is_section = parent_classify["category"] in (
                "other", "legal_choice")
            if parent_is_section:
                if classify["category"] == "other":
                    classify = {
                        "category": "narrative_derived",
                        "subcategory": "conditional_section_child",
                        "parent_section": parent,
                    }
                writable_when = {
                    "all_of": [
                        {"field": parent, "equals": True}
                    ]
                }
        data_type, data_constraints = detect_data_type(fid, classify)
        # Apply classifications.yaml override (shallow merge over classify
        # AND can override writable_when, data_type, etc.)
        ov = overrides.get(fid, {})
        extra_validators: list[str] = []
        if ov:
            classify.update({k: v for k, v in ov.items()
                             if k in ("category", "subcategory", "slot_group",
                                      "slot_index", "slot_field", "party",
                                      "parent_section", "choice_group",
                                      "choice_value")})
            if "data_type" in ov:
                data_type = ov["data_type"]
            if "data_constraints" in ov:
                data_constraints = ov["data_constraints"]
            if "writable_when" in ov:
                writable_when = ov["writable_when"]
            # `validators` from the override APPENDS to the auto-emitted
            # list (so e.g. equals_field can coexist with dedupe_within).
            if "validators" in ov and isinstance(ov["validators"], list):
                extra_validators = [str(v) for v in ov["validators"]]
        risk_input = eval_risk.get(fid, {})
        risk = score_risk(classify, risk_input)
        strategy = fill_strategy(classify)
        hand_review_reasons = _hand_review_reasons(classify, risk_input)
        formula = formulas.get(fid)
        depends_on = _depends_on(formula) if formula else []
        choice_group = classify.pop("choice_group", None) if isinstance(classify, dict) else None
        choice_value = classify.pop("choice_value", None) if isinstance(classify, dict) else None
        # formula_mode override: "exact" (default) or "at_least"
        # (used when a sum_slot formula's actual value can exceed the
        # computed sum because of addendum overflow — see PP-406).
        formula_mode = ov.get("formula_mode") if ov else None
        rec = {
            "field_id": fid,
            "widget_id": (node.get("widgets") or [None])[0],
            "label": node.get("label") or fid.replace("_", " ").title(),
            "type": node.get("type", "text"),
            "data_type": data_type,
            "data_constraints": data_constraints,
            **classify,
            "writable_when": writable_when,
            "required_when": ov.get("required_when") if ov else None,
            "choice_group": choice_group,
            "choice_value": choice_value,
            "formula": formula,
            "formula_mode": formula_mode,
            "depends_on": depends_on,
            "validators": field_validators(classify, all_ids) + extra_validators,
            "fill_strategy": strategy,
            **risk,
            "hand_review": {
                "reasons": hand_review_reasons,
            },
            "eval_evidence": risk_input or None,
        }
        fields.append(rec)

    # Source-PDF provenance — prefer the tree-pipeline-snapped artifact in
    # output_tree/<category>/<form>_tree.pdf; fall back to the older
    # output/<category>/<form>_fillable.pdf if the tree pipeline hasn't run.
    # For variant trees (e.g. AF-101.vA), strip the variant suffix and use
    # the base form's PDF, since variants are tree-only alternative metadata.
    def _find_pdf(fid: str) -> str | None:
        for pdf in pathlib.Path("output_tree").rglob(f"{fid}*_tree.pdf"):
            return str(pdf)
        for pdf in pathlib.Path("output").rglob(f"{fid}*_fillable.pdf"):
            return str(pdf)
        return None

    source_pdf = _find_pdf(form_id)
    if source_pdf is None and "." in form_id:
        # Variant form (e.g. "AF-101.vA"): share PDF with the base form
        base = form_id.split(".", 1)[0]
        source_pdf = _find_pdf(base)

    return {
        "form_id": form_id,
        "schema_version": "0.2",
        "source_pdf": source_pdf,
        "artifact_pdf": "form.pdf",
        "n_fields": len(fields),
        "by_category": _count_by(fields, "category"),
        "by_risk_tier": _count_by(fields, "risk_tier"),
        "by_data_type": _count_by(fields, "data_type"),
        "_skill_metadata_override": skill_metadata,
        "fields": fields,
    }


def _hand_review_reasons(classify: dict, eval_risk: dict) -> list[str]:
    reasons: list[str] = []
    cat = classify.get("category")
    sub = classify.get("subcategory")
    if cat == "other":
        reasons.append("unclassified — needs human triage")
    if sub == "repeating_slot":
        reasons.append("repeating_slot — high duplication risk")
    if cat == "legal_choice":
        reasons.append("legal_choice — strategic decision, human required")
    if cat == "signature":
        reasons.append("signature — wet-ink physical sign")
    if (eval_risk.get("wrong") or 0) >= 1:
        reasons.append(f"eval: {eval_risk['wrong']}/5 patterns marked WRONG")
    if (eval_risk.get("overconfident") or 0) >= 2:
        reasons.append(
            f"eval: {eval_risk['overconfident']}/5 patterns overconfident")
    if (eval_risk.get("misunderstood") or 0) >= 2:
        reasons.append(
            f"eval: {eval_risk['misunderstood']}/5 patterns misunderstood")
    return reasons


def _count_by(fields: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for f in fields:
        out[str(f.get(key)) or "unknown"] += 1
    return dict(out)


# Category-prefix defaults for skill.md frontmatter. Per-form
# classifications.yaml can override via a `skill_metadata:` block (handled
# below). The defaults are conservative — if a form's filer_role or
# statutes are wrong, fix them per-form rather than broadening here.
FORM_PREFIX_DEFAULTS = {
    "AD": {
        "filer_role": "petitioner",
        "statutes": ["18-A M.R.S.A. §§ 9-301 to 9-315 (Adoption)"],
        "service_required": True,
    },
    "AF": {
        "filer_role": "affiant",
        "statutes": [],
        "service_required": False,
    },
    "APP": {
        "filer_role": "appellant",
        "statutes": ["18-C M.R.S.A. § 1-308 (Appeals)"],
        "service_required": True,
    },
    "CN": {
        "filer_role": "consentor",
        "statutes": [],
        "service_required": False,
    },
    "DE": {
        "filer_role": "personal_representative_or_petitioner",
        "statutes": ["18-C M.R.S.A. Article 3 (Decedents' Estates)"],
        "service_required": True,
    },
    "GS": {
        "filer_role": "guardian",
        "statutes": ["18-C M.R.S.A. §§ 5-201 to 5-211 (Guardianship of Minor)"],
        "service_required": True,
    },
    "MISC": {
        "filer_role": "filer",
        "statutes": ["M.R. Prob. P."],
        "service_required": True,
    },
    "N": {
        "filer_role": "filer",
        "statutes": ["18-C M.R.S.A. (Notice provisions)"],
        "service_required": True,
    },
    "NC": {
        "filer_role": "petitioner",
        "statutes": ["18-C M.R.S.A. § 1-201 (Name Change)"],
        "service_required": True,
    },
    "PB": {
        "filer_role": "filer",
        "statutes": ["M.R. Prob. P."],
        "service_required": True,
    },
    "PP": {
        "filer_role": "petitioner",
        "statutes": ["18-C M.R.S.A. Article 5 (Protective Proceedings)"],
        "service_required": True,
    },
}


def _form_metadata_defaults(form_id: str) -> dict:
    """Return {filer_role, statutes, service_required} defaults based
    on the form ID prefix."""
    prefix = re.match(r"^([A-Z]+)", form_id)
    if not prefix:
        return {}
    return FORM_PREFIX_DEFAULTS.get(prefix.group(1), {})


def _parse_form_title(source_pdf: str) -> tuple[str, str | None]:
    """Extract (title, revision) from output_tree path like
    'output_tree/estates/DE-405 Inventory (Rev. 5-6-21)_tree.pdf'.
    Returns ('Inventory', '5-6-21') or (best-effort title, None)."""
    if not source_pdf:
        return ("TODO", None)
    name = pathlib.Path(source_pdf).stem
    name = re.sub(r"_(tree|fillable|fused|staged)$", "", name)
    # Strip leading form-id token
    m = re.match(r"^([A-Z0-9.-]+)\s+(.+?)(?:\s*\(Rev\.?\s*([\d./-]+)\))?\s*$",
                 name)
    if m:
        return (m.group(2).strip(), m.group(3))
    return (name, None)


def _failure_modes_from_eval(fields: list[dict]) -> list[tuple]:
    """Compile a list of (field_id, risk_tier, top_symptom, wrong_n)
    from eval_evidence — top 8 by risk_score."""
    rows = []
    for f in fields:
        ev = f.get("eval_evidence") or {}
        if not ev: continue
        w = ev.get("wrong", 0)
        oc = ev.get("overconfident", 0)
        mc = ev.get("misunderstood", 0)
        if not (w or oc or mc): continue
        symptom = []
        if w: symptom.append(f"wrong {w}/5")
        if oc: symptom.append(f"oc {oc}/5")
        if mc: symptom.append(f"miscompr {mc}/5")
        rows.append((f["field_id"], f.get("risk_tier"),
                     "; ".join(symptom), w))
    rows.sort(key=lambda r: -r[3])
    return rows[:8]


def write_skill_md_draft(schema: dict, path: pathlib.Path) -> None:
    """Emit a fact-dense skill.md derived from schema + eval data.

    Tries to populate as much as possible automatically:
      * form_title + revision from source_pdf filename
      * sections / slot_groups from auto-detected groupings
      * known_failure_modes table from eval_evidence
      * pipeline routing + risk distribution

    Sections still requiring human input are marked TODO."""
    if path.exists():
        return  # don't clobber hand-curated files
    fields = schema["fields"]
    form_id = schema["form_id"]
    cats = schema["by_category"]
    tiers = schema["by_risk_tier"]
    title, revision = _parse_form_title(schema.get("source_pdf") or "")
    slot_groups = sorted({f["slot_group"] for f in fields
                          if f.get("slot_group")})
    # Parties seen in party_attr classifications
    parties = sorted({f.get("party") for f in fields
                      if f.get("party")})
    # Section_header legal_choice fields → exclusive section choices
    section_headers = [f["field_id"] for f in fields
                       if f.get("subcategory") == "section_header"]
    # Other legal_choice fields → enumerable elections
    elections = [f["field_id"] for f in fields
                 if f.get("category") == "legal_choice"
                 and f.get("subcategory") != "section_header"]

    routing_rows = []
    cat_human = {
        "case_constant":     "deterministic from case_dict",
        "party_attr":        "deterministic from party record",
        "computed":          "deterministic; recompute from formula",
        "narrative_derived": "LLM over narrative + validators",
        "legal_choice":      "human decision required",
        "signature":         "wet-ink; never auto-fill",
        "external":          "left blank (filled by court/clerk)",
        "other":             "TRIAGE — unclassified",
    }
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        routing_rows.append(
            f"| {cat} | {n} | {cat_human.get(cat, '?')} |")
    routing_table = "\n".join(routing_rows)

    has_computed = "computed" in cats
    formula_section = (
        "## Computed formulas\n\nSee `formulas.yaml` for JSON-DSL "
        "expressions interpreted by `validate_filled.py`.\n"
    ) if has_computed else "## Computed formulas\n\nNone.\n"

    high_risk = [f for f in fields if f.get("risk_tier") == "red"]
    high_risk_section = ""
    if high_risk:
        rows = []
        for f in high_risk[:20]:
            reasons = "; ".join(f.get("hand_review", {}).get("reasons", []))
            rows.append(f"| `{f['field_id']}` | {f.get('risk_score')} "
                        f"| {reasons} |")
        high_risk_section = (
            "\n## High-risk fields (red tier)\n\n"
            "| field | score | reasons |\n|---|---|---|\n"
            + "\n".join(rows) + "\n"
        )

    slot_section = ""
    if slot_groups:
        rows = []
        for sg in slot_groups:
            members = [f for f in fields if f.get("slot_group") == sg]
            indices = sorted({f["slot_index"] for f in members
                              if f.get("slot_index")})
            if not indices: continue
            suffixes = sorted({f.get("slot_field") for f in members
                               if f.get("slot_field")})
            rows.append(f"| `{sg}` | {min(indices)}..{max(indices)} | "
                        f"{', '.join(suffixes)} |")
        if rows:
            slot_section = (
                "\n## Repeating slot groups\n\n"
                "| prefix | indices | suffixes |\n|---|---|---|\n"
                + "\n".join(rows) + "\n"
            )

    # Failure modes from eval evidence
    eval_rows = _failure_modes_from_eval(fields)
    failure_section = "\n## Known LLM failure modes (May-2026 eval)\n\n"
    if eval_rows:
        failure_section += "| field | tier | eval signals |\n|---|---|---|\n"
        for fid, t, sym, _ in eval_rows:
            failure_section += f"| `{fid}` | {t} | {sym} |\n"
        failure_section += (
            "\nFor each, the validator-level guard is encoded in "
            "`schema.json` `fields[].validators[]`.\n"
        )
    else:
        failure_section += (
            "_No eval evidence on file for this form. "
            "Run `scripts/run_fact_eval.sh <form_id> 5` to generate._\n"
        )

    parties_frontmatter = ""
    if parties:
        parties_frontmatter = "\nparties:\n" + "\n".join(
            f"  - {p}" for p in parties)
    sections_frontmatter = ""
    if section_headers:
        sections_frontmatter = "\nsection_headers_exclusive: true\nsection_headers:\n" + \
                               "\n".join(f"  - {s}" for s in section_headers)
    elections_frontmatter = ""
    if elections and not section_headers:
        elections_frontmatter = "\nlegal_choices:\n" + \
                                "\n".join(f"  - {e}" for e in elections)
    slot_frontmatter = ""
    if slot_groups:
        slot_frontmatter = "\nslot_groups:\n" + \
                           "\n".join(f"  - {sg}" for sg in slot_groups)

    revision_line = f'\nform_revision: "{revision}"' if revision else ""

    # Category-prefix defaults for filer_role / statutes / service_required.
    # Per-form classifications.yaml `skill_metadata:` block overrides.
    defaults = _form_metadata_defaults(form_id)
    overrides_meta = (schema.get("_skill_metadata_override") or {})
    filer_role = overrides_meta.get("filer_role",
                                    defaults.get("filer_role", "TODO"))
    statutes_list = overrides_meta.get("statutes",
                                       defaults.get("statutes", []))
    service_required = overrides_meta.get(
        "service_required",
        defaults.get("service_required", "TODO"))
    if isinstance(service_required, bool):
        service_required = "true" if service_required else "false"
    filing_deadline_days = overrides_meta.get("filing_deadline_days",
                                              defaults.get("filing_deadline_days"))
    if filing_deadline_days is None:
        filing_deadline_value = "null"
    else:
        filing_deadline_value = str(filing_deadline_days)
    filing_deadline_anchor = overrides_meta.get("filing_deadline_anchor")
    if statutes_list:
        statutes_yaml = "\n".join(f'  - "{s}"' for s in statutes_list)
        statutes_block = f"statutes:\n{statutes_yaml}"
    else:
        statutes_block = "statutes: []"

    deadline_anchor_line = (
        f'\nfiling_deadline_anchor: "{filing_deadline_anchor}"'
        if filing_deadline_anchor else "")
    content = f"""---
form_id: {form_id}
form_title: {title}{revision_line}
jurisdiction: Maine
court: Probate
filer_role: {filer_role}
{statutes_block}
filing_deadline_days: {filing_deadline_value}{deadline_anchor_line}
service_required: {service_required}
n_fields: {schema['n_fields']}
addendum_supported: true{parties_frontmatter}{sections_frontmatter}{elections_frontmatter}{slot_frontmatter}
---

## Pipeline routing

| category | n | path |
|---|---|---|
{routing_table}

{formula_section}{slot_section}{failure_section}{high_risk_section}
## Validators

Declarative tags emitted in `schema.json` `fields[].validators[]`;
interpreted by `scripts/validate_filled.py`.

- `dedupe_within(<group>_<role>)` — rejects duplicates within a slot group
- `cross_section_dedupe(...)` — rejects desc appearing across sections
- `nonempty_if_desc` — value/encumbrance must be empty when desc is empty
- `recompute_from_dependencies` — computed cells equal formula
- `populate_from_case_dict` — case_constant cells equal case_dict[field]

## Risk distribution

```
{chr(10).join(f"{t:7} {n:>3}" for t, n in tiers.items())}
```
"""
    path.write_text(content)


def write_csv(schema: dict, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow([
            "field_id", "label", "widget_id", "type", "data_type",
            "category", "subcategory", "slot_group", "slot_index",
            "risk_score", "risk_tier",
            "fill_deterministic", "fill_llm_eligible", "fill_human_required",
            "fill_source", "validators",
        ])
        for f in schema["fields"]:
            fs = f.get("fill_strategy") or {}
            w.writerow([
                f["field_id"], f.get("label", ""), f.get("widget_id", ""),
                f.get("type", ""), f.get("data_type", ""),
                f.get("category", ""), f.get("subcategory", ""),
                f.get("slot_group", ""), f.get("slot_index", ""),
                f.get("risk_score", ""), f.get("risk_tier", ""),
                "Y" if fs.get("deterministic") else "",
                "Y" if fs.get("llm_eligible") else "",
                "Y" if fs.get("human_required") else "",
                fs.get("source", "") or "",
                "; ".join(f.get("validators", [])),
            ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("form_id")
    ap.add_argument("--out-dir", type=pathlib.Path,
                    default=pathlib.Path("repo/forms"))
    args = ap.parse_args()
    schema = build_schema(args.form_id, args.out_dir)
    out_dir = args.out_dir / args.form_id
    out_dir.mkdir(parents=True, exist_ok=True)
    schema_path = out_dir / "schema.json"
    csv_path = out_dir / "fields.csv"
    skill_path = out_dir / "skill.md"
    pdf_path = out_dir / "form.pdf"
    schema_path.write_text(json.dumps(schema, indent=2, default=str))
    write_csv(schema, csv_path)
    pre_existed = skill_path.exists()
    write_skill_md_draft(schema, skill_path)
    print(f"wrote {schema_path}")
    print(f"wrote {csv_path}")
    if pre_existed:
        print(f"  (skipped {skill_path} — already exists)")
    else:
        print(f"wrote {skill_path} (DRAFT — needs hand-author)")
    # Copy the canonical source PDF into the folder.
    src = schema.get("source_pdf")
    if src and pathlib.Path(src).exists():
        shutil.copy(src, pdf_path)
        print(f"wrote {pdf_path} ({pdf_path.stat().st_size // 1024}K, "
              f"from {pathlib.Path(src).parent})")
    else:
        print(f"  (no source PDF found for {args.form_id}; "
              f"form.pdf not written)")
    print(f"\nForm {args.form_id}: {schema['n_fields']} fields")
    print(f"  by category:  {schema['by_category']}")
    print(f"  by risk_tier: {schema['by_risk_tier']}")
    print(f"  by data_type: {schema['by_data_type']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
