#!/usr/bin/env python3
"""Smoke-test the DocuSign export against a free developer sandbox.

This is the one paradigm worth a live account: the export places fields by
page + x/y, and whether those land correctly in DocuSign's tab coordinate space
can only be confirmed by a real render. This script builds an envelope from
`docusign_template.json` + the form's blank PDF and creates it in your sandbox as
a DRAFT (status=created) — nothing is emailed. Open it in the sandbox web console
to eyeball tab placement, then delete it.

Get a free, non-expiring sandbox at https://developers.docusign.com. You need:
  DOCUSIGN_BASE_URL      (default https://demo.docusign.net)
  DOCUSIGN_ACCOUNT_ID    (API account id, GUID — from Apps & Keys)
  DOCUSIGN_ACCESS_TOKEN  (an OAuth token; the quickest is the API Request
                          Builder / a generated token, or your own JWT/auth-code flow)

    export DOCUSIGN_ACCOUNT_ID=... DOCUSIGN_ACCESS_TOKEN=...
    python3 -m tools.export.docusign_sandbox_test --form DE-101

    # send it for real (emails the signer) instead of leaving a draft:
    python3 -m tools.export.docusign_sandbox_test --form DE-101 \
        --send --signer-email you@example.com --signer-name "Test Filer"

No SDK dependency — pure urllib. The token-getting step is yours to do in the
DocuSign developer console; this script only exercises the envelope payload.
Not legal advice.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from tools.export import model as M           # noqa: E402
from tools.export import exporters as X       # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (maine-probate-forms-oss docusign smoke test)"}


def _fetch_blank(form: M.Form) -> bytes:
    req = urllib.request.Request(form.source_url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _count_tabs(tabs: dict) -> int:
    return sum(len(v) for v in tabs.values())


def build_envelope(form: M.Form, pdf_bytes: bytes, send: bool,
                   signer_email: str, signer_name: str) -> dict:
    """docusign_template.json + the blank PDF -> a v2.1 envelopeDefinition."""
    env = json.loads(X.export_esign(form)["docusign_template.json"])
    env["documents"][0]["documentBase64"] = base64.b64encode(pdf_bytes).decode()
    signer = env["recipients"]["signers"][0]
    signer["email"] = signer_email
    signer["name"] = signer_name
    # status=created -> a DRAFT (nothing sent); status=sent -> emails the signer.
    env["status"] = "sent" if send else "created"
    return env


def create_envelope(base_url: str, account_id: str, token: str, env: dict) -> dict:
    url = f"{base_url.rstrip('/')}/restapi/v2.1/accounts/{account_id}/envelopes"
    body = json.dumps(env).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json", **UA})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return {"status": r.status, "body": json.loads(r.read())}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail)
        except Exception:
            pass
        return {"status": e.code, "error": detail}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True, help="form id, e.g. DE-101")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--pdf", help="local blank PDF (else fetched from source_url)")
    ap.add_argument("--send", action="store_true",
                    help="actually send (emails signer); default leaves a draft")
    ap.add_argument("--signer-email", default=os.environ.get(
        "DOCUSIGN_SIGNER_EMAIL", "test-signer@example.com"))
    ap.add_argument("--signer-name", default="Test Filer")
    ap.add_argument("--dry-run", action="store_true",
                    help="build + validate the envelope, print a summary, do not call the API")
    a = ap.parse_args()

    form = M.load_form(a.form, pathlib.Path(a.root))
    pdf = pathlib.Path(a.pdf).read_bytes() if a.pdf else _fetch_blank(form)
    env = build_envelope(form, pdf, a.send, a.signer_email, a.signer_name)
    tabs = env["recipients"]["signers"][0]["tabs"]
    print(f"{a.form}: {_count_tabs(tabs)} tabs across "
          f"{ {k: len(v) for k, v in tabs.items()} }, "
          f"{len(pdf)} byte PDF, status={env['status']}")

    if a.dry_run:
        print("dry-run: envelope built and valid; not calling the API.")
        return 0

    base = os.environ.get("DOCUSIGN_BASE_URL", "https://demo.docusign.net")
    acct = os.environ.get("DOCUSIGN_ACCOUNT_ID")
    token = os.environ.get("DOCUSIGN_ACCESS_TOKEN")
    if not (acct and token):
        print("\nSet DOCUSIGN_ACCOUNT_ID and DOCUSIGN_ACCESS_TOKEN to call the sandbox "
              "(or pass --dry-run to just validate the payload).", file=sys.stderr)
        return 2

    res = create_envelope(base, acct, token, env)
    if "error" in res:
        print(f"\nAPI {res['status']} error:\n{json.dumps(res['error'], indent=2)}")
        return 1
    eid = res["body"].get("envelopeId")
    print(f"\nOK envelope {eid} ({res['body'].get('status')}).")
    print(f"View it: {base}/  ->  Manage  ->  {'Sent' if a.send else 'Drafts'}.")
    print("Open the envelope to verify tab placement, then delete it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
