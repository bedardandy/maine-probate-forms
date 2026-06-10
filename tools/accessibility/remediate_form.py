#!/usr/bin/env python3
"""Form-field accessibility remediation — shim over the shared
``maine-forms-engine`` (``maine_forms_engine.accessibility.remediate_form``),
with this repo's /TU naming policy pinned: ``schema-label`` — the schema.json
``label`` is already a human-readable accessible name, so /TU announces it
directly (the package's ``caption`` default derives names from the printed
caption text, the court sibling's strategy).

Sets each widget's accessible name (/TU), document title, /Lang, and tab
order.

    python3 remediate_form.py <filled.pdf> <out.pdf> --schema repo/forms/<ID>/schema.json
"""
from maine_forms_engine.accessibility.remediate_form import (  # noqa: F401
    main as _pkg_main,
    remediate as _pkg_remediate,
    schema_label_names,
)


def label_map(schema_path):
    """field_id -> label (+ form title) from schema.json (kept for callers)."""
    import json
    import pathlib
    sch = json.loads(pathlib.Path(schema_path).read_text())
    title = sch.get("_skill_metadata_override", {}).get("form_title") \
        or sch.get("form_id", "")
    return schema_label_names(sch), title


def remediate(inp, outp, schema_path, lang, title, *, naming="schema-label"):
    return _pkg_remediate(inp, outp, schema_path, lang, title, naming=naming)


def main(argv=None) -> int:
    return _pkg_main(argv, default_naming="schema-label")


if __name__ == "__main__":
    raise SystemExit(main())
