#!/usr/bin/env python3
"""Embed unembedded base-14 widget fonts (+ ToUnicode) — shim over the shared
``maine-forms-engine`` (``maine_forms_engine.accessibility.embed_widget_font``
— the base-14-family-generalized version this repo back-ported from the court
sibling; behavior identical, comment-only diffs at extraction).

Replaces unembedded Helvetica/Arial/Times/Courier (and styled variants) with
embedded Liberation equivalents, optionally synthesizes ZapfDingbats, and adds
ToUnicode CMaps. Font search paths honor ``WIDGET_TTF`` / ``ZAPF_TTF`` /
``LIBERATION_DIR`` env vars, as before. Run this LAST, after
accessibility_pipeline.py.

    python3 embed_widget_font.py in.pdf out.pdf [--ttf <LiberationSans.ttf>]
"""
from maine_forms_engine.accessibility.embed_widget_font import (  # noqa: F401
    LIB,
    ZAPF,
    _is_unembedded,
    _is_unembedded_base14,
    _liberation_for,
    add_tounicode,
    build_font,
    build_symbol_font,
    is_unembedded_helvetica,
    is_unembedded_symbol,
    main,
    repair_fonts,
)

if __name__ == "__main__":
    raise SystemExit(main())
