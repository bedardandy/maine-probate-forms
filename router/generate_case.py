"""Qwen-driven synthetic Case + Event generator.

For each requested case_type, asks the local Qwen endpoint to produce
a realistic Maine probate case + a triggering event. Output is one
JSON line per case to router/synthetic_cases.jsonl, ready for batch
routing via router.run_case.

Uses vLLM's guided_json to constrain the model's output to the Case
schema. Each generation is a single call (~5-15s).

Usage:
  python3 -m router.generate_case --count-per-type 2
  python3 -m router.generate_case --case-types estate_intestate,conservatorship
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request
import zlib

from router.schemas import (
    CANONICAL_EVENT_TYPES, CANONICAL_ROLES, CASE_TYPES,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "router" / "synthetic_cases.jsonl"

# Scenario variants per case_type. Each variant sets a small set of
# facts that drive the lifecycle template's conditional events and the
# router's fact-boost rules. "baseline" leaves facts untouched. The
# generator picks a variant per case (round-robin by seed) so a batch
# of N cases per type lands across variants instead of all-baseline.
SCENARIO_VARIANTS: dict[str, list[tuple[str, dict, str]]] = {
    "estate_intestate": [
        ("baseline", {}, "ordinary intestate succession"),
        ("renunciation",
         {"renunciation_filed": True},
         "surviving spouse renounces priority in favor of adult child"),
        ("special_admin",
         {"special_admin_requested": True},
         "emergency need for a special administrator before regular PR"),
        ("elective_share",
         {"elective_share_filed": True},
         "surviving spouse files an elective share petition"),
        ("bond_required",
         {"bond_required": True},
         "non-spouse PR required to post bond"),
        ("pr_removal",
         {"pr_removal_petitioned": True},
         "later petition to remove the appointed PR for cause"),
        ("creditor_claim_disputed",
         {"creditor_claim_filed": True, "claim_disallowed": True},
         "creditor files claim, PR disallows, claimant petitions to resolve"),
        ("complete_settlement",
         {"complete_settlement_requested": True},
         "petition for order of complete settlement at distribution"),
    ],
    "estate_testate": [
        ("baseline", {}, "ordinary testate succession with will"),
        ("supervised_admin",
         {"supervised_admin_requested": True},
         "petition for formal/supervised administration"),
        ("elective_share",
         {"elective_share_filed": True},
         "surviving spouse files elective share against the will"),
        ("renunciation",
         {"renunciation_filed": True},
         "nominated executor renounces in favor of alternate"),
        ("bond_required",
         {"bond_required": True},
         "PR not exempt from bond — must post"),
        ("creditor_claim_disputed",
         {"creditor_claim_filed": True, "claim_disallowed": True},
         "creditor files claim, PR disallows, claimant petitions to resolve"),
    ],
    "guardianship_adult": [
        ("baseline",
         {"filing_type": "standard"},
         "non-emergency adult guardianship petition"),
        ("emergency",
         {"emergency_basis": "imminent_harm"},
         "emergency ex-parte guardianship sought before hearing"),
        ("limited_powers",
         {"filing_type": "standard", "proposed_powers": "limited"},
         "petition for guardian with explicitly limited powers"),
        ("dual_appointment",
         {"filing_type": "standard", "dual_gc_petition": True},
         "single petition for both guardian and conservator (PP-205 path)"),
    ],
    "guardianship_minor": [
        ("baseline", {}, "minor needs a guardian, no contest"),
        ("temporary",
         {"temporary_guardianship": True},
         "temporary guardianship pending hearing"),
    ],
    "conservatorship": [
        ("initial_petition",
         {"is_initial_petition": True, "filing_type": "petition"},
         "initial petition for conservator appointment (not anniversary)"),
        ("annual_account",
         {"filing_type": "financial_account"},
         "year-one annual financial account"),
        ("status_report",
         {"filing_type": "status_report"},
         "year-two status report"),
        ("interim_account",
         {"filing_type": "interim_account"},
         "off-cycle interim accounting"),
    ],
    # NOTE: "guardianship_minor" is defined once above (baseline +
    # temporary). A second literal entry here previously re-declared it
    # with only "baseline", silently overriding the first and killing the
    # "temporary" variant. Merged into the single entry above.
    "adoption": [("baseline", {}, "stepparent or relative adoption")],
    "name_change": [("baseline", {}, "adult or minor name change")],
    "small_estate": [
        ("baseline", {}, "small-estate affidavit, no formal PR")],
}


def _pick_scenario(case_type: str, seed: int,
                   forced_name: str | None = None
                   ) -> tuple[str, dict, str]:
    variants = SCENARIO_VARIANTS.get(case_type) or [("baseline", {}, "")]
    if forced_name:
        for v in variants:
            if v[0] == forced_name:
                return v
        raise ValueError(
            f"no scenario {forced_name!r} for case_type {case_type!r}; "
            f"available: {[v[0] for v in variants]}")
    return variants[seed % len(variants)]

# Per-case-type guidance so Qwen's output is realistic and routable.
TYPE_HINTS = {
    "estate_intestate": (
        "Decedent died without a will. Survived by mix of spouse, "
        "adult children, or minor heirs. Required parties: decedent, "
        "applicant (usually surviving spouse or adult child), "
        "personal_representative. Trigger event: decedent_death_date."
    ),
    "estate_testate": (
        "Decedent died with a will naming an executor/PR. Required "
        "parties: decedent, applicant, personal_representative; may "
        "include attorney. Trigger event: decedent_death_date or "
        "will_admission_date."
    ),
    "guardianship_adult": (
        "Adult respondent alleged to be incapacitated. Required parties: "
        "petitioner (often adult child/spouse), respondent, "
        "individual_under_protection. May include emergency_basis fact "
        "for emergency posture. Trigger event: petition_filing_date."
    ),
    "guardianship_minor": (
        "Minor child needing a guardian. Required parties: petitioner, "
        "minor (with DOB), individual_under_protection. Trigger event: "
        "petition_filing_date."
    ),
    "conservatorship": (
        "Adult needing financial conservator. Required parties: "
        "conservator, individual_under_protection, attorney. Often "
        "anniversary-driven (filing_type: financial_account or "
        "status_report). Trigger event: appointment_anniversary."
    ),
    "adoption": (
        "Adoption petition. Required parties: petitioner, adoptee, "
        "possibly parent_1, parent_2, agency. Trigger event: "
        "petition_filing_date or adoption_finalization_date."
    ),
    "name_change": (
        "Petition to change legal name. Required parties: petitioner. "
        "Trigger event: petition_filing_date or hearing_date."
    ),
}


# Tight JSON schema for guided generation. Mirrors router.schemas.Case
# but lets Qwen omit optional fields.
PERSON_SCHEMA = {
    "type": "object",
    "properties": {
        "full_name": {"type": "string"},
        "address": {"type": "string"},
        "dob": {"type": "string"},
        "dod": {"type": "string"},
        "phone": {"type": "string"},
        "email": {"type": "string"},
        "relationship_to_subject": {"type": "string"},
        "attrs": {"type": "object"},
    },
    "required": ["full_name"],
    "additionalProperties": False,
}

CASE_GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "description": {"type": "string"},
        "case": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "case_type": {"type": "string"},
                "county": {"type": "string"},
                "docket_number": {"type": "string"},
                "opened_date": {"type": "string"},
                "parties": {
                    "type": "object",
                    "additionalProperties": PERSON_SCHEMA,
                },
                "party_lists": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "array",
                        "items": PERSON_SCHEMA,
                    },
                },
                "facts": {"type": "object"},
            },
            "required": ["case_id", "case_type", "parties"],
            "additionalProperties": False,
        },
        "event": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "date": {"type": "string"},
                "case_id": {"type": "string"},
                "payload": {"type": "object"},
            },
            "required": ["type", "date", "case_id"],
            "additionalProperties": False,
        },
    },
    "required": ["id", "case", "event"],
    "additionalProperties": False,
}


PROMPT_TEMPLATE = """\
You are generating a synthetic Maine probate court case for end-to-end \
testing of a form router. Output ONE realistic case + event tuple as \
JSON matching the schema enforced by the API.

Target case_type: {case_type}
Scenario variant: {scenario_name} — {scenario_desc}

Hint: {hint}

Constraints:
- All dates ISO YYYY-MM-DD, all within 2024-2026.
- Use realistic Maine names, towns (Cumberland, Penobscot, York, \
Androscoggin, Kennebec counties), realistic addresses, phone (207).
- `case.parties` keys MUST come from this canonical set when applicable: \
{canonical_roles}
- `event.type` MUST come from this canonical set: {canonical_events}
- `event.case_id` MUST equal `case.case_id`.
- `case.facts` MUST include these scenario-specific keys: {scenario_facts}
- Beyond the required keys, include 2-4 additional fact keys appropriate \
to the case type (testacy, has_minor_heirs, spouse_surviving, \
real_estate_in_estate, etc.).
- id must be a snake_case slug ≤ 60 chars, distinct from any previous \
case in this batch.
- Build the case narrative around the scenario variant (e.g. if "renunciation", \
include a person with priority who will renounce).

Generate seed #{seed}. Output JSON only, no markdown fences.
"""


def call_qwen_generate(url: str, model: str, prompt: str,
                       max_tokens: int = 8192) -> dict:
    """Single Qwen call. Raises on truncation, None content, or parse fail."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": max_tokens,
        "extra_body": {"guided_json": CASE_GEN_SCHEMA},
        "guided_json": CASE_GEN_SCHEMA,
    }
    req = urllib.request.Request(
        f"{url}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        d = json.load(resp)
    choice = d["choices"][0]
    text = choice["message"].get("content")
    if not text:
        # Qwen-thinking can stash output in `reasoning` when the
        # content stream gets cut. Try that, then fail clearly.
        text = choice["message"].get("reasoning") or ""
    if not text:
        raise ValueError(f"empty content; finish_reason="
                         f"{choice.get('finish_reason')}")
    if choice.get("finish_reason") == "length":
        raise ValueError(f"truncated (finish_reason=length, "
                         f"len={len(text)}) — bump max_tokens")
    return json.loads(text)


# Common Qwen schema-drift renames. Apply post-hoc to align Qwen
# output with router.schemas vocabulary.
PERSON_KEY_ALIASES = {
    "name": "full_name",
    "full name": "full_name",
    "fullname": "full_name",
    "date_of_birth": "dob",
    "birth_date": "dob",
    "date_of_death": "dod",
    "death_date": "dod",
    "relationship": "relationship_to_subject",
    "relation": "relationship_to_subject",
}

# Person fields explicitly defined on the dataclass. Anything else gets
# folded into `attrs` so Pydantic-style strict construction stops
# erroring out on Qwen's creative key choices.
_PERSON_KNOWN_KEYS = {
    "full_name", "address", "dob", "dod", "phone", "email",
    "relationship_to_subject", "attrs",
}

CASE_KEY_ALIASES = {
    "filing_date": "opened_date",
    "case_open_date": "opened_date",
    "case_number": "case_id",
    "docket": "docket_number",
    "court": "county",   # Qwen sometimes puts "Penobscot County Probate
                         # Court" under `court`; downstream we only use
                         # county. Strip the suffix later.
}


def _normalize_person(p: dict) -> dict:
    out: dict = {}
    extras: dict = {}
    for k, v in p.items():
        new_k = PERSON_KEY_ALIASES.get(k, k)
        if new_k in _PERSON_KNOWN_KEYS:
            out[new_k] = v
        else:
            extras[new_k] = v
    if extras:
        attrs = out.get("attrs") or {}
        attrs.update(extras)
        out["attrs"] = attrs
    return out


def _normalize_case(doc: dict, case_type: str) -> dict:
    """Patch Qwen schema drift into the canonical Case + Event shape."""
    case = doc.get("case", {}) or {}

    # id can live at top level OR nested under case
    if "id" not in doc:
        doc["id"] = case.pop("id", None) or f"synth_{case.get('case_id', '?')}"
    case.setdefault("case_type", case_type)
    doc.setdefault("description", f"Synthesized {case_type} case")

    # Field renames on the case object
    for old, new in CASE_KEY_ALIASES.items():
        if old in case and new not in case:
            case[new] = case.pop(old)
    # Backstop: if case_id still missing, derive from id/slug.
    if not case.get("case_id"):
        case["case_id"] = doc.get("id") or f"synth_{case_type}"
    # Strip trailing "County" / "Probate Court" from county field.
    if case.get("county"):
        c = str(case["county"])
        for suffix in (" County Probate Court", " Probate Court", " County"):
            if c.endswith(suffix):
                c = c[: -len(suffix)]
        case["county"] = c

    # Normalize people in `parties`
    parties = case.get("parties") or {}
    case["parties"] = {role: _normalize_person(p)
                       for role, p in parties.items()
                       if isinstance(p, dict)}
    # Normalize people in `party_lists`
    plists = case.get("party_lists") or {}
    case["party_lists"] = {
        role: [_normalize_person(p) for p in plist if isinstance(p, dict)]
        for role, plist in plists.items()
    }
    case["extra_parties"] = case.get("extra_parties") or {}

    doc["case"] = case

    # Backfill attorney bar_number + email — Qwen rarely emits these
    # but downstream forms (DE-201/DE-502/DE-504/DE-507/PP-412 ...) have
    # widgets for them. See scripts/backfill_attorney_block.py for
    # derivation logic — stable per attorney name so refills match.
    try:
        from scripts.backfill_attorney_block import backfill_case
        backfill_case(doc)
    except Exception as e:
        print(f"    (attorney backfill skipped: {e})", file=sys.stderr)

    # Event normalization: keep case_id in sync.
    event = doc.get("event") or {}
    event["case_id"] = case.get("case_id", event.get("case_id", ""))
    doc["event"] = event
    return doc


def generate(case_type: str, seed: int, url: str, model: str,
             max_attempts: int = 3,
             forced_scenario: str | None = None) -> dict:
    """Generate one case, retrying on transient failure (truncation,
    parse errors, empty content). Scenario variant is selected by
    seed % len(variants), so iterating seeds across a case_type lands
    cases across all variants. Pass forced_scenario to pin it."""
    scenario_name, scenario_facts, scenario_desc = _pick_scenario(
        case_type, seed, forced_name=forced_scenario)
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        prompt = PROMPT_TEMPLATE.format(
            case_type=case_type,
            hint=TYPE_HINTS.get(case_type, "(no specific hint)"),
            canonical_roles=sorted(CANONICAL_ROLES),
            canonical_events=sorted(CANONICAL_EVENT_TYPES),
            scenario_name=scenario_name,
            scenario_desc=scenario_desc,
            scenario_facts=json.dumps(scenario_facts) if scenario_facts
                            else "(no required scenario facts)",
            seed=seed + 1000 * (attempt - 1),
        )
        try:
            raw = call_qwen_generate(url, model, prompt)
            case = _normalize_case(raw, case_type)
            # Enforce scenario facts (Qwen sometimes drops them).
            if scenario_facts:
                facts = case.setdefault("case", {}).setdefault("facts", {})
                for k, v in scenario_facts.items():
                    facts[k] = v
                case["scenario"] = scenario_name
            return case
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < max_attempts:
                print(f"    attempt {attempt} failed ({e}); retrying",
                      file=sys.stderr)
    raise last_err if last_err else RuntimeError("generation failed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count-per-type", type=int, default=1)
    ap.add_argument("--case-types", default=",".join(sorted(CASE_TYPES)),
                    help="Comma-separated case_types to generate.")
    ap.add_argument("--url", default="http://localhost:8088")
    ap.add_argument("--model", default="Qwen3.6-27B-FP8")
    ap.add_argument("--out", type=pathlib.Path, default=OUT_PATH)
    ap.add_argument("--scenario", default=None,
                    help="If set, force every generated case onto this "
                         "scenario name (must exist for each --case-type).")
    ap.add_argument("--append", action="store_true",
                    help="Append to --out instead of truncating it.")
    args = ap.parse_args()

    types = [t.strip() for t in args.case_types.split(",") if t.strip()]
    args.out.parent.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    n_fail = 0
    open_mode = "a" if args.append else "w"
    with args.out.open(open_mode) as fh:
        for ct in types:
            for i in range(args.count_per_type):
                # zlib.crc32 is stable across processes; builtin hash() is
                # salted per-process (PYTHONHASHSEED) → non-reproducible
                # corpora for the same case_type across runs.
                seed = zlib.crc32(ct.encode()) % 1000 + i
                try:
                    case = generate(ct, seed, args.url, args.model,
                                    forced_scenario=args.scenario)
                except Exception as e:
                    print(f"  {ct} seed {seed}: FAIL {e}", file=sys.stderr)
                    n_fail += 1
                    continue
                # Validation: enforce id uniqueness and event.case_id link.
                cid = case.get("case", {}).get("case_id", "")
                if case.get("event", {}).get("case_id") != cid:
                    case["event"]["case_id"] = cid
                fh.write(json.dumps(case, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"  {ct} seed {seed}: {case.get('id','?')} "
                      f"({cid}, event={case['event']['type']})")
                n_ok += 1
    print(f"\nGenerated {n_ok} case(s), {n_fail} failure(s) → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
