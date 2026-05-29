"""End-to-end radio test on PB-007 without calling the VLM.

Loads the existing validation JSON for PB-007, manually marks the three
appointment-type checkboxes (Limited-Purpose / Standard / Expanded) as a
radio group, then runs taxonomy + acroform_writer. Inspects the resulting
fillable PDF to confirm a real PDF RadioGroup with three kids sharing a
field_name and each carrying a unique on-state.

This validates the schema/validator/writer integration on a real form
geometry without requiring a live VLM endpoint.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

import config  # noqa: E402
from modules import acroform_writer, taxonomy  # noqa: E402
from modules.schema import (  # noqa: E402
    GroupRole,
    ValidationResult,
)


PB007_FORM_ID = "PB-007"
PB007_PDF = ROOT / "forms/guardian_minor/PB-007 GAL Joint Appt. Order 3.4.20.pdf"

APPOINTMENT_TYPE_LABELS = {
    "limited-purpose": "limited_purpose",
    "limited purpose": "limited_purpose",
    "standard": "standard",
    "expanded": "expanded",
}

# Hard-coded geometry of the appointment-type triple on page 0.
# Derived from text-span analysis: each gap between bold/italic label
# spans is the actual checkbox slot. Vector squares aren't drawn (the
# detector picked up the wrong y elsewhere on the page), so we patch the
# rect at write-time so the rendered radio sits on the visible square.
APPOINTMENT_TYPE_RECTS = {
    "limited_purpose": (314.0, 184.0, 324.0, 194.0),
    "standard":        (406.0, 184.0, 416.0, 194.0),
    "expanded":        (463.0, 184.0, 473.0, 194.0),
}


def patch_validation_json() -> ValidationResult:
    raw = json.loads((config.VALIDATION_DIR / f"{PB007_FORM_ID}.json").read_text())
    patched = 0
    for f in raw.get("fields", []):
        label = (f.get("nearby_label") or "").strip().lower()
        option = APPOINTMENT_TYPE_LABELS.get(label)
        if option is None:
            continue
        f["field_type"] = "radio"
        f["group_id"] = "appointment_type"
        f["group_role"] = "radio"
        f["group_option"] = option
        # Override the detector's wrong y with the correct geometry (the
        # detector missed because PB-007 draws boxes as stroked-line paths,
        # not /re rects).
        x0, y0, x1, y1 = APPOINTMENT_TYPE_RECTS[option]
        f["rect"] = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
        patched += 1
    print(f"Patched {patched} fields into appointment_type radio group")
    return ValidationResult.model_validate(raw)


def main() -> int:
    val = patch_validation_json()
    naming = taxonomy.name_fields(val)
    radio_kids = [f for f in naming.fields
                  if f.group_role == GroupRole.RADIO and f.group_id]
    print(f"Naming has {len(naming.fields)} fields total; "
          f"{len(radio_kids)} radio kids in {len({f.group_id for f in radio_kids})} groups")
    for f in radio_kids:
        print(f"  group={f.group_id!r} option={f.group_option!r} name={f.field_name!r}")

    out = acroform_writer.write_form(naming, str(PB007_PDF))
    print(f"\nWrote: {out.output_path} ({out.field_count} widgets)")

    # Inspect the actual PDF radios
    d = fitz.open(out.output_path)
    page = d[0]
    print("\nRadio widgets in output:")
    radio_summaries = []
    for w in (page.widgets() or []):
        if w.field_type_string == "RadioButton":
            summary = {
                "name": w.field_name,
                "on_state": w.on_state(),
                "states": w.button_states()["normal"],
                "rect": [round(c, 1) for c in w.rect],
            }
            radio_summaries.append(summary)
            print(f"  name={summary['name']!r:<24} on_state={summary['on_state']!r:<22} "
                  f"states={summary['states']} rect={summary['rect']}")
    d.close()

    if not radio_summaries:
        print("\nFAIL: no radio widgets in output", file=sys.stderr)
        return 1
    names = {r["name"] for r in radio_summaries}
    on_states = {r["on_state"] for r in radio_summaries}
    if len(names) != 1:
        print(f"\nFAIL: radios should share field_name, got {names}", file=sys.stderr)
        return 1
    if len(on_states) != len(radio_summaries):
        print(f"\nFAIL: each radio needs a unique on_state, got {on_states}",
              file=sys.stderr)
        return 1
    print(f"\nOK: {len(radio_summaries)} radio kids share field_name {names.pop()!r}; "
          f"{len(on_states)} unique on_states.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
