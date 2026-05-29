"""Render a Case + Event into a fact_patterns.yaml-style narrative.

The fill pipeline (scripts/fill_form.py) consumes a free-text narrative
and pattern-matches against the form schema. Rather than reengineer
the pipeline to accept structured input, we render the structured
Case into a narrative paragraph and feed the existing pipeline.

The narrative is deterministic and reads like a first-person intake
note. Person roles, party_lists, and case.facts are surfaced as
explicit sentences so the LLM has high-confidence anchors for each
schema field.

Output: a fact_patterns.yaml-compatible dict with one `patterns` entry.
"""
from __future__ import annotations

import textwrap
import yaml

from router.schemas import Case, Event, Person


# ── per-role narrative phrasing ───────────────────────────────────────────

def _person_blurb(role: str, p: Person) -> str:
    bits = [p.full_name]
    if p.dob:
        bits.append(f"DOB {p.dob}")
    if p.dod:
        bits.append(f"died {p.dod}")
    if p.address:
        bits.append(f"at {p.address}")
    if p.phone:
        bits.append(f"phone {p.phone}")
    if p.email:
        bits.append(f"email {p.email}")
    if p.relationship_to_subject:
        bits.append(f"({p.relationship_to_subject.replace('_', ' ')})")
    for k, v in (p.attrs or {}).items():
        bits.append(f"{k.replace('_', ' ')} {v}")
    return f"{role.replace('_', ' ').title()}: " + ", ".join(bits) + "."


def _fact_blurb(key: str, value) -> str:
    label = key.replace("_", " ")
    if isinstance(value, bool):
        return f"{label.capitalize()}: {'yes' if value else 'no'}."
    return f"{label.capitalize()}: {value}."


def _event_blurb(event: Event) -> str:
    label = event.type.replace("_", " ")
    if event.payload:
        extras = "; ".join(f"{k}={v}" for k, v in event.payload.items())
        return f"Triggering event: {label} on {event.date} ({extras})."
    return f"Triggering event: {label} on {event.date}."


# ── main entry ────────────────────────────────────────────────────────────

def render_narrative(case: Case, event: Event) -> str:
    """Produce a single multi-line narrative for fill_form.py."""
    lines: list[str] = []

    # Case header
    header = f"Case {case.case_id} ({case.case_type.replace('_', ' ')})"
    if case.county:
        header += f" in {case.county} County"
    if case.docket_number:
        header += f", docket {case.docket_number}"
    if case.opened_date:
        header += f", opened {case.opened_date}"
    lines.append(header + ".")

    lines.append(_event_blurb(event))

    # Tell the model: any signature/date line on the form should carry the
    # event date. Models trained to avoid confabulation otherwise leave
    # signature_date / date_signed / dated fields blank because the narrative
    # doesn't explicitly tag them — vision audit caught this on DE-503.
    lines.append(
        f"Form completion date: this form is being signed and dated on "
        f"{event.date} — use this date for any signature date, date signed, "
        f"or dated field on the form (claimant/applicant/petitioner/PR/"
        f"attorney signature date all refer to this date unless the "
        f"narrative explicitly states a different date).")

    # Singular-role parties
    for role, person in case.parties.items():
        lines.append(_person_blurb(role, person))
    for role, person in case.extra_parties.items():
        lines.append(_person_blurb(role, person))

    # Plural-role parties — render as itemized list inside one paragraph
    for role, plist in case.party_lists.items():
        if not plist:
            continue
        items = []
        for i, p in enumerate(plist, 1):
            sub = [p.full_name]
            if p.relationship_to_subject:
                sub.append(f"({p.relationship_to_subject.replace('_', ' ')})")
            if p.dob:
                sub.append(f"DOB {p.dob}")
            if p.address:
                sub.append(f"at {p.address}")
            items.append(f"({i}) " + " ".join(sub))
        lines.append(
            f"{role.replace('_', ' ').title()} ({len(plist)}): "
            + "; ".join(items) + ".")

    # Case-level facts
    for k, v in case.facts.items():
        lines.append(_fact_blurb(k, v))

    return "\n".join(lines)


def render_patterns_yaml(case: Case, event: Event,
                         pattern_id: int = 1,
                         complexity: str = "complete") -> dict:
    narrative = render_narrative(case, event)
    return {
        "patterns": [{
            "id": pattern_id,
            "title": f"Synthesized from {case.case_id} / {event.type}",
            "complexity": complexity,
            "narrative": narrative,
        }],
    }


# ── CLI ───────────────────────────────────────────────────────────────────

def _cli():
    import argparse
    import pathlib
    from router.schemas import from_dict_case, from_dict_event

    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=pathlib.Path,
                    default=pathlib.Path(__file__).parent / "seed_cases.yaml")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--out", type=pathlib.Path,
                    help="Write fact_patterns.yaml here.")
    args = ap.parse_args()

    seed = yaml.safe_load(args.seed.read_text())
    matches = [c for c in seed.get("cases", []) if c.get("id") == args.case_id]
    if not matches:
        raise SystemExit(f"no seed case {args.case_id}")
    c = matches[0]
    case = from_dict_case(c["case"])
    event = from_dict_event(c["event"])

    doc = render_patterns_yaml(case, event)
    out = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    if args.out:
        args.out.write_text(out)
        print(f"wrote {args.out}")
    else:
        print(out)


if __name__ == "__main__":
    _cli()
