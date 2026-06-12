# DE-101 (Formal) Examples

Worked example showing the inputs and expected outputs for filling
DE-101 (Petition for Formal Adjudication of Intestacy and Appointment
of PR). The informal counterpart with the same fact pattern lives at
`repo/forms/DE-101(I)/examples/`.

| file | role |
|---|---|
| `case.example.json` | canonical case input (case_dict + records + narrative_facts) |
| `filled.example.json` | expected resolved values per field id |

Run it:

```bash
python3 tools/fill_plan.py --form DE-101 --case "repo/forms/DE-101/examples/case.example.json"
python3 tools/fill_pdf.py  --form DE-101 --case "repo/forms/DE-101/examples/case.example.json" --out /tmp/DE-101.filled.pdf
python3 tools/verify_filled.py --form DE-101 --case "repo/forms/DE-101/examples/case.example.json" --filled /tmp/DE-101.filled.pdf
```

Wet-ink fields (`petitioner_signature`, the `Dated:` line, and the
renunciation block) stay blank by design. The `PETITION TO JUDGE`
checkboxes fill from `narrative_facts.petition_type`
(`formal_adjudication` / `formal_appointment` — list = check both).
