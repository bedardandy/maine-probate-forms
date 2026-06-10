#!/usr/bin/env python3
"""Synthesize a minimal ZapfDingbats TrueType — shim over the shared
``maine-forms-engine`` (``maine_forms_engine.accessibility.make_zapf_ttf``;
ships verbatim there as embed_widget_font's optional fallback).

    python3 tools/accessibility/make_zapf_ttf.py /tmp/ZapfDingbats.ttf
"""
import sys

from maine_forms_engine.accessibility.make_zapf_ttf import (  # noqa: F401
    DEJAVU_CANDIDATES,
    ZAPF_TO_UNI,
    build,
    ensure,
)

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ZapfDingbats.ttf"
    print(build(out))
