#!/usr/bin/env python3
"""Filled probate form -> tagged, PDF/UA-identified PDF — shim over the shared
``maine-forms-engine`` (``maine_forms_engine.accessibility.
accessibility_pipeline``; byte-identical to this repo's copy at extraction),
with this repo's /TU naming policy: ``schema-label`` (this repo's schema.json
labels are already human-readable accessible names).

Steps: remediate (schema-label /TU + title + /Lang + tabs) -> OpenDataLoader
content tag tree -> finalize (7.18.4 form-kids fix, CIDSet strip, PDF/UA-id
stamp) -> optional veraPDF validate. External tools come from ``ODL_PYTHON``
and ``VERAPDF`` env vars, as before.

    python3 accessibility_pipeline.py filled.pdf out.pdf \
        --schema repo/forms/<ID>/schema.json [--validate]
"""
from maine_forms_engine.accessibility import remediate_form  # noqa: F401
from maine_forms_engine.accessibility.accessibility_pipeline import (  # noqa: F401
    ODL_PYTHON,
    VERAPDF,
    _fix_form_kids,
    finalize,
    main as _pkg_main,
    tag_with_opendataloader,
    validate,
)


def main(argv=None) -> int:
    return _pkg_main(argv, default_naming="schema-label")


if __name__ == "__main__":
    raise SystemExit(main())
