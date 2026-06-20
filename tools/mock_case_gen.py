#!/usr/bin/env python3
"""Generate a realistic, schema-valid mock case for any form (smoke-test data).

Reads a form's schema.json and, from each field's `fill_strategy.source`, routes
a synthesized value to the right place in the canonical case object:

  case_dict.<key>        -> case_dict[key]
  <role>_record.<attr>   -> <role>_record[attr]   (one coherent identity per role)
  llm_over_narrative      -> narrative_facts[field_id]   (composed by data_type)
  recompute / wet_ink / human_decision / left_blank -> left for the fill pipeline

Values are Maine-flavoured and seeded (deterministic per form+seed), so smoke
tests get stable, plausible matter data without any real PII.

    python3 tools/mock_case_gen.py --form DE-401 --seed 7 --out /tmp/de401.json
    python3 tools/mock_case_gen.py --form DE-401 --seed 7 --fill /tmp/de401.pdf
"""
from __future__ import annotations
import argparse, json, pathlib, random, re, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent

COUNTIES = ["Cumberland", "York", "Penobscot", "Kennebec", "Androscoggin",
            "Aroostook", "Hancock", "Knox", "Lincoln", "Sagadahoc"]
TOWNS = [("Portland", "04101"), ("Falmouth", "04105"), ("Bangor", "04401"),
         ("Augusta", "04330"), ("Brunswick", "04011"), ("Saco", "04072"),
         ("Camden", "04843"), ("Bath", "04530"), ("Orono", "04473"),
         ("Lewiston", "04240"), ("Scarborough", "04074"), ("Kennebunk", "04043")]
STREETS = ["Pine Hill Road", "Commercial Street", "Maple Avenue", "Ocean View Drive",
           "Birch Lane", "Falmouth Foreside Way", "Main Street", "Elm Court",
           "Highland Terrace", "Shore Road", "Mill Pond Lane", "Cedar Street"]
FIRST = ["Margaret", "Robert", "Sarah", "James", "Patricia", "John", "Linda",
         "Michael", "Barbara", "David", "Susan", "Thomas", "Helen", "Daniel",
         "Nancy", "Paul", "Karen", "Mark", "Ruth", "Joseph"]
LAST = ["Walsh", "Bennett", "Goff", "Pelletier", "Thibodeau", "Hutchins",
        "Gagnon", "Day", "Crowley", "Mercier", "Lapointe", "Snow", "Frost",
        "Googins", "Jewett", "Pomroy"]


class Bank:
    """Deterministic synthetic-data bank with one coherent identity per role."""
    def __init__(self, seed, stress=False):
        self.r = random.Random(seed)
        self.stress = stress
        self.county = self.r.choice(COUNTIES)
        yr = self.r.randint(2024, 2026)
        self.docket = f"{yr}-{self.county[:4].upper()}-{self.r.randint(1, 9999):04d}"
        self._roles = {}

    def person(self):
        if self.stress:  # long hyphenated names to shake out horizontal overflow
            return (f"{self.r.choice(FIRST)}-{self.r.choice(FIRST)} "
                    f"{self.r.choice(LAST)}-{self.r.choice(LAST)} {self.r.choice(LAST)}")
        return f"{self.r.choice(FIRST)} {self.r.choice(LAST)[:1]}. {self.r.choice(LAST)}"

    def role(self, role):
        if role not in self._roles:
            town, zp = self.r.choice(TOWNS)
            street = f"{self.r.randint(2, 990)} {self.r.choice(STREETS)}"
            if self.stress:
                street = f"{self.r.randint(100,9999)} {self.r.choice(STREETS)}, Apt {self.r.randint(1,40)}B"
            self._roles[role] = {
                "name": self.person(),
                "street": street,
                "city": town, "state": "ME", "zip": zp,
                "phone": f"(207) 555-0{self.r.randint(100, 199)}",
                "email": f"user{self.r.randint(10,99)}@example.com",
                "bar": str(self.r.randint(3000, 9999)),
            }
        return self._roles[role]

    def date(self, lo=1940, hi=2026):
        y = self.r.randint(lo, hi)
        return f"{y:04d}-{self.r.randint(1,12):02d}-{self.r.randint(1,28):02d}"

    def money(self):
        dollars = self.r.randint(2, 800) * 1000 + self.r.randint(0, 999)
        if self.stress:
            dollars = self.r.randint(10, 99) * 1_000_000 + self.r.randint(0, 999_999)
        return f"{dollars:,}.{self.r.randint(0,99):02d}"


_ADDR = {"address": "street", "city": "city", "state": "state", "zip": "zip",
         "phone": "phone", "telephone": "phone", "email": "email",
         "bar_number": "bar", "bar": "bar"}


def _value_for(field_id, data_type, key, bank: Bank):
    """Synthesize a value from the field's data_type and key name."""
    k = (key or field_id).lower()
    # jurat / signature date components are often mis-typed as generic text and
    # live in narrow blanks ("this __ day of ____, 20__"); give them fitting
    # short values instead of a long narrative sentinel.
    if re.search(r"(^|_)day($|_)", k):
        return str(bank.r.randint(1, 28))
    if re.search(r"(^|_)month($|_)", k):
        return bank.r.choice(["January", "February", "March", "April", "May",
                              "June", "July", "August", "September", "October",
                              "November", "December"])
    if re.search(r"(^|_)year($|_)", k):
        return str(bank.r.randint(2024, 2026))
    if data_type == "date" or k.endswith("date") or "date_of" in k:
        if "death" in k:
            return bank.date(2024, 2026)
        if "birth" in k or "dob" in k:
            return bank.date(1940, 1970)
        return bank.date(2025, 2026)
    if data_type == "currency" or "value" in k or "amount" in k or "fee" in k:
        if "fee" in k:
            return bank.r.choice(["175.00", "21.00", "0.00", "50.00"])
        return bank.money()
    if data_type == "person_name" or k.endswith("name"):
        return bank.role("primary")["name"]
    if data_type in ("docket_number",) or k == "docket_number":
        return bank.docket
    if "county" in k:
        return bank.county
    if "email" in k:
        return bank.role("attorney")["email"]
    if "phone" in k or "telephone" in k:
        return bank.role("primary")["phone"]
    if "bar" in k:
        return bank.role("attorney")["bar"]
    return None


def _role_of(source):
    m = re.match(r"([a-z_]+)_record\.(.+)", source)
    return (m.group(1), m.group(2)) if m else (None, None)


def generate(form_id, seed=0, stress=False):
    pkg = ROOT / "repo" / "forms" / form_id
    fields = json.loads((pkg / "schema.json").read_text())["fields"]
    bank = Bank(seed, stress=stress)
    case = {"case_dict": {}, "narrative_facts": {}}

    for f in fields:
        fid = f["field_id"]
        dt = f.get("data_type")
        src = (f.get("fill_strategy") or {}).get("source") or ""
        if src.startswith("case_dict."):
            key = src[len("case_dict."):]
            kl = key.lower()
            if "county" in kl:
                v = bank.county
            elif kl == "docket_number":
                v = bank.docket
            elif "estate_of" in kl or kl.startswith("estate_") or "decedent" in kl:
                v = bank.role("decedent")["name"]
            elif "name" in kl and "county" not in kl:
                v = bank.role("primary")["name"]
            else:
                v = _value_for(fid, dt, key, bank) or bank.role("primary")["name"]
            case["case_dict"][key] = v
        elif "_record." in src:
            role, attr = _role_of(src)
            if not role:
                continue
            rec = case.setdefault(f"{role}_record", {})
            al = attr.lower()
            r = bank.role(role)
            if attr == role or al.endswith("name") or al == "decedent" or al == "applicant" or al == "petitioner":
                rec[attr] = r["name"]
            elif al.endswith("address"):
                rec[attr] = f"{r['street']}, {r['city']}, ME {r['zip']}"
            elif any(al.endswith(s) for s in _ADDR):
                suf = next(s for s in _ADDR if al.endswith(s))
                rec[attr] = r[_ADDR[suf]]
            elif "date" in al:
                rec[attr] = _value_for(fid, "date", attr, bank)
            else:
                rec[attr] = _value_for(fid, dt, attr, bank) or r["name"]
        elif src == "llm_over_narrative":
            v = _value_for(fid, dt, fid, bank)
            # Only fall back to the narrative sentinel for genuinely free-text
            # fields. Routing "Not applicable" into a date/currency/numeric blank
            # (e.g. a jurat "___ day of ___" or a "$___" slot) overflows the
            # narrow box and is semantically wrong -- leave those for the real
            # narrative/human step instead.
            if v is None and dt in ("text", None):
                v = "Not applicable"
            if v is not None:
                case["narrative_facts"][fid] = v
    case["_meta"] = {"form": form_id, "seed": seed, "synthetic": True}
    return case


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out")
    ap.add_argument("--fill", help="also fill the form to this PDF path")
    a = ap.parse_args()
    case = generate(a.form, a.seed)
    text = json.dumps(case, indent=2, ensure_ascii=False)
    if a.out:
        pathlib.Path(a.out).write_text(text); print(f"wrote {a.out}")
    else:
        print(text)
    if a.fill:
        import subprocess, tempfile
        cf = a.out or tempfile.mktemp(suffix=".json")
        pathlib.Path(cf).write_text(text)
        sys.path.insert(0, str(ROOT / "tools"))
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "fill_pdf.py"),
                            "--form", a.form, "--case", cf, "--out", a.fill],
                           capture_output=True, text=True)
        res = json.loads(r.stdout)
        print(f"filled {a.fill}: {res.get('text_written')} text, "
              f"{res.get('options_checked')} options, "
              f"source_verified={res.get('source_verified')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
