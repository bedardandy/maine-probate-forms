"""LLM-driven flowchart/tree extraction from a form digest.

Input: a digest produced by build_form_digest.py (text-format) plus the
fused PDF whose widget IDs the digest cites.

Output: a YAML tree that captures the form's logical structure:
  * select_one nodes (radio groups) with mutually-exclusive options
  * select_many nodes (independent checkboxes that share a stem)
  * text/date/currency nodes (free-form fillable fields)
  * enabler relationships (parent checkbox gating a sub-question)
  * branch logic (if option X is chosen, jump past sibling questions)

The tree is then human-reviewable BEFORE any PDF is regenerated. Bad VLM
output gets caught at YAML-review time, not at fillability-test time.

Calls the local Qwen3.6-27B-FP8 vLLM by default (matches existing pipeline);
override with --endpoint / --model. The model gets:
  * The digest as-is (text + widget IDs in reading order)
  * A schema spec it must follow

Validation after parse:
  * Every widget ID in the tree must exist in the digest (no hallucinated IDs)
  * Every widget ID in the digest must appear ≤1 time in the tree (no double-binding)
  * select_one option count must be ≥2; select_many ≥2
  * `when:` references must point to earlier nodes
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

import yaml
from openai import OpenAI


SYSTEM_PROMPT = """You are a legal-form structure analyst. You convert a flat digest of a fillable PDF form into a structured tree that captures the form's logical flow.

The digest interleaves text spans and widget tokens like:
  [W007 TXT @p1 x=54 y=182]   text input field
  [W008 RAD @p1 x=315 y=182]  radio-style checkbox
  [W024 CHK @p1 x=73 y=468]   independent checkbox

The digest also marks **layout structure** explicitly:
  [BAND 2 — 2 columns]
    COL-A:
      PROBATE COURT
      [W001 TXT @p1 x=89 y=92]
      County:
      Docket No. [W003 TXT @p1 x=105 y=106]
    COL-B:
      DISTRICT COURT
      [W002 TXT @p1 x=423 y=92]
      Location:
      Docket No. [W004 TXT @p1 x=432 y=106]
  [/BAND]

A multi-column band almost always represents a **logical alternative** — the user fills ONE column, leaves the other(s) blank. Examples: probate vs. district caption, petitioner vs. respondent signature blocks, parallel "if minor / if adult" sections.

You produce a YAML document with this schema:

```yaml
form_id: <string>
nodes:
  - id: <snake_case unique identifier, semantically meaningful>
    type: select_one | select_many | text | date | currency | enabler
    prompt: <plain-English question, ~10 words>
    explanation: <optional one-line clarifying note>
    when: <optional boolean expression over earlier node ids>  # gating
    virtual: true                                              # see rule 11
    widgets: [W001, W014, ...]                                 # for text/date/currency
    options:                                                   # for select_one/select_many
      - {value: <snake_case>, widget: W008, label: "Limited-Purpose"}
      - {value: <snake_case>, widget: W009, label: "Standard"}
```

Rules:

1. **select_one** = a radio group. Options are mutually exclusive. Use when the form makes peers exclusive — explicit cues ("choose A, B, or C", "or" between options) OR layout cues (parallel columns in a multi-column band).

2. **select_many** = independent checkboxes that share a question stem but are NOT mutually exclusive (e.g. "Petitioner / Respondent / Other objects" — multiple parties can object simultaneously).

3. **text / date / currency** = fillable text. List the widget IDs that fill the same logical answer (multi-line fields get multiple widgets).

4. **enabler** = a single checkbox whose semantics is "the following question applies". Followed by other nodes with `when: <enabler_id> == true`.

5. **`when:`** controls branching. Examples:
   - `when: appointment_type == 'limited_purpose'`  — only relevant under one option
   - `when: any_party_objects == 'objects'`         — when a select_one took a particular value
   - `when: petitioner_pays == true`                — when an enabler is checked

6. **Each widget appears in exactly ONE node.** If a widget seems to belong to two questions, you have misread the form — re-examine the layout.

7. **Don't invent widgets or options.** Only cite widget IDs that appear in the digest. Don't add a "no objection" or "none" option unless an actual checkbox in the digest says so.

8. **Skip decorative/filler widgets.** If a TXT widget is clearly a long underscore underline that has no associated question (just visual filler at top of page), omit it.

9. **Section headings** (numbered like "4. TYPE OF APPOINTMENT") become `id` prefixes for clarity but aren't themselves nodes.

10. Preserve **reading order** — nodes should appear in the order a human would fill the form.

11. **Implicit mutex columns** — when a `[BAND — N columns]` represents a "fill ONE column" choice, model it as a `virtual: true` select_one whose options name the columns but have NO `widget` field. Then gate each column's text fields with `when: <column_choice> == '<value>'`. Example for the probate/district caption:

    ```yaml
    - id: court_type
      type: select_one
      virtual: true
      prompt: "Which court is this case in?"
      options:
        - {value: probate}
        - {value: district}

    - id: county_probate
      type: text
      when: court_type == 'probate'
      widgets: [W001]

    - id: district_court_location
      type: text
      when: court_type == 'district'
      widgets: [W002]
    ```

    `virtual: true` means "no widget for the choice — the user signals it implicitly by which column they fill." Use this for any multi-column band whose columns are alternatives, not peers.

12. **Same answer in multiple places** — a form sometimes marks the same logical choice in TWO locations (e.g. a top-of-page summary row AND a Section 4 letter-marker A/B/C for the same appointment type). Use plural `widgets: [...]` on a select_one option to bind one logical value to multiple widgets:

    ```yaml
    - id: appointment_level
      type: select_one
      prompt: "Type of GAL appointment"
      options:
        - {value: limited_purpose, widgets: [W008, W039], label: "Limited-Purpose / A"}
        - {value: standard,        widgets: [W009, W045], label: "Standard / B"}
        - {value: expanded,        widgets: [W010, W048], label: "Expanded / C"}
    ```

    The writer creates a single radio group whose kids are all listed widgets, so checking any one mirror checks the others. ONLY use this when the two visual locations represent the same answer (verify by reading the surrounding text). If they are independent questions, keep them as separate select_one nodes.

13. **OR-with-multi-select pattern** — a very common form structure is:

        [W024] No party objects to the X; or
        [W025] Petitioner [W026] Respondent [W027] Other (____) objects to X

    There is ONE checkbox on the "no party objects" side and a row of independent checkboxes on the "some party objects" side. The user picks ONE of two branches: either the "no objection" box, or one-or-more of the party boxes. The alternative branch HAS NO checkbox of its own — it's signaled by any of the party boxes being checked.

    Model this as a select_one with ONE real option and ONE virtual option, then a select_many gated by the virtual option:

    ```yaml
    - id: objection_status
      type: select_one
      prompt: "Does any party object?"
      options:
        - {value: none, widget: W024, label: "No party objects"}
        - {value: objects, virtual: true, label: "Some party objects"}

    - id: objecting_parties
      type: select_many
      when: objection_status == 'objects'
      prompt: "Which parties object?"
      options:
        - {value: petitioner, widget: W025, label: "Petitioner"}
        - {value: respondent, widget: W026, label: "Respondent"}
        - {value: other, widget: W027, label: "Other"}
    ```

    Critical: do NOT bind W025 (or any party checkbox) as the second option of `objection_status`. Each widget belongs to exactly one node. The virtual option has no widget.

14. **Independent text fields are separate `text` nodes — NOT options of a select_one.**

    Options are mutually-exclusive answers to ONE question, typically marked by checkboxes (CHK / RAD widgets). If a section has parallel text fields with different labels (Name / Address / Phone / Email / Date / Signature), each is its OWN `text` node. Do NOT cram them into one select_one. The check: real select_one options correspond to checkbox widgets the user could click. If the widgets are TXT only and the labels are descriptors (not "or"-separated alternatives), they are separate nodes.

    WRONG:
    ```yaml
    - id: contact_info
      type: select_one
      options:
        - {value: address, widget: W016}
        - {value: phone,   widget: W018}
        - {value: email,   widget: W019}
    ```

    RIGHT:
    ```yaml
    - id: address
      type: text
      widgets: [W016, W017]
    - id: phone
      type: text
      widgets: [W018]
    - id: email
      type: text
      widgets: [W019]
    ```

15. **Header-led alternative columns even without [BAND] markup.** Some forms place column alternatives in flowing text rather than visually-banded blocks (e.g. "PROBATE COURT     DISTRICT COURT" with County/Location side-by-side underneath). Even though the digest may not mark this as a [BAND], if you see two parallel column headings naming alternatives followed by parallel field rows, apply Rule 11 — emit a `virtual: true` select_one for the choice and gate each column's fields with `when:`.

16. **"Check all that apply" lists are select_many, not many select_ones.** A common pattern is a stem like "(check all that apply):" or "I have done the following:" followed by 3-8 independent checkboxes, one per line. Model the WHOLE list as ONE `select_many` node with one option per checkbox. Do NOT emit one `select_one` per checkbox — those would each need a synthetic "false" option, and the boxes are not mutually exclusive anyway.

    ```yaml
    - id: jurisdictional_inquiries
      type: select_many
      prompt: "Which inquiries did you make?"
      options:
        - {value: asked_first_parent,    widget: W018, label: "Asked first parent"}
        - {value: asked_guardian,        widget: W019, label: "Asked guardian"}
        - {value: asked_second_parent,   widget: W020, label: "Asked second parent"}
        - {value: checked_probate_clerk, widget: W021, label: "Checked probate court"}
    ```

17. **Standalone yes/no checkboxes are `enabler` nodes.** A single checkbox without a paired alternative ("If X applies, check this box and fill below") is a binary on/off toggle. Model it as `enabler`, not as a `select_one` with options like `{value: true}`/`{value: false}`. The `enabler` type is a single-checkbox node whose downstream fields can use `when: <enabler_id> == true`. Never synthesize a "false" or "no" option just to make a select_one work — option lists must have ≥2 widget-bearing entries OR fit the OR-with-multi-select pattern (Rule 13).

Output ONLY a valid YAML document inside a ```yaml code block. No commentary outside the block.
"""


def call_llm(digest: str, *, endpoint: str, model: str, max_tokens: int,
             disable_thinking: bool = True,
             history: list[dict] | None = None) -> str:
    """Single LLM call. If `history` is provided, it replaces the default
    [system, user(digest)] pair — used by the retry loop to feed back
    validator errors as additional turns."""
    client = OpenAI(base_url=endpoint, api_key="not-needed")
    extra_body: dict = {}
    if disable_thinking:
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}
    messages = history if history is not None else [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            "Convert the following form digest into a YAML tree per the schema:\n\n"
            + digest
        )},
    ]
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.0,
        extra_body=extra_body,
    )
    choice = resp.choices[0]
    finish = choice.finish_reason
    content = choice.message.content or ""
    print(f"finish_reason={finish}  content_chars={len(content)}", file=sys.stderr)
    return content


def parse_reply(reply: str, form_id: str) -> tuple[dict | None, str | None]:
    """Extract+parse YAML from reply. Returns (tree, error_msg)."""
    yaml_str = extract_yaml(reply)
    try:
        tree = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        return None, f"YAML parse error: {e}"
    if isinstance(tree, list):
        tree = {"form_id": form_id, "nodes": tree}
    if not isinstance(tree, dict):
        return None, "YAML did not parse to a dict"
    return tree, None


def extract_yaml(reply: str) -> str:
    m = re.search(r"```(?:yaml)?\s*\n(.*?)\n```", reply, re.DOTALL)
    if m:
        return m.group(1)
    # Fall back to the whole reply if no fence
    return reply.strip()


ALLOWED_NODE_KEYS = {"id", "type", "prompt", "explanation", "when", "virtual",
                     "widget", "widgets", "options", "label"}
ALLOWED_OPTION_KEYS = {"value", "widget", "widgets", "label", "virtual"}


def widget_ids_in_digest(digest: str) -> set[str]:
    return set(re.findall(r"\bW\d{3}\b", digest))


def validate_tree(tree: dict, digest_widget_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if "nodes" not in tree or not isinstance(tree["nodes"], list):
        return ["tree has no `nodes` list"]
    seen_widgets: dict[str, str] = {}  # widget_id -> first node that used it
    seen_node_ids: set[str] = set()
    for i, node in enumerate(tree["nodes"]):
        if not isinstance(node, dict):
            errors.append(f"node[{i}] is not a dict")
            continue
        nid = node.get("id")
        ntype = node.get("type")
        if not nid:
            errors.append(f"node[{i}] missing id")
            continue
        if nid in seen_node_ids:
            errors.append(f"duplicate node id: {nid}")
        seen_node_ids.add(nid)
        if ntype not in ("select_one", "select_many", "text", "date", "currency", "enabler"):
            errors.append(f"{nid}: unknown type {ntype!r}")
        # Reject unknown node keys (catches LLM-hallucinated schema features
        # like `fields:` nested under options, `branches:`, etc.).
        unknown = set(node.keys()) - ALLOWED_NODE_KEYS
        if unknown:
            errors.append(f"{nid}: unknown keys {sorted(unknown)}")
        # Collect this node's widget references
        node_widgets: list[str] = []
        if ntype in ("select_one", "select_many"):
            opts = node.get("options") or []
            if len(opts) < 2:
                errors.append(f"{nid}: {ntype} needs >=2 options, got {len(opts)}")
            node_is_virtual = bool(node.get("virtual"))
            for j, opt in enumerate(opts):
                if not isinstance(opt, dict):
                    errors.append(f"{nid}.options[{j}] is not a dict")
                    continue
                unknown_opt = set(opt.keys()) - ALLOWED_OPTION_KEYS
                if unknown_opt:
                    errors.append(
                        f"{nid}.options[{j}]: unknown keys {sorted(unknown_opt)}"
                    )
                val = opt.get("value")
                if val is not None and not isinstance(val, str):
                    errors.append(
                        f"{nid}.options[{j}]: value must be string, got "
                        f"{type(val).__name__} {val!r}"
                    )
                # Accept either `widget: Wxxx` (single) or `widgets: [Wxxx, Wyyy]`
                # (multiple, for the same logical answer marked in two places).
                w_single = opt.get("widget")
                w_plural = opt.get("widgets") or []
                opt_widgets: list[str] = []
                if w_single:
                    opt_widgets.append(w_single)
                opt_widgets.extend(w_plural)
                if opt_widgets:
                    node_widgets.extend(opt_widgets)
                elif ntype == "select_one":
                    opt_is_virtual = opt.get("virtual")
                    if not (node_is_virtual or opt_is_virtual):
                        errors.append(f"{nid}.options[{j}] missing widget")
                else:  # select_many
                    errors.append(f"{nid}.options[{j}] missing widget")
            # select_one without virtual: true must have at least one
            # widget-bearing option, otherwise there's no way to record which
            # option the user chose. Catches the both-options-virtual anti-pattern.
            if ntype == "select_one" and not node_is_virtual:
                has_real_widget = any(
                    isinstance(o, dict) and (o.get("widget") or o.get("widgets"))
                    for o in opts
                )
                if opts and not has_real_widget:
                    errors.append(
                        f"{nid}: select_one has no widget-bearing options "
                        f"— mark `virtual: true` on the node if intentional, "
                        f"or use enabler/select_many"
                    )
        elif ntype == "enabler":
            # Accept either `widget: W001` or `widgets: [W001]` — LLM tends
            # to default to the plural form even though the schema says
            # singular. As long as exactly one widget is bound, it's fine.
            w = node.get("widget")
            ws = node.get("widgets") or []
            if w:
                node_widgets.append(w)
            elif len(ws) == 1:
                node_widgets.extend(ws)
            elif len(ws) > 1:
                errors.append(f"{nid}: enabler must have exactly one widget, got {len(ws)}")
            else:
                errors.append(f"{nid}: enabler needs widget")
        else:  # text/date/currency
            # Accept either `widget: Wxxx` or `widgets: [Wxxx, ...]`.
            w_single = node.get("widget")
            ws = node.get("widgets") or []
            if w_single:
                node_widgets.append(w_single)
            node_widgets.extend(ws)
            if not node_widgets:
                errors.append(f"{nid}: {ntype} needs at least one widget")
        for w in node_widgets:
            if w not in digest_widget_ids:
                errors.append(f"{nid}: widget {w} not in digest (hallucinated)")
            if w in seen_widgets:
                errors.append(
                    f"{nid}: widget {w} already bound by {seen_widgets[w]} (double-binding)"
                )
            else:
                seen_widgets[w] = nid
    return errors


def emit_with_retry(digest: str, form_id: str, *, endpoint: str, model: str,
                    max_tokens: int, retries: int,
                    save_raw_dir: pathlib.Path | None = None,
                    ) -> tuple[dict | None, list[str], int]:
    """Run emit→validate up to `retries+1` times. Returns (tree, errors, attempts).
    On each retry, the previous reply and the validator errors are appended to
    the conversation as additional turns so the model can self-correct."""
    digest_widget_ids = widget_ids_in_digest(digest)
    history: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            "Convert the following form digest into a YAML tree per the schema:\n\n"
            + digest
        )},
    ]
    last_tree: dict | None = None
    last_errs: list[str] = ["(no attempts made)"]
    for attempt in range(retries + 1):
        print(f"\n── attempt {attempt + 1}/{retries + 1} ──", file=sys.stderr)
        reply = call_llm(digest, endpoint=endpoint, model=model,
                         max_tokens=max_tokens, history=history)
        if save_raw_dir is not None:
            save_raw_dir.mkdir(parents=True, exist_ok=True)
            (save_raw_dir / f"{form_id}.attempt{attempt + 1}.txt").write_text(reply)
        tree, parse_err = parse_reply(reply, form_id)
        if tree is None:
            last_errs = [parse_err or "parse failed"]
            if attempt < retries:
                history.append({"role": "assistant", "content": reply})
                history.append({"role": "user", "content": (
                    f"That reply could not be parsed as YAML ({parse_err}). "
                    "Re-emit the YAML tree inside a ```yaml code block. "
                    "Output ONLY the YAML, no commentary."
                )})
                continue
            return None, last_errs, attempt + 1
        last_tree = tree
        last_errs = validate_tree(tree, digest_widget_ids)
        if not last_errs:
            return tree, [], attempt + 1
        if attempt < retries:
            error_bullets = "\n".join(f"  - {e}" for e in last_errs)
            history.append({"role": "assistant", "content": reply})
            history.append({"role": "user", "content": (
                f"The previous tree had {len(last_errs)} validation issue(s):\n\n"
                f"{error_bullets}\n\n"
                "Re-emit the FULL YAML tree, fixing every issue. "
                "Common fixes: unknown keys → remove them (no `children:`, `fields:`, "
                "`branches:`, `group:`); duplicate node id → rename one; widget not in "
                "digest → drop the binding; double-bound widget → assign it to exactly "
                "one node; missing widget → add the right Wxxx or drop the node. "
                "Output ONLY the corrected YAML inside a ```yaml block."
            )})
    return last_tree, last_errs, retries + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("digest", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--endpoint",
                    default=os.environ.get("VLM_API_BASE", "http://localhost:8088/v1"))
    ap.add_argument("--model", default="Qwen3.6-27B-FP8")
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--retries", type=int, default=0,
                    help="If >0, on validation failure send errors back to the "
                         "model and retry up to N times.")
    ap.add_argument("--save-raw", type=pathlib.Path,
                    help="Directory to save raw model replies per attempt.")
    args = ap.parse_args()
    if not args.digest.exists():
        print(f"missing: {args.digest}", file=sys.stderr)
        return 2

    digest = args.digest.read_text()
    print(f"digest: {len(digest)} bytes")
    print(f"calling {args.model} at {args.endpoint}  (retries={args.retries})")

    form_id = args.digest.stem
    tree, errs, attempts = emit_with_retry(
        digest, form_id,
        endpoint=args.endpoint, model=args.model,
        max_tokens=args.max_tokens, retries=args.retries,
        save_raw_dir=args.save_raw,
    )
    print(f"\nattempts={attempts}", file=sys.stderr)
    if tree is None:
        print(f"failed to produce a parseable tree after {attempts} attempt(s)",
              file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 3
    digest_widget_ids = widget_ids_in_digest(digest)
    if errs:
        print(f"\nVALIDATION: {len(errs)} issue(s) (after {attempts} attempt(s))")
        for e in errs:
            print(f"  - {e}")
    else:
        print(f"\nVALIDATION: clean (after {attempts} attempt(s))")

    # Coverage stats
    used = set()
    for node in tree.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        for opt in node.get("options") or []:
            if not isinstance(opt, dict):
                continue
            if opt.get("widget"):
                used.add(opt["widget"])
            for w in opt.get("widgets") or []:
                used.add(w)
        for w in node.get("widgets") or []:
            used.add(w)
        # Singular `widget:` is allowed on enabler/text/date/currency.
        if node.get("widget"):
            used.add(node["widget"])
    unused = sorted(digest_widget_ids - used)
    print(f"coverage: {len(used)}/{len(digest_widget_ids)} widgets bound")
    if unused:
        print(f"unbound: {', '.join(unused[:20])}"
              + (" ..." if len(unused) > 20 else ""))

    out = args.out or args.digest.with_suffix(".tree.yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Re-serialize from the parsed tree so the saved YAML always has the
    # canonical {form_id, nodes} wrapper, even when the model omitted it.
    out.write_text(yaml.safe_dump(tree, sort_keys=False, default_flow_style=False))
    if errs:
        print(f"\nwrote {out} for inspection — fix the {len(errs)} "
              f"issue(s) above before running apply_tree.", file=sys.stderr)
        return 4
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
