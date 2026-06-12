# Maine Probate Forms — Statutes for Consideration

> ⚠️ **Experimental — AI/LLM-generated, not legal advice.** The statute and case-law references on this page are generated and annotated by an AI model and have **not** been reviewed by an attorney. They list issues an LLM or person filling the form may want to *consider* — not what to do or conclude — and are no substitute for a licensed Maine attorney. Statute section text is quoted from legislature.maine.gov; the *selection of statutes/cases, the relevance notes, and any case-law holdings are the model's experimental annotations and may be wrong*. Which code applies can turn on the date of death — see the transition note. **Verify everything against the current statute and the actual opinions.**

A per-form layer mapping each court form to the Maine Uniform Probate Code (**Title 18-C**) sections worth considering when answering its questions, with a transition note for the former **Title 18-A** and pointers to related resources.

## How this is built

- **`_index/18c-sections.json`** — the trusted index of every 18-C section (verbatim from legislature.maine.gov). Every citation below resolves to it.
- **`_index/18a-key-diffs.json`** — the 18-A transition rule + material differences.
- **`_index/cross-refs.json`** — non-Title-18-C citations the forms touch (estate tax, etc.).
- **`_index/caselaw.json`** + **[`caselaw.md`](caselaw.md)** — Maine Law Court (Supreme Judicial Court) estate/probate decisions, tied to forms through the statutes they construe. ⚠️ The case selection and holding summaries are AI/LLM-generated, experimental, and not attorney-reviewed — read the opinion and confirm it is still good law.
- **`../digital-assets-access.md`** — accessing a deceased person's online accounts (grounded in 18-C Article 10, the Maine RUFADAA).
- Per-form pages are generated from `repo/forms/<FORM>/statutes.json`.

## The transition rule (read this first)

Title 18-C took effect **2019-09-01** (18-C §8-301). Title 18-C took effect 2019-09-01. It applies to: (a) wills of, and intestate succession / elective share / exempt property / wrongful-death matters for, decedents who die on or after 2019-09-01; and (b) any probate proceeding pending on, or commenced on or after, the effective date, regardless of when the decedent died. Title 18-A (the former code) continues to govern the substantive succession rights for estates of decedents who died BEFORE 2019-09-01.

> **Practical test:** The single most useful upstream question for almost every estate form: did the decedent die before or on/after 2019-09-01? Date of death selects the substantive code; the procedural code in a pending/new case is 18-C either way.

See [`_index/18a-key-diffs.json`](_index/18a-key-diffs.json) for the material 18-A→18-C differences (elective share, intestate shares, guardianship rewrite, small-estate threshold, TOD deeds, digital assets).

## Forms by category

### Decedent's Estates — Probate & Administration

- [DE-101](DE-101.md) — Petition for Formal Adjudication of Intestacy and Appointment of PR (or Adjudication Only) (7 governing, 15 per-question)
- [DE-101(I)](DE-101(I).md) — Application for Informal Probate / Appointment - Intestate (6 governing, 12 per-question)
- [DE-104](DE-104.md) — PR Acceptance (2 governing, 1 per-question)
- [DE-201](DE-201.md) — Petition for Formal Probate of Will or Appointment of Personal Representative or Both (7 governing, 14 per-question)
- [DE-201(I)](DE-201(I).md) — Application for Informal Probate of Will or Appointment (6 governing, 8 per-question)
- [DE-301](DE-301.md) — Petition for Formal Appointment of Special Administrator (3 governing, 7 per-question)
- [DE-301(I)](DE-301(I).md) — Application for Informal Appointment of Special Administrator (3 governing, 3 per-question)
- [DE-401](DE-401.md) — Certificate of Value Resident and Non Resident (1 governing, 5 per-question)
- [DE-403](DE-403.md) — Bond For Personal Representative (4 governing, 3 per-question)
- [DE-405](DE-405.md) — Inventory (3 governing, 3 per-question)
- [DE-406](DE-406.md) — Probate Account (4 governing, 4 per-question)
- [DE-407](DE-407.md) — Renunciation-Nomination (1 governing, 2 per-question)
- [DE-501](DE-501.md) — Petition with Respect to Supervised Administration (4 governing, 3 per-question)
- [DE-502](DE-502.md) — Demand For Bond (2 governing, 2 per-question)
- [DE-503](DE-503.md) — Claim Against Estate (4 governing, 5 per-question)
- [DE-504](DE-504.md) — Petition to Resolve Disputed Claim and Allowance (2 governing, 2 per-question)
- [DE-505](DE-505.md) — Petition with Respect to Pretermitted or Omitted Child (1 governing, 3 per-question)
- [DE-506](DE-506.md) — Petition for Elective Share (4 governing, 5 per-question)
- [DE-507](DE-507.md) — Petition to Reopen Estate (2 governing, 2 per-question)
- [DE-509](DE-509.md) — Petition for Removal of Personal Representative (3 governing, 2 per-question)
- [DE-601](DE-601.md) — Petition for Order of Complete Settlement (2 governing, 1 per-question)
- [DE-602](DE-602.md) — Sworn Statement (1 governing, 1 per-question)
- [DE-603](DE-603.md) — Closing Statement for Small Estate (2 governing, 1 per-question)
- [DE-605](DE-605.md) — Verified Application for Certificate of Discharge (2 governing, 2 per-question)

### Affidavits & Alternative Procedures

- [AF-101](AF-101.md) — Jurisdictional Affidavit (2 governing, 2 per-question)
- [AF-101.vA](AF-101.vA.md) — Jurisdictional Affidavit (2 governing, 2 per-question)
- [AF-102](AF-102.md) — Small Estate Affidavit for Collection of Personal Property (2 governing, 2 per-question)
- [AF-103](AF-103.md) — Affidavit of Name Change for Adult (1 governing, 2 per-question)
- [AF-104](AF-104.md) — Affidavit of Diligent Search (1 governing, 2 per-question)
- [AF-105](AF-105.md) — Indigency-Financial Affidavit (2 governing, 2 per-question)

### Guardianship & Conservatorship

- [PP-107](PP-107.md) — Petition for Appointment of Conservator of Minor (4 governing, 3 per-question)
- [PP-108](PP-108.md) — Acceptance of Appt by Conservator - Minor (2 governing, 1 per-question)
- [PP-201](PP-201.md) — Petition for Appointment of Guardian (5 governing, 8 per-question)
- [PP-203](PP-203.md) — Acceptance of Appointment by Guardian (2 governing, 1 per-question)
- [PP-205](PP-205.md) — Joined Petition for Guardian and Conservator (4 governing, 5 per-question)
- [PP-207](PP-207.md) — Acceptance of Appointment by Guardian and Conservator (2 governing, 1 per-question)
- [PP-209](PP-209.md) — Interim and Annual Report of Guardian (2 governing, 3 per-question)
- [PP-210](PP-210.md) — Registration of Guardianship or Conservatorship (1 governing, 2 per-question)
- [PP-401](PP-401.md) — Petition for Appointment of Conservator (6 governing, 7 per-question)
- [PP-402](PP-402.md) — Acceptance of Appointment by Conservator (2 governing, 1 per-question)
- [PP-405](PP-405.md) — Bond for Conservator (3 governing, 2 per-question)
- [PP-406](PP-406.md) — Inventory (1 governing, 2 per-question)
- [PP-407](PP-407.md) — Conservator Account (2 governing, 2 per-question)
- [PP-408](PP-408.md) — Claim Against Estate (1 governing, 2 per-question)
- [PP-409](PP-409.md) — Petition to Resolve Disputed Claim and Petition for Allowance (1 governing, 1 per-question)
- [PP-410](PP-410.md) — Petition for Interim Order (2 governing, 2 per-question)
- [PP-412](PP-412.md) — Conservators Report (2 governing, 2 per-question)
- [PP-413](PP-413.md) — Petition for Termination, Removal or Resignation (5 governing, 4 per-question)
- [PP-502](PP-502.md) — Guardianship Plan-Adult (1 governing, 2 per-question)
- [PP-503](PP-503.md) — Conservator Plan (1 governing, 2 per-question)
- [PP-504](PP-504.md) — Joined Plan (2 governing, 2 per-question)
- [PP-505](PP-505.md) — Physician's or Psychologist's Report (2 governing, 2 per-question)
- [PP-506](PP-506.md) — Visitor's Report (2 governing, 2 per-question)
- [PP-507](PP-507.md) — Affidavit for Emergency Guardian and-or Conservator (2 governing, 3 per-question)
- [PP-509](PP-509.md) — Petition to Accept Transfer of Guardianship.Conservatorship (1 governing, 1 per-question)
- [PP-510](PP-510.md) — Petition to Transfer of Guardianship-Conservatorship and Provisional Order (1 governing, 2 per-question)
- [PP-601](PP-601.md) — Petition for Other Protective Arrangements (4 governing, 2 per-question)

### Guardianship & Conservatorship — Minors

- [GS-008](GS-008.md) — Acceptance of Appt by Guardian (3 governing, 1 per-question)
- [GS-008.vA](GS-008.vA.md) — Acceptance of Appt by Guardian (3 governing, 1 per-question)
- [GS-014](GS-014.md) — Status Report of the Guardian (2 governing, 2 per-question)
- [PB-007](PB-007.md) — GAL Joint Appt. Order 3.4.20 (2 governing, 2 per-question)
- [PB-007.vA](PB-007.vA.md) — GAL Joint Appt. Order 3.4.20 (2 governing, 1 per-question)

### Adoption

- [AD-007](AD-007.md) — Confidential Statement (2 governing, 1 per-question)
- [AD-008](AD-008.md) — Report of Disbursements (1 governing, 2 per-question)
- [AD-009](AD-009.md) — Certificate of Counseling (1 governing, 1 per-question)
- [AD-011](AD-011.md) — Pet to Recognize Foreign Adoption (1 governing, 2 per-question)
- [AD-026](AD-026.md) — Petition for Adult Adoption (3 governing, 3 per-question)
- [AD-028](AD-028.md) — Affidavit of Parentage (1 governing, 1 per-question)

### Name Change

- [CN-1](CN-1.md) — Name Change Petition (1 governing, 3 per-question)
- [NC-001](NC-001.md) — Petition for Name Change of Minor (1 governing, 1 per-question)

### Notices & Service

- [N-105](N-105.md) — Demand for Notice (2 governing, 1 per-question)
- [N-106](N-106.md) — Notice of Removal (1 governing, 1 per-question)
- [N-107](N-107.md) — Waiver of Notice (2 governing, 1 per-question)
- [N-108](N-108.md) — Waiver of Notice on Behalf of Minor or Individual Subject to G-C (2 governing, 1 per-question)
- [N-112](N-112.md) — Notice of Intent to Register Guardianship or Conservatorship (1 governing, 1 per-question)
- [N-115](N-115.md) — Notice re Appointment of PR to Heirs, Devisees (2 governing, 2 per-question)
- [N-117](N-117.md) — Notice of Appointment of GC (2 governing, 1 per-question)
- [N-118](N-118.md) — Notice of Guardianship Conservatorship Proceeding (2 governing, 4 per-question)

### Appeals & Court Procedure

- [APP-1](APP-1.md) — Notice of Appeal to Law Court (2 governing, 1 per-question)
- [APP-2](APP-2.md) — Transcript Order (2 governing, 1 per-question)

### Miscellaneous

- [MISC-101](MISC-101.md) — Motion Form (2 governing, 2 per-question)
- [MISC-102](MISC-102.md) — Witness Subpoena (2 governing, 2 per-question)
