"""DE-505 (Petition with Respect to Pretermitted or Omitted Child).

Fills 4 narrative items + 2 checkbox groups that LLM leaves blank
because the case generator doesn't emit omitted-child specifics.

Item 1 (omitted_child_info): child's full name + address + email
Item 2 (circumstances_existed): circumstances at Will execution
Item 3 (omission_intent + basis_facts): intentional vs unintentional + basis
Item 4 (intestate_share_prayer): should / should not receive intestate share

All seeded deterministically per case_id. Form-gated to DE-505.
AFTER infer_attorney_bar, BEFORE recompute_overwrite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys


CASES_PATH_DEFAULT = pathlib.Path("router/synthetic_cases.jsonl")


def _lookup_case(case_id: str, cases_path: pathlib.Path) -> dict | None:
    if not cases_path.exists():
        return None
    for line in cases_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = d.get("case_id") or d.get("case", {}).get("case_id")
        if cid == case_id:
            return d.get("case") or d
    return None


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
            {"to": value, "method": f"de505-{source}"})
    else:
        answers[fid] = value
    return True


CHILD_NAMES = (
    "Jonathan Robert Caldwell",
    "Emily Rose Thibodeault",
    "Marcus James Bouchard",
    "Sarah Lynn Pelletier",
)

CITIES = (
    ("Portland", "ME", "04101"),
    ("Bangor", "ME", "04401"),
    ("Augusta", "ME", "04330"),
    ("Lewiston", "ME", "04240"),
)

CIRCUMSTANCES_TEMPLATES = (
    "At the time of Will execution, the decedent and the omitted child's "
    "other parent had been separated for several years and the decedent had "
    "no contact with the child; the decedent was unaware of the child's "
    "circumstances.",
    "At the time the Will was executed, decedent reasonably believed "
    "the omitted child was deceased and so did not include the child in "
    "the testamentary dispositions.",
    "The omitted child was born after the Will was executed. Decedent "
    "did not survive long enough to revise the Will to include the after-born child.",
)

BASIS_TEMPLATES = (
    "Will execution predated the child's birth; no codicil was prepared.",
    "Decedent had explicitly stated intent to provide for omitted child "
    "through non-probate means (life insurance / payable-on-death account) "
    "but failed to update those designations.",
    "The Will's residuary clause names the surviving spouse only; "
    "decedent's correspondence indicates the omission of children was "
    "an oversight, not intentional disinheritance.",
)


def _seeded_pick(case_id: str, options: tuple, salt: int = 0) -> object:
    h = hashlib.sha256(f"{case_id}|de505|{salt}".encode()).digest()
    return options[h[0] % len(options)]


def process(filled: dict, case_id: str | None = None,
            cases_path: pathlib.Path | None = None
            ) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "DE-505":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    case = (_lookup_case(case_id, cases_path or CASES_PATH_DEFAULT)
            if case_id else None) or {}

    # Item 1: Child's full name, current address, email
    if not _get(answers, "omitted_child_info"):
        name = _seeded_pick(case_id or "", CHILD_NAMES, 0)
        city, state, zip_ = _seeded_pick(case_id or "", CITIES, 1)
        first = name.split()[0].lower()
        last = name.split()[-1].lower()
        email = f"{first}.{last}@example.com"
        addr = f"{name}, 14 Maple Street, {city}, {state} {zip_}, {email}"
        if _set(answers, "omitted_child_info", addr, "seeded-child-info"):
            changes.append(("omitted_child_info", addr, "seeded-child-info"))

    # Item 2: Circumstances
    if not _get(answers, "circumstances_existed"):
        v = _seeded_pick(case_id or "", CIRCUMSTANCES_TEMPLATES, 2)
        if _set(answers, "circumstances_existed", v, "seeded-circumstances"):
            changes.append(("circumstances_existed", v, "seeded-circumstances"))

    # Item 3: Omission intent — schema has a single `omission_intent`
    # field that form_filler expands to __intentional/__unintentional
    # checkboxes based on its value.
    if not _get(answers, "omission_intent"):
        if _set(answers, "omission_intent", "unintentional",
                "default-unintentional"):
            changes.append(("omission_intent", "unintentional",
                            "default-unintentional"))
    if not _get(answers, "basis_facts"):
        v = _seeded_pick(case_id or "", BASIS_TEMPLATES, 3)
        if _set(answers, "basis_facts", v, "seeded-basis"):
            changes.append(("basis_facts", v, "seeded-basis"))

    # Item 4: Should/should_not — schema has single `intestate_share_prayer`
    if not _get(answers, "intestate_share_prayer"):
        if _set(answers, "intestate_share_prayer", "should",
                "default-should-receive"):
            changes.append(("intestate_share_prayer", "should",
                            "default-should-receive"))

    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--cases-path", type=pathlib.Path,
                    default=CASES_PATH_DEFAULT)
    args = ap.parse_args()

    case_id = args.case_id or args.filled.parent.name
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, case_id, args.cases_path)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_de505_omitted_child: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
