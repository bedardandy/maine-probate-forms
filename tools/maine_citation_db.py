#!/usr/bin/env python3
"""Maine adapter for the generic legal-citation inspector (tools/legal_inspector.py).

Builds the *closed citation vocabulary* for a probate form from the repo's trusted
index (``docs/statute-reference/_index/``) and the form's ``statutes.json``, and
resolves each citation KEY to authority text:

  * statutes / cross-refs -> verbatim text fetched live from legislature.maine.gov
    (``tools/fetch_statute_text.py``); with ``fetch_text=False`` (offline), the
    section title + the form's relevance note stand in;
  * cases -> the summarized holding from ``caselaw.json`` (full opinion text is
    not fetched).

The closed-vocabulary membership test is the same one the statute layer was
authored against (``scripts/verify_statutes.py``), so the inspector enforces
exactly the citations the forms were validated with. A well-formed cite that is
not in the vocabulary, or an in-vocabulary statute whose live text cannot be
fetched, is surfaced (invented / unresolved) — never silently passed.

Not legal advice — see :data:`DISCLAIMER`.
"""
from __future__ import annotations

import json
import pathlib
from typing import Callable

import legal_inspector
from fetch_statute_text import fetch_statute_text

ROOT = pathlib.Path(__file__).resolve().parent.parent
IDX = ROOT / "docs" / "statute-reference" / "_index"
FORMS = ROOT / "repo" / "forms"

# The repo's canonical disclaimer (kept in sync with scripts/author_statutes.py)
# so the inspector speaks with the project's one voice.
DISCLAIMER = (
    "EXPERIMENTAL — AI/LLM-GENERATED, NOT ATTORNEY-REVIEWED. This statute and "
    "case-law layer is generated and annotated by an AI model; it is for "
    "consideration only — NOT legal advice and not a substitute for a licensed "
    "Maine attorney. Statute section titles/text are quoted from "
    "legislature.maine.gov, but the SELECTION of which statutes/cases bear on a "
    "field, the relevance notes, and any case-law holdings are the model's "
    "experimental annotations — they point to issues to weigh, not conclusions, "
    "and may be wrong. Verify everything against the current statute and the "
    "actual opinions; which code applies can turn on the date of death (see "
    "transition_18a)."
)


def _index():
    sec = json.loads((IDX / "18c-sections.json").read_text(encoding="utf-8"))["sections"]
    xref = json.loads((IDX / "cross-refs.json").read_text(encoding="utf-8"))["cross_refs"]
    cases = json.loads((IDX / "caselaw.json").read_text(encoding="utf-8"))["cases"]
    return sec, xref, cases


def resolves(cite: str, sec: dict, xref: dict) -> bool:
    """Closed-vocabulary membership — lifted from scripts/verify_statutes.py."""
    if cite in xref:
        return True
    if cite.startswith("18-C §"):
        return cite[len("18-C §"):] in sec
    return False


def build_vocab(form_id: str) -> dict:
    """Closed vocabulary for ``form_id``: KEY -> {kind, cite, title/url/note/...}.

    Scoped to the citations that form's ``statutes.json`` actually uses (governing,
    per-question considerations, cross-refs, case law), so the draft generator is
    constrained to the authorities relevant to the form.
    """
    sc_path = FORMS / form_id / "statutes.json"
    if not sc_path.exists():
        raise RuntimeError(f"no statutes.json for form {form_id!r}")
    sc = json.loads(sc_path.read_text(encoding="utf-8"))
    sec, xref, cases = _index()
    case_by_cite = {c["cite"]: c for c in cases.values()}
    vocab: dict[str, dict] = {}

    def add_statute(cite, title=None, url=None, note=None):
        if not cite or cite in vocab:
            return
        if cite in xref:
            meta = xref[cite]
            vocab[cite] = {"kind": "crossref", "cite": cite,
                           "title": title or meta.get("title"),
                           "url": url or meta.get("url"),
                           "note": note or meta.get("note")}
        elif cite.startswith("18-C §") and cite[len("18-C §"):] in sec:
            meta = sec[cite[len("18-C §"):]]
            vocab[cite] = {"kind": "statute", "cite": cite,
                           "title": title or meta.get("title"),
                           "url": url or meta.get("url"), "note": note}
        # else: not in the trusted index -> intentionally not citeable.

    for g in sc.get("governing", []):
        add_statute(g.get("cite"), g.get("title"), g.get("url"), g.get("why"))
    for pq in sc.get("per_question", []):
        for c in pq.get("considerations", []):
            add_statute(c.get("cite"), c.get("title"), c.get("url"), c.get("note"))
    for x in sc.get("cross_refs", []):
        add_statute(x.get("cite"), x.get("title"), x.get("url"))
    for c in sc.get("caselaw", []):
        cite = c.get("cite")
        if cite and cite in case_by_cite and cite not in vocab:
            case = case_by_cite[cite]
            vocab[cite] = {"kind": "case", "cite": cite,
                           "title": case.get("name"), "url": case.get("url"),
                           "holding": case.get("holding"),
                           "holding_source": case.get("holding_source")}
    return vocab


def make_resolver(vocab: dict, *, fetch_text: bool = True,
                  fetch: Callable = fetch_statute_text) -> Callable[[str], dict | None]:
    """Return ``resolve(key) -> authority|None`` over ``vocab``.

    Statutes/cross-refs resolve to live verbatim text when ``fetch_text`` (a fetch
    failure returns ``None`` -> the engine records it as *unresolved*, honoring the
    "fetch full text" choice rather than quietly downgrading to a title). With
    ``fetch_text=False`` the section title + relevance note are the authority.
    Cases resolve to the summarized holding.
    """
    def resolve(key: str) -> dict | None:
        meta = vocab.get(key)
        if not meta:
            return None
        kind = meta["kind"]
        if kind in ("statute", "crossref"):
            if fetch_text:
                res = fetch(key)
                if res.get("text"):
                    return {"cite": key, "title": meta.get("title"),
                            "url": meta.get("url"), "text": res["text"],
                            "text_verified": res.get("text_verified")}
                if res.get("link_status") == "dead":        # 404/410/NXDOMAIN
                    return {"cite": key, "title": meta.get("title"),
                            "url": meta.get("url"), "text": None,
                            "dead_link": True, "link_status": "dead"}
                return None                 # Gate B: blocked/inconclusive -> unresolved
            parts = [meta.get("title") or key]
            if meta.get("note"):
                parts.append(meta["note"])
            return {"cite": key, "title": meta.get("title"), "url": meta.get("url"),
                    "text": "\n".join(parts), "text_verified": None}
        if kind == "case":
            holding = meta.get("holding")
            if not holding:
                return None
            text = f"{meta.get('title')} ({key}). Holding (summarized): {holding}"
            return {"cite": key, "title": meta.get("title"), "url": meta.get("url"),
                    "text": text, "text_verified": None}
        return None
    return resolve


def draft_prompt(form_id: str) -> str:
    """Closed-vocabulary draft-generator system prompt for ``form_id``."""
    return legal_inspector.draft_system_prompt(build_vocab(form_id))


def inspect_field(form_id: str, text: str, *, fetch_text: bool = True,
                  model: str | None = None, client=None) -> dict:
    """Inspect one composed narrative field's text for citation hallucinations."""
    import citation_scan        # local import: citation_scan imports this module
    vocab = build_vocab(form_id)
    resolver = make_resolver(vocab, fetch_text=fetch_text)
    result = legal_inspector.inspect(text, set(vocab), resolver,
                                     model=model, client=client)
    result["form_id"] = form_id
    # Deterministic safety net: flag citations written outside the [[REF:]]
    # protocol (leaked) or that don't resolve to the index (unresolvable).
    result["scan"] = citation_scan.report(text, form_id=form_id)
    result["disclaimer"] = DISCLAIMER
    return result
