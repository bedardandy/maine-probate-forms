#!/usr/bin/env python3
"""Author the per-form statutes.json sidecars from a single curated source.

This is the human-authored source of truth for the "statutes for consideration"
layer. Each form maps to:
  - governing:    statute sections the form is built on (cite + why)
  - per_question: for legally-material fields, the considerations an LLM/filler
                  should weigh (field_id + list of {cite?, note})
  - transition:   the 18-A / date-of-death transition note for the form
  - cross_refs:   non-Title-18-C citations the form touches

Statute TITLES are not hand-typed — they are looked up from the trusted index
(docs/statute-reference/_index/18c-sections.json) and cross-ref table, so a wrong
section number fails loudly here rather than shipping a plausible-but-wrong cite.
Every per_question field_id is validated against the form's schema.json.

This emits repo/forms/<FORM>/statutes.json for all 79 forms. It is a
considerations aid, NOT legal advice (see DISCLAIMER).

Usage:
    python3 scripts/author_statutes.py            # write all sidecars
    python3 scripts/author_statutes.py --check     # validate only, write nothing
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
FORMS_DIR = REPO / "repo" / "forms"
IDX = REPO / "docs" / "statute-reference" / "_index"

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

T_ESTATE = (
    "Title 18-C applies to estates of decedents who die on or after 2019-09-01, "
    "and to any proceeding pending on or commenced after that date. For a decedent "
    "who died before 2019-09-01, former Title 18-A supplies the substantive "
    "succession rules even though the proceeding itself runs under 18-C. "
    "See _index/18a-key-diffs.json (governing rule: 18-C §8-301)."
)
T_GC = (
    "18-C Article 5 (Maine Uniform Guardianship, Conservatorship and Protective "
    "Proceedings Act) governs proceedings pending on or commenced after 2019-09-01. "
    "Orders entered under former Title 18-A may rest on different standards; the "
    "modern least-restrictive / supported-decision-making framework applies going "
    "forward. See _index/18a-key-diffs.json."
)
T_ADOPT = (
    "18-C Article 9 governs adoption proceedings under the current code; §9-108 "
    "addresses application of prior laws to pending matters."
)
T_PROC = (
    "Procedural form under current Title 18-C and the Maine Rules of Probate "
    "Procedure; the date-of-death substantive transition rules are generally not "
    "implicated, though the underlying estate or guardianship matter carries its "
    "own transition posture."
)

# ---------------------------------------------------------------------------
# Curated content. Tuple shorthand:
#   governing:    [ (cite, why), ... ]
#   per_question: [ (field_id, [ (cite_or_None, note), ... ]), ... ]
# Titles are filled from the index at build time.
# ---------------------------------------------------------------------------
FORMS: dict[str, dict] = {
    # ===================== DECEDENT ESTATES (DE-*, AF-102) =================
    "DE-101": {
        "summary": "Petition for formal adjudication of intestacy and appointment of a personal representative (or adjudication only).",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-401", "Formal testacy proceeding — this petition asks the JUDGE (not the register) to adjudicate intestacy, after notice and hearing."),
            ("18-C §3-402", "Required contents of a formal testacy/appointment petition."),
            ("18-C §3-403", "Notice of hearing to interested persons; the formal proceeding is litigated on notice."),
            ("18-C §3-409", "After hearing, the court enters an order determining heirs and intestacy."),
            ("18-C §3-414", "Formal proceedings concerning appointment of a PR."),
            ("18-C §3-203", "Priority among persons seeking appointment as PR."),
            ("18-C §3-108", "Three-year ultimate time limit, subject to its exceptions."),
        ],
        "per_question": [
            ("petition_type", [("18-C §3-401", "Formal adjudication and formal appointment may be sought together or adjudication alone (AND/OR checkboxes).")]),
            ("petitioner_interest", [("18-C §3-402", "Petitioner must state an interest giving standing to petition.")]),
            ("decedent_death_date", [("18-C §3-108", "Death date starts the 3-year clock."), ("18-C §8-301", "Death date selects 18-C vs former 18-A substantive law.")]),
            ("died_more_than_3_years", [("18-C §3-108", "After 3 years a formal testacy proceeding is limited to the §3-108 exceptions (e.g. determination of heirs).")]),
            ("heirs_name_1", [("18-C §2-103", "Heirs are determined by the intestacy statute."), ("18-C §3-402", "The petition must list heirs so the §3-409 order can adjudicate them.")]),
            ("non_registered_partner_status", [("18-C §2-102", "Only a registered domestic partner takes a spouse-equivalent intestate share."), ("18-C §1-201", "Statutory definition of 'domestic partner'.")]),
            ("real_estate_in_maine", [("18-C §3-201", "Maine-situs real property supports venue.")]),
            ("testamentary_instrument_status", [("18-C §3-402", "Petitioner must either identify an unrevoked testamentary instrument (and explain why it is not being probated) or state that diligent search found none.")]),
            ("pr_relationship", [("18-C §3-203", "Relationship drives statutory priority to serve as PR.")]),
            ("prior_equal_right", [("18-C §3-203", "Persons of prior or equal right must be addressed (renunciation, nomination, or notice)."), ("18-C §3-414", "Formal appointment resolves competing claims of priority.")]),
            ("pr_priority_question", [("18-C §3-414", "Questions of priority or qualification are resolved by the court in the formal proceeding; if none, state 'None.'")]),
            ("bond_status", [("18-C §3-603", "Bond requirements for a PR; the court may be asked to decide."), ("18-C §3-605", "An interested person may demand bond.")]),
            ("publish_notice_creditors", [("18-C §3-801", "Optional published notice to creditors shortens the claims period.")]),
            ("supervised_administration", [("18-C §3-501", "Supervised administration is a single in rem proceeding under continuing court authority.")]),
            ("renunciation_signature_1", [("18-C §3-203", "A person with priority may renounce the right to appointment or concur in the appointment sought.")]),
        ],
        "cross_refs": ["36 M.R.S. §4107"],
    },
    "DE-101(I)": {
        "summary": "Application for informal probate / appointment — intestate.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-301", "This form IS the §3-301 application for informal probate/appointment."),
            ("18-C §3-307", "Informal appointment of a personal representative and its effect."),
            ("18-C §3-308", "Proof and findings the register must make for informal appointment."),
            ("18-C §3-203", "Priority among persons seeking appointment as PR."),
            ("18-C §2-103", "Intestate shares of heirs other than the surviving spouse."),
            ("18-C §3-108", "Three-year ultimate time limit on probate/appointment proceedings."),
        ],
        "per_question": [
            ("applicant_legal_interest", [("18-C §3-301", "Applicant must have standing; interest determines who may apply.")]),
            ("personal_representative_relationship", [("18-C §3-203", "Relationship drives statutory priority to serve as PR; spouse/heirs rank ahead of creditors.")]),
            ("prior_equal_right_explanation", [("18-C §3-203", "Persons of equal or higher priority must be addressed (renunciation/nomination or consent).")]),
            ("decedent_date_of_death", [("18-C §3-108", "Death date starts the 3-year clock."), ("18-C §8-301", "Death date also selects 18-C vs former 18-A substantive law.")]),
            ("died_more_than_3_years", [("18-C §3-108", "After 3 years, informal probate/appointment is generally barred except for the §3-108 exceptions (e.g. determination of heirs, later-discovered will).")]),
            ("heir_1_name", [("18-C §2-103", "Heirs are determined by the intestacy statute."), ("18-C §2-106", "Per-capita-at-each-generation governs how issue share.")]),
            ("non_registered_domestic_partner", [("18-C §2-102", "Only a registered domestic partner takes a spouse-equivalent intestate share."), ("18-C §1-201", "See the statutory definition of 'domestic partner'; an unregistered partner is not an heir.")]),
            ("real_estate_in_maine", [("18-C §3-201", "Maine-situs real property supports venue and devolves under §3-101.")]),
            ("bond_requirement", [("18-C §3-603", "Bond is not required for an informal PR unless ordered or demanded."), ("18-C §3-605", "An interested person may demand bond.")]),
            ("demand_for_notice", [("18-C §3-204", "Anyone with an interest may file a demand for notice of orders/filings.")]),
            ("request_publish_notice_creditors", [("18-C §3-801", "Optional published notice to creditors shortens the claims period."), ("18-C §3-803", "Non-claim limitations on presentation of creditor claims.")]),
            ("testamentary_instrument", [(None, "Intestate form: the applicant is representing there is no will to be probated; if a will exists, use DE-201(I) instead.")]),
        ],
        "cross_refs": ["36 M.R.S. §4107"],
    },
    "DE-201": {
        "summary": "Petition for formal probate of will and/or formal appointment of a personal representative.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-401", "Formal testacy proceeding — probate of the will is adjudicated by the judge after notice and hearing."),
            ("18-C §3-402", "Required contents of the petition, including identification of the will and codicils."),
            ("18-C §3-403", "Notice of hearing to interested persons."),
            ("18-C §3-407", "Burdens of proof in contested testacy cases — the formal route when a will is contested."),
            ("18-C §3-414", "Formal proceedings concerning appointment of a PR."),
            ("18-C §3-203", "Priority to serve as PR (a will nominee has high priority)."),
            ("18-C §3-108", "Three-year ultimate time limit, subject to its exceptions."),
        ],
        "per_question": [
            ("petition_type", [("18-C §3-401", "Formal probate and formal appointment may be sought separately or together.")]),
            ("will_date", [("18-C §2-502", "Execution requirements the will must satisfy."), ("18-C §3-402", "The petition identifies the will by date and represents it was validly executed."), ("18-C §3-407", "If contested, proponents must establish due execution.")]),
            ("codicils_date", [("18-C §2-502", "Each codicil must independently satisfy execution requirements.")]),
            ("involves_will", [("18-C §3-401", "A formal testacy proceeding may concern a will or alleged intestacy.")]),
            ("will_probated_informally", [("18-C §3-401", "A formal proceeding may follow and supersede an earlier informal probate.")]),
            ("date_of_death", [("18-C §3-108", "Death date starts the 3-year clock."), ("18-C §8-301", "Death date selects 18-C vs former 18-A substantive law.")]),
            ("died_more_than_3_years", [("18-C §3-108", "After 3 years the proceeding is limited to the §3-108 exceptions.")]),
            ("heirs_page1_name_1", [("18-C §2-103", "Heirs must be listed even in testate estates — the §3-409 order determines heirship."), ("18-C §3-403", "Heirs receive notice of the hearing.")]),
            ("devisees_name_1", [("18-C §2-603", "Antilapse rules may substitute takers for a predeceased devisee."), ("18-C §3-403", "Devisees receive notice of the hearing.")]),
            ("pr_designated_in_will", [("18-C §3-203", "A PR nominated in the will has priority for appointment.")]),
            ("pr_priority_questions", [("18-C §3-414", "Priority/qualification questions are resolved by the court."), ("18-C §3-601", "Qualification of a PR.")]),
            ("bond_status", [("18-C §3-603", "Bond rules; a will may waive bond."), ("18-C §3-605", "An interested person may still demand bond.")]),
            ("supervised_administration", [("18-C §3-501", "Supervised administration request and standard.")]),
            ("publish_notice_creditors", [("18-C §3-801", "Published creditor notice shortens the claims period.")]),
        ],
        "cross_refs": ["36 M.R.S. §4107"],
    },
    "DE-201(I)": {
        "summary": "Application for informal probate of will and/or appointment — testate.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-301", "The application for informal probate of a will and appointment."),
            ("18-C §3-302", "Informal probate; duty of register and effect of informal probate of a will."),
            ("18-C §3-303", "Proof and findings required for informal probate of a will."),
            ("18-C §3-304", "Situations where informal probate is unavailable (must go formal)."),
            ("18-C §3-203", "Priority to serve as PR (a will nominee has high priority)."),
            ("18-C §3-108", "Three-year ultimate time limit."),
        ],
        "per_question": [
            ("will_date", [("18-C §2-502", "Execution requirements the will must satisfy."), ("18-C §2-503", "A self-proved will eases the proof burden."), ("18-C §2-501", "Testamentary capacity — who may make a will (the core will-validity question)."), ("18-C §3-407", "If the will is contested (capacity/undue influence), this sets the burdens; informal probate is unavailable for a contested will and the matter proceeds in formal testacy.")]),
            ("codicils_date", [("18-C §2-502", "Each codicil must independently satisfy execution requirements.")]),
            ("pr_designated_in_will", [("18-C §3-203", "A PR nominated in the will has priority for appointment.")]),
            ("devisees_name_1", [("18-C §2-603", "Antilapse rules may substitute takers for a predeceased devisee."), ("18-C §2-604", "Failure of a testamentary provision passes property elsewhere.")]),
            ("died_more_than_3_years", [("18-C §3-108", "After 3 years, informal probate of the will is generally barred except for §3-108 exceptions.")]),
            ("bond_requirement", [("18-C §3-603", "Bond ordinarily not required; a will may also waive bond."), ("18-C §3-605", "Interested person may still demand bond.")]),
            ("non_registered_partner", [("18-C §2-102", "Relevant if intestacy partially applies; only a registered partner is a spouse-equivalent.")]),
            ("publish_notice_creditors", [("18-C §3-801", "Published creditor notice; "), ("18-C §3-803", "non-claim limitations.")]),
        ],
        "cross_refs": ["36 M.R.S. §4107"],
    },
    "DE-301": {
        "summary": "Petition for formal appointment of a special administrator (judge-ordered, after notice/hearing unless emergency).",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-614", "When a special administrator may be appointed; formal appointment is by court order."),
            ("18-C §3-615", "Who may be appointed special administrator."),
            ("18-C §3-617", "Powers and duties of a special administrator appointed by the court — the order may limit them."),
        ],
        "per_question": [
            ("person_whose_appointment_is_sought", [("18-C §3-615", "Eligibility/priority for who serves as special administrator.")]),
            ("will_presented_for_probate", [("18-C §3-614", "Whether a will is pending bears on the need for and role of the special administrator.")]),
            ("nominated_as_pr_in_will", [("18-C §3-203", "A will nominee's priority; if another person is sought, the petition must explain.")]),
            ("reason_for_special_admin", [("18-C §3-614", "The court must find the appointment necessary to preserve the estate or secure its proper administration.")]),
            ("appointment_without_notice", [("18-C §3-614", "Appointment without notice requires an emergency; state its nature.")]),
            ("venue_basis_if_not_domiciled", [("18-C §3-201", "Venue for estate proceedings when the decedent was not domiciled in the county.")]),
            ("order_limitations", [("18-C §3-617", "Unless limited by the order, the special administrator has the powers of a general PR (court fills this section).")]),
        ],
    },
    "DE-301(I)": {
        "summary": "Application for informal appointment of a special administrator.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-614", "When a special administrator may be appointed."),
            ("18-C §3-615", "Who may be appointed special administrator."),
            ("18-C §3-616", "Powers and duties of a special administrator appointed informally."),
        ],
        "per_question": [
            ("person_whose_appointment_is_sought", [("18-C §3-615", "Eligibility/priority for who serves as special administrator.")]),
            ("will_presented_for_probate", [("18-C §3-616", "Whether a will is pending affects the special administrator's role/powers.")]),
            ("venue_basis_if_not_domiciled", [("18-C §3-201", "Venue for estate proceedings; basis when decedent not domiciled in the county.")]),
        ],
    },
    "DE-104": {
        "summary": "Personal representative's acceptance of appointment.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-601", "Qualification of a personal representative."),
            ("18-C §3-602", "Acceptance of appointment; consent to the court's jurisdiction."),
        ],
        "per_question": [
            ("signature_personal_representative", [("18-C §3-602", "Signing accepts the appointment and submits the PR personally to the court's jurisdiction.")]),
        ],
    },
    "DE-401": {
        "summary": "Certificate of value (resident and non-resident) — estate tax screening.",
        "transition": "Estate-tax exposure is governed by Title 36, Chapter 577 (deaths after 2012); the legacy 18-A-era cite to 36 M.R.S. §4063 is superseded. " + T_ESTATE,
        "governing": [
            ("18-C §3-706", "Inventory and appraisal supply the asset values certified here."),
        ],
        "per_question": [
            ("total_probate_estate_value", [("36 M.R.S. §4102", "Compared to the Maine exclusion amount to screen estate-tax liability.")]),
            ("maine_estate_tax_return", [("36 M.R.S. §4103", "Maine estate tax on a resident estate."), ("36 M.R.S. §4107", "Return and payment due 9 months after death.")]),
            ("federal_estate_tax_return", [(None, "Federal estate tax (IRC §2001; Form 706) is separate from Maine; a federal return may be required even when no Maine tax is due, and vice versa.")]),
            ("estimated_maine_estate_tax", [("36 M.R.S. §4103", "Computation of Maine estate tax above the exclusion amount.")]),
            ("decedent_interest_maine_real_estate", [("36 M.R.S. §4104", "Maine-situs real/tangible property of a nonresident is taxed under the nonresident provision.")]),
        ],
        "cross_refs": ["36 M.R.S. §4102", "36 M.R.S. §4103", "36 M.R.S. §4104", "36 M.R.S. §4107", "36 M.R.S. §4063"],
    },
    "DE-403": {
        "summary": "Bond for personal representative (with sureties).",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-603", "When a PR bond is required."),
            ("18-C §3-604", "Bond amount, security and procedure."),
            ("18-C §3-606", "Terms and conditions of bonds."),
            ("18-C §8-204", "Approval of the bond by the judge; surety procedures."),
        ],
        "per_question": [
            ("penal_sum_numeric", [("18-C §3-604", "The penal sum is set by reference to estate value and anticipated income.")]),
            ("corporate_surety_name", [("18-C §3-606", "Corporate-surety bond terms."), ("18-C §8-208", "Reduced liability where signed by a surety company.")]),
            ("affidavit_surety_1_signature", [("18-C §8-204", "Sufficiency of individual sureties is tested on approval.")]),
        ],
    },
    "DE-405": {
        "summary": "Inventory of the estate.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-706", "Duty to prepare and file an inventory and appraisal."),
            ("18-C §3-707", "Employment of appraisers."),
            ("18-C §3-708", "Supplementary inventory when new assets surface or values change."),
        ],
        "per_question": [
            ("appraisers_info", [("18-C §3-707", "Appraisers may be employed to value items; identify them.")]),
            ("calc_net_inventory", [("18-C §3-706", "Net value (gross less encumbrances) is the inventory's bottom line.")]),
            ("real_prop_1_desc", [("18-C §3-706", "Real property is inventoried at date-of-death value with encumbrances shown.")]),
        ],
    },
    "DE-406": {
        "summary": "Probate account (income, expenses, distributions).",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-1001", "An account supports a formal order of complete settlement."),
            ("18-C §3-719", "Compensation of the personal representative."),
            ("18-C §3-805", "Classification (priority) of claims and expenses paid."),
            ("18-C §3-902", "Order in which assets are appropriated; abatement."),
        ],
        "per_question": [
            ("expenses_amount", [("18-C §3-805", "Administration expenses and claims are paid in statutory priority order.")]),
            ("exemptions_allowances_amount", [("18-C §2-402", "Homestead allowance."), ("18-C §2-403", "Exempt property."), ("18-C §2-404", "Family allowance — these come off the top before general distribution.")]),
            ("distributions_amount", [("18-C §3-906", "Distribution in kind and valuation."), ("18-C §3-902", "Abatement where assets are insufficient.")]),
            ("maine_estate_tax_status", [("36 M.R.S. §4103", "Whether Maine estate tax was owed/paid.")]),
        ],
        "cross_refs": ["36 M.R.S. §4103"],
    },
    "DE-407": {
        "summary": "Renunciation of priority and nomination of personal representative.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-203", "A person with priority may renounce and nominate another to serve as PR."),
        ],
        "per_question": [
            ("renunciation_nomination_actions", [("18-C §3-203", "Renouncing priority and/or nominating another reorders who may be appointed.")]),
            ("nominate_pr_name", [("18-C §3-203", "A nominee of a person with priority steps into that priority.")]),
        ],
    },
    "DE-501": {
        "summary": "Petition with respect to supervised administration.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-501", "Nature of supervised administration."),
            ("18-C §3-502", "Petition for and order of supervised administration."),
            ("18-C §3-504", "Powers of the PR under court supervision."),
            ("18-C §3-505", "Interim, distribution and closing orders."),
        ],
        "per_question": [
            ("supervision_request", [("18-C §3-502", "Grounds/standard for placing the estate under supervision.")]),
            ("special_restrictions_details", [("18-C §3-504", "The order may restrict the PR's otherwise-broad powers.")]),
            ("testacy_status", [("18-C §3-502", "Testacy must be resolved as part of supervised administration.")]),
        ],
    },
    "DE-502": {
        "summary": "Demand for bond by an interested person.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-605", "An interested person may demand that the PR post bond."),
            ("18-C §3-604", "Sets how the bond amount is calculated."),
        ],
        "per_question": [
            ("bond_amount", [("18-C §3-604", "Demanded amount should track the statutory measure (estate value + income).")]),
            ("petitioner_interest_value", [("18-C §3-605", "The demanding person's stake supports the demand.")]),
        ],
    },
    "DE-503": {
        "summary": "Claim against the estate (creditor).",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-803", "Non-claim limitations on presentation of claims."),
            ("18-C §3-804", "Manner of presenting a claim."),
            ("18-C §3-805", "Classification (priority) of claims."),
            ("18-C §3-806", "Allowance or disallowance of claims."),
        ],
        "per_question": [
            ("amount_claimed", [("18-C §3-804", "Claim must state the amount and basis to be properly presented.")]),
            ("date_claim_due", [("18-C §3-810", "Claims not yet due or contingent/unliquidated are handled specially.")]),
            ("nature_of_uncertainty", [("18-C §3-810", "Describe contingency/unliquidated nature for proper treatment.")]),
            ("decision_by_personal_representative", [("18-C §3-806", "The PR allows or disallows; disallowance starts the claimant's clock to sue.")]),
            ("notice_mailed_date", [("18-C §3-803", "Presentation timing is measured against the non-claim bar."), ("18-C §3-806", "Mailing a disallowance starts the 60-day period to petition.")]),
        ],
    },
    "DE-504": {
        "summary": "Petition to resolve a disputed claim and for allowance.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-806", "Court resolution of allowance/disallowance of a disputed claim."),
            ("18-C §3-807", "Payment of allowed claims."),
        ],
        "per_question": [
            ("factual_legal_issues", [("18-C §3-806", "The dispute is framed for the court's allowance determination.")]),
            ("allowed_part_amount", [("18-C §3-806", "The court may allow a claim in whole or in part.")]),
        ],
    },
    "DE-505": {
        "summary": "Petition regarding a pretermitted/omitted child.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §2-302", "Share of a child omitted from the will (born/adopted after will execution)."),
        ],
        "per_question": [
            ("omitted_child_info", [("18-C §2-302", "Identifies the child claiming an omitted-child share.")]),
            ("omission_intent", [("18-C §2-302", "An intentional omission, or provision outside the will, defeats the claim.")]),
            ("intestate_share_prayer", [("18-C §2-302", "The omitted-child share is generally an intestate-equivalent share."), ("18-C §2-103", "Intestate-share computation supplies the amount.")]),
        ],
    },
    "DE-506": {
        "summary": "Petition for elective share of the surviving spouse.",
        "transition": (
            "MATERIAL DIFFERENCE: under former 18-A the elective share was a flat 1/3 of the "
            "augmented estate; under 18-C §2-202 it is 50% of the MARITAL-PROPERTY PORTION of "
            "the augmented estate, which scales with length of marriage (§2-203). Date of death "
            "selects which regime applies. " + T_ESTATE
        ),
        "governing": [
            ("18-C §2-202", "The elective-share amount = 50% of the marital-property portion of the augmented estate."),
            ("18-C §2-203", "Composition of the augmented estate and the length-of-marriage scaling."),
            ("18-C §2-209", "Sources from which the elective share is payable."),
            ("18-C §2-211", "Proceeding for elective share and its time limit."),
        ],
        "per_question": [
            ("election_extension_status", [("18-C §2-211", "The election has a deadline (tied to death and probate); extensions are limited.")]),
            ("probate_estate_value", [("18-C §2-204", "The net probate estate is one component of the augmented estate.")]),
            ("augmented_estate_value", [("18-C §2-203", "Augmented estate aggregates probate + nonprobate transfers, then applies the marital-property percentage.")]),
            ("transferees_name_1", [("18-C §2-205", "Nonprobate transfers to others are pulled into the augmented estate."), ("18-C §2-210", "Recipients can be personally liable to make up the elective share.")]),
            ("elective_share_determined", [("18-C §2-202", "Final elective-share amount the court determines.")]),
        ],
    },
    "DE-507": {
        "summary": "Petition to reopen an estate (subsequent administration).",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-1008", "Subsequent administration to deal with after-discovered property/business."),
            ("18-C §3-108", "The 3-year ultimate time limit still frames late proceedings."),
        ],
        "per_question": [
            ("newly_discovered_property", [("18-C §3-1008", "After-discovered assets are the usual basis to reopen.")]),
            ("priority_questions", [("18-C §3-203", "Who is appointed for the subsequent administration follows the priority rules.")]),
        ],
    },
    "DE-509": {
        "summary": "Petition for removal of a personal representative.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-611", "Removal of a PR for cause and the procedure."),
            ("18-C §3-608", "Termination of appointment generally."),
            ("18-C §3-613", "Appointment of a successor PR."),
        ],
        "per_question": [
            ("grounds_for_removal", [("18-C §3-611", "Removal requires statutory cause (mismanagement, conflict, etc.).")]),
            ("co_representative_status", [("18-C §3-717", "Co-representative dynamics affect who must act and removal effects.")]),
        ],
    },
    "DE-601": {
        "summary": "Petition for order of complete settlement.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-1001", "Formal proceeding terminating administration with an order of complete settlement."),
            ("18-C §3-1002", "Order construing the will without re-adjudicating testacy."),
        ],
        "per_question": [
            ("court_requests", [("18-C §3-1001", "The settlement order can adjudicate distribution, discharge, and protect the PR.")]),
        ],
    },
    "DE-602": {
        "summary": "Sworn statement (verified statement of the personal representative).",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-1003", "Closing the estate by the PR's sworn (verified) statement."),
        ],
        "per_question": [
            ("further_verify_actions", [("18-C §3-1003", "The sworn statement verifies notice, inventory, distribution and accounting steps.")]),
        ],
    },
    "DE-603": {
        "summary": "Closing statement for a small estate.",
        "transition": "Small-estate eligibility turns on the current §3-1201 threshold ($40,000, inflation-adjusted), raised from the lower former-18-A figure. " + T_ESTATE,
        "governing": [
            ("18-C §3-1204", "Small estates: closing by sworn statement of the PR."),
            ("18-C §3-1203", "Small-estate summary administrative procedure."),
        ],
        "per_question": [
            ("distributee_1_name", [("18-C §3-1204", "Each distributee who received estate property is listed.")]),
        ],
    },
    "DE-605": {
        "summary": "Verified application for certificate of discharge.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-1007", "Certificate discharging liens securing fiduciary performance."),
            ("18-C §3-1005", "Limitations on proceedings against the PR after closing."),
        ],
        "per_question": [
            ("sworn_statement_filed_date", [("18-C §3-1003", "A prior closing by sworn statement supports discharge.")]),
            ("court_order_closing_date", [("18-C §3-1001", "A formal settlement order may instead support discharge.")]),
        ],
    },
    "AF-102": {
        "summary": "Small estate affidavit for collection of personal property.",
        "transition": "Eligibility turns on the current §3-1201 threshold ($40,000, inflation-adjusted) and the 30-day wait — both raised/changed from former 18-A. " + T_ESTATE,
        "governing": [
            ("18-C §3-1201", "Collection of personal property by affidavit (small estate, $40,000 limit, 30 days after death)."),
            ("18-C §3-1202", "Effect of the affidavit; protection of the person paying/delivering."),
        ],
        "per_question": [
            ("date_of_death", [("18-C §3-1201", "At least 30 days must have elapsed since death before using the affidavit.")]),
            ("affiant_name", [("18-C §3-1201", "The affiant must be a successor entitled to the property.")]),
        ],
    },
    # ===================== AFFIDAVITS / FEE / NOTICE-SUPPORT ===============
    "AF-101": {
        "summary": "Jurisdictional affidavit (minor guardianship).",
        "transition": T_PROC,
        "governing": [
            ("18-C §5-104", "Subject-matter jurisdiction over guardianship/conservatorship."),
            ("18-C §5-106", "Venue."),
        ],
        "per_question": [
            ("jurisdictional_inquiries", [("18-C §5-104", "The affidavit establishes the court's jurisdiction over the minor."), ("19-A M.R.S.", "A pending or possible custody case can shift jurisdiction under the UCCJEA.")]),
            ("district_case_docket", [("19-A M.R.S.", "Identify competing family/custody matters that affect jurisdiction.")]),
        ],
        "cross_refs": ["19-A M.R.S."],
    },
    "AF-101.vA": {
        "summary": "Jurisdictional affidavit (minor guardianship) — variant A.",
        "transition": T_PROC,
        "governing": [
            ("18-C §5-104", "Subject-matter jurisdiction over guardianship/conservatorship."),
            ("18-C §5-106", "Venue."),
        ],
        "per_question": [
            ("oath_no_pending_other_state", [("19-A M.R.S.", "Out-of-state custody/guardianship proceedings can divest Maine of jurisdiction under the UCCJEA.")]),
            ("oath_no_transferred", [("18-C §5-105", "A transferred proceeding affects which court has jurisdiction.")]),
        ],
        "cross_refs": ["19-A M.R.S."],
    },
    "AF-103": {
        "summary": "Affidavit of name change for an adult.",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-701", "Process to change a name in the Probate Court."),
        ],
        "per_question": [
            ("new_name", [("18-C §1-701", "The requested name; courts deny changes sought for fraud/evasion.")]),
            ("minor_children_details", [("18-C §1-701", "Disclosure of minor children is part of the name-change record.")]),
        ],
    },
    "AF-104": {
        "summary": "Affidavit of diligent search (for notice to a person whose whereabouts are unknown).",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-401", "Notice requirements; this affidavit documents diligent efforts before alternative notice."),
        ],
        "per_question": [
            ("interested_party_name", [("18-C §1-401", "Identifies the person who must receive notice but cannot be located.")]),
            ("internet_inquiries", [("18-C §1-401", "Reasonable diligence (internet, family, government inquiries) supports notice by publication if needed.")]),
        ],
    },
    "AF-105": {
        "summary": "Indigency / financial affidavit (fee waiver).",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-602", "Filing and certification fees that an indigent applicant seeks to waive."),
            ("M.R. Prob. P.", "Fee-waiver practice is governed by the Probate Rules."),
        ],
        "per_question": [
            ("request_type", [("18-C §1-602", "The fee at issue is the statutory filing/certification fee.")]),
            ("total_living_expenses", [(None, "Income-vs-expenses showing supports indigency; no specific statute fixes the threshold — it is a court determination.")]),
        ],
        "cross_refs": ["M.R. Prob. P."],
    },
    # ===================== NOTICES (N-*) ==================================
    "N-105": {
        "summary": "Demand for notice of orders and filings.",
        "transition": T_PROC,
        "governing": [
            ("18-C §3-204", "Demand for notice in a decedent's estate."),
            ("18-C §5-116", "Request for notice in a guardianship/conservatorship matter."),
        ],
        "per_question": [
            ("interest_in_estate", [("18-C §3-204", "An interested person's demand entitles them to copies of subsequent orders/filings.")]),
        ],
    },
    "N-106": {
        "summary": "Notice of removal (of a proceeding).",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-306", "No jury trial in Probate Court; removal of issues triable by jury."),
        ],
        "per_question": [
            ("removal_basis", [("18-C §1-306", "Removal is grounded in the right to a jury trial on certain contested issues.")]),
        ],
    },
    "N-107": {
        "summary": "Waiver of notice.",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-402", "Waiver of notice by an interested person."),
            ("18-C §5-114", "Waiver of notice in guardianship/conservatorship matters."),
        ],
        "per_question": [
            ("waiver_type", [("18-C §1-402", "A competent interested person may waive notice in writing.")]),
        ],
    },
    "N-108": {
        "summary": "Waiver of notice on behalf of a minor or individual subject to guardianship/conservatorship.",
        "transition": T_PROC,
        "governing": [
            ("18-C §5-114", "Waiver of notice on behalf of a protected person."),
            ("18-C §1-403", "When one party's action binds others / representation."),
        ],
        "per_question": [
            ("fiduciary_role", [("18-C §5-114", "The fiduciary's authority to waive on the protected person's behalf must exist.")]),
        ],
    },
    "N-112": {
        "summary": "Notice of intent to register a guardianship or conservatorship.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-125", "Registration of a guardianship/conservatorship order and its effect."),
        ],
        "per_question": [
            ("appointing_court_name", [("18-C §5-125", "Registration is based on the order of the appointing (often out-of-state) court.")]),
        ],
    },
    "N-115": {
        "summary": "Notice of appointment of personal representative to heirs and devisees.",
        "transition": T_ESTATE,
        "governing": [
            ("18-C §3-705", "PR's duty to give information of appointment to heirs and devisees."),
            ("18-C §3-310", "Notice requirements following informal appointment."),
        ],
        "per_question": [
            ("pr_name", [("18-C §3-705", "Within 30 days of appointment the PR must inform heirs/devisees.")]),
            ("bond_status", [("18-C §3-603", "The notice states whether bond was required.")]),
        ],
    },
    "N-117": {
        "summary": "Notice of appointment of a guardian/conservator.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-311", "Notice of the order of appointment of a guardian, and rights."),
            ("18-C §5-412", "Notice of the order of appointment of a conservator, and rights."),
        ],
        "per_question": [
            ("appointment_type", [("18-C §5-311", "Guardian-appointment notice; "), ("18-C §5-412", "or conservator-appointment notice.")]),
        ],
    },
    "N-118": {
        "summary": "Notice of guardianship/conservatorship proceeding and post-appointment events.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-113", "Notice of hearing in a guardianship/conservatorship proceeding."),
            ("18-C §5-315", "Special limitations on a guardian's power (dwelling change, contact)."),
        ],
        "per_question": [
            ("change_in_permanent_dwelling", [("18-C §5-315", "Moving the protected person's dwelling triggers notice/limits on guardian power.")]),
            ("restrictions_on_contact", [("18-C §5-315", "Restricting contact with others is a specially limited guardian power requiring notice.")]),
            ("revised_guardianship_plan_filed", [("18-C §5-316", "A revised guardian's plan must be filed/approved.")]),
            ("conservators_report_and_accounting", [("18-C §5-423", "Conservator's report and accounting is filed and noticed.")]),
        ],
    },
    # ===================== GUARDIANSHIP / CONSERVATORSHIP (PP-*, GS-*) ====
    "PP-201": {
        "summary": "Petition for appointment of guardian for an adult.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-301", "Basis for appointing a guardian for an adult."),
            ("18-C §5-302", "Contents of the petition."),
            ("18-C §5-303", "Notice and hearing."),
            ("18-C §5-309", "Who may be guardian; priorities."),
            ("18-C §5-312", "Emergency guardian where harm is imminent."),
        ],
        "per_question": [
            ("respondent_need_description", [("18-C §5-301", "Must show the adult cannot meet essential needs even with supports/less-restrictive alternatives.")]),
            ("guardianship_scope", [("18-C §5-315", "Guardianship must be the least restrictive option; full guardianship needs justification."), ("18-C §5-314", "Defines the powers a guardian may exercise.")]),
            ("nominee_relationship", [("18-C §5-309", "Priority ordering for who is appointed guardian.")]),
            ("emergency_guardian_requested", [("18-C §5-312", "Emergency appointment requires a showing of likely substantial harm.")]),
            ("respondent_attorney", [("18-C §5-305", "The adult has a right to counsel.")]),
            ("persons_to_notify", [("18-C §5-303", "Statutorily defined interested persons must receive notice.")]),
            ("nominee_bankruptcy", [("18-C §5-117", "Nominee must disclose bankruptcy/criminal history.")]),
            ("nominee_conviction", [("18-C §5-117", "Nominee must disclose criminal history.")]),
        ],
    },
    "PP-203": {
        "summary": "Acceptance of appointment by a guardian.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-108", "Letters of office issue upon a qualified acceptance."),
            ("18-C §5-109", "Effect of accepting appointment (submission to court authority)."),
        ],
        "per_question": [
            ("guardian_signature", [("18-C §5-109", "Acceptance subjects the guardian to the court's jurisdiction and duties.")]),
        ],
    },
    "PP-205": {
        "summary": "Joined petition for appointment of guardian and conservator (adult).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-301", "Basis for adult guardianship."),
            ("18-C §5-401", "Basis for conservatorship."),
            ("18-C §5-302", "Guardianship petition contents."),
            ("18-C §5-402", "Conservatorship petition contents."),
        ],
        "per_question": [
            ("guardianship_scope", [("18-C §5-315", "Least-restrictive principle applies to the guardianship sought.")]),
            ("conservatorship_scope", [("18-C §5-419", "A conservator's plan must match the scope sought."), ("18-C §5-414", "Powers needing court approval.")]),
            ("guardianship_need_description", [("18-C §5-301", "Need showing for guardianship.")]),
            ("conservatorship_need_description", [("18-C §5-401", "Need showing for conservatorship (manage property/business affairs).")]),
            ("nominee_bankruptcy", [("18-C §5-117", "Disclosure of bankruptcy/criminal history.")]),
        ],
    },
    "PP-207": {
        "summary": "Acceptance of appointment by a guardian and conservator.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-108", "Letters of office."),
            ("18-C §5-109", "Effect of acceptance."),
        ],
        "per_question": [
            ("guardian_signature", [("18-C §5-109", "Acceptance binds the fiduciary to the court's authority and statutory duties.")]),
        ],
    },
    "PP-209": {
        "summary": "Interim and annual report of guardian (adult).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-317", "Guardian's report and monitoring of the guardianship."),
            ("18-C §5-313", "Duties of a guardian for an adult."),
        ],
        "per_question": [
            ("supported_decision_making_services", [("18-C §5-313", "Guardian must encourage the adult's participation and self-reliance.")]),
            ("recommendation_for_continued_guardianship", [("18-C §5-317", "Report addresses whether guardianship should continue/be modified."), ("18-C §5-319", "Termination/modification standard.")]),
            ("guardian_fees", [("18-C §5-119", "Compensation and expenses of the guardian.")]),
        ],
    },
    "PP-210": {
        "summary": "Registration of guardianship or conservatorship.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-125", "Registration of an order and its effect."),
        ],
        "per_question": [
            ("registration_type", [("18-C §5-125", "Registration lets a fiduciary exercise authority in Maine based on the original order.")]),
            ("appointing_court_info", [("18-C §5-632", "If transferring from another state, the accepting-transfer rules may also apply.")]),
        ],
    },
    "PP-401": {
        "summary": "Petition for appointment of conservator (adult).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-401", "Basis for appointing a conservator."),
            ("18-C §5-402", "Petition contents."),
            ("18-C §5-403", "Notice and hearing."),
            ("18-C §5-410", "Who may be conservator; priorities."),
            ("18-C §5-413", "Emergency conservator."),
            ("18-C §5-416", "Bond or alternative asset-protection arrangement."),
        ],
        "per_question": [
            ("respondent_need_description", [("18-C §5-401", "Show inability to manage property/financial affairs and a need to protect assets.")]),
            ("conservatorship_type", [("18-C §5-414", "Powers requiring court approval define scope."), ("18-C §5-419", "Conservator's plan.")]),
            ("incapacity_basis", [("18-C §5-401", "Clinical/functional basis for the protective need.")]),
            ("emergency_conservator", [("18-C §5-413", "Emergency conservator requires imminent risk to property.")]),
            ("nominee_relationship", [("18-C §5-410", "Priority ordering for conservator.")]),
            ("respondent_property", [("18-C §5-420", "Identified property frames the inventory the conservator must file.")]),
            ("nominee_bankruptcy", [("18-C §5-117", "Disclosure of bankruptcy/criminal history.")]),
        ],
    },
    "PP-402": {
        "summary": "Acceptance of appointment by a conservator.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-108", "Letters of office."),
            ("18-C §5-109", "Effect of acceptance."),
        ],
        "per_question": [
            ("by_signature", [("18-C §5-109", "Acceptance subjects the conservator to the court's authority and duties.")]),
        ],
    },
    "PP-405": {
        "summary": "Bond for conservator (with sureties).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-416", "Bond or alternative asset-protection arrangement for a conservator."),
            ("18-C §5-417", "Terms and requirements of the bond."),
            ("18-C §8-204", "Approval of the bond; surety sufficiency."),
        ],
        "per_question": [
            ("penal_sum_numeric", [("18-C §5-417", "The penal sum is set by reference to the conservatorship estate value/income.")]),
            ("corporate_surety_name", [("18-C §8-208", "Corporate-surety liability reduction.")]),
        ],
    },
    "PP-406": {
        "summary": "Inventory (conservatorship).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-420", "Conservator's inventory and records."),
        ],
        "per_question": [
            ("calc_net_inventory", [("18-C §5-420", "Net protected estate (gross less encumbrances) is reported.")]),
            ("appraisers_info", [("18-C §5-420", "Basis of valuation/appraisal for the protected estate.")]),
        ],
    },
    "PP-407": {
        "summary": "Conservator account.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-423", "Conservator's report and accounting; court monitoring."),
            ("18-C §5-422", "Distributions from the conservatorship estate."),
        ],
        "per_question": [
            ("distributions_amount", [("18-C §5-422", "Distributions must be authorized for the protected person's benefit.")]),
            ("expenses_amount", [("18-C §5-418", "Expenditures must fit the conservator's duties/plan.")]),
        ],
    },
    "PP-408": {
        "summary": "Claim against the (conservatorship) estate.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-428", "Presentation and allowance of a claim against a conservatorship estate."),
        ],
        "per_question": [
            ("basis_for_claim", [("18-C §5-428", "The claim must be presented per the statutory process.")]),
            ("decision_status", [("18-C §5-428", "The conservator allows or disallows the claim.")]),
        ],
    },
    "PP-409": {
        "summary": "Petition to resolve a disputed claim / petition for allowance (conservatorship).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-428", "Court resolution of a disputed claim against the conservatorship estate."),
        ],
        "per_question": [
            ("factual_legal_issues_in_dispute", [("18-C §5-428", "Frames the disputed claim for the court's allowance decision.")]),
        ],
    },
    "PP-410": {
        "summary": "Petition for interim order (conservatorship/guardianship).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-415", "Petition for an order subsequent to appointment."),
            ("18-C §5-124", "Temporary substitute guardian/conservator."),
        ],
        "per_question": [
            ("interim_order_relief", [("18-C §5-415", "Interim/subsequent orders address needs arising after appointment.")]),
            ("appointment_required", [("18-C §5-413", "If urgent protection is needed, an emergency/temporary appointment may be sought.")]),
        ],
    },
    "PP-412": {
        "summary": "Conservator's report.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-423", "Conservator's report and accounting; monitoring."),
            ("18-C §5-418", "Duties of the conservator the report documents."),
        ],
        "per_question": [
            ("services_provided", [("18-C §5-418", "Report documents how the conservator discharged statutory duties.")]),
            ("business_relation_provider", [("18-C §5-425", "Conflict-of-interest transactions must be disclosed/justified.")]),
        ],
    },
    "PP-413": {
        "summary": "Petition for termination, removal or resignation (guardian/conservator).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-318", "Removal of a guardian and appointment of a successor."),
            ("18-C §5-319", "Termination or modification of an adult guardianship."),
            ("18-C §5-430", "Removal of a conservator and successor appointment."),
            ("18-C §5-431", "Termination or modification of a conservatorship."),
            ("18-C §5-112", "Effect of death, removal or resignation of a fiduciary."),
        ],
        "per_question": [
            ("belief_reason_removal", [("18-C §5-318", "Removal of guardian for cause; "), ("18-C §5-430", "or removal of conservator for cause.")]),
            ("belief_reason_termination", [("18-C §5-319", "Termination when the basis for guardianship no longer exists; "), ("18-C §5-431", "or for conservatorship.")]),
            ("belief_reason_resignation", [("18-C §5-112", "Resignation takes effect on court acceptance and successor arrangements.")]),
            ("visitor_required", [("18-C §5-304", "A visitor may be appointed to investigate; "), ("18-C §5-405", "conservatorship counterpart.")]),
        ],
    },
    "PP-502": {
        "summary": "Guardianship plan — adult.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-316", "Guardian's plan for the adult's care and decision-making."),
        ],
        "per_question": [
            ("living_arrangement", [("18-C §5-316", "Plan must address the adult's living arrangement and least-restrictive setting.")]),
            ("goals_restoration_rights", [("18-C §5-319", "Plan should orient toward restoring rights where possible.")]),
        ],
    },
    "PP-503": {
        "summary": "Conservator plan.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-419", "Conservator's plan for managing the protected estate."),
        ],
        "per_question": [
            ("budget_expenses", [("18-C §5-419", "Plan sets the budget for the protected person's needs.")]),
            ("restore_ability_steps", [("18-C §5-431", "Plan should consider steps toward restoring the person's management ability.")]),
        ],
    },
    "PP-504": {
        "summary": "Joined plan (guardianship + conservatorship).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-316", "Guardian's plan."),
            ("18-C §5-419", "Conservator's plan."),
        ],
        "per_question": [
            ("living_arrangement_services", [("18-C §5-316", "Care/living plan for the guardianship component.")]),
            ("conservator_budget_charges", [("18-C §5-419", "Budget for the conservatorship component.")]),
        ],
    },
    "PP-505": {
        "summary": "Physician's or psychologist's report.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-306", "Professional evaluation in a guardianship proceeding."),
            ("18-C §5-407", "Professional evaluation in a conservatorship proceeding."),
        ],
        "per_question": [
            ("cognitive_functional_abilities", [("18-C §5-306", "Evaluation of functional ability is central to the need determination.")]),
            ("conservator_appointment_opinion", [("18-C §5-407", "Opinion supports/contradicts the conservatorship basis.")]),
        ],
    },
    "PP-506": {
        "summary": "Visitor's report.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-304", "Visitor's role/report in a guardianship proceeding."),
            ("18-C §5-405", "Visitor's role/report in a conservatorship proceeding."),
        ],
        "per_question": [
            ("recommend_attorney", [("18-C §5-305", "Visitor may recommend appointment of counsel for the adult.")]),
            ("conservatorship_appropriateness", [("18-C §5-405", "Visitor assesses whether conservatorship/less-restrictive options fit.")]),
        ],
    },
    "PP-507": {
        "summary": "Affidavit for emergency guardian and/or conservator.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-312", "Emergency guardian standard and procedure."),
            ("18-C §5-413", "Emergency conservator standard and procedure."),
        ],
        "per_question": [
            ("circumstances_of_harm", [("18-C §5-312", "Must show likelihood of substantial harm without immediate appointment."), ("18-C §5-413", "Property-risk counterpart for an emergency conservator.")]),
            ("requested_powers", [("18-C §5-312", "Emergency powers are limited to those needed to prevent the harm.")]),
            ("no_notice_name_1", [("18-C §5-312", "Notice may be abbreviated/deferred only as the emergency statute allows.")]),
        ],
    },
    "PP-509": {
        "summary": "Petition to accept transfer of guardianship/conservatorship from another state.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-632", "Accepting a guardianship/conservatorship transferred from another state."),
        ],
        "per_question": [
            ("transferring_state", [("18-C §5-632", "Maine's acceptance follows the sending state's provisional transfer order.")]),
        ],
    },
    "PP-510": {
        "summary": "Petition to transfer guardianship/conservatorship to another state (and provisional order).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-631", "Transfer of a guardianship/conservatorship to another state."),
        ],
        "per_question": [
            ("transfer_destination_state", [("18-C §5-631", "Transfer requires that the receiving state be the better forum.")]),
            ("individual_move_permanently", [("18-C §5-631", "A permanent move supports transfer to the new state.")]),
        ],
    },
    "PP-601": {
        "summary": "Petition for other protective arrangements.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-501", "Authority for protective arrangements."),
            ("18-C §5-502", "Protective arrangement instead of guardianship for an adult."),
            ("18-C §5-503", "Protective arrangement instead of conservatorship."),
            ("18-C §5-504", "Petition contents."),
        ],
        "per_question": [
            ("arrangement_nature", [("18-C §5-501", "A single transaction/arrangement can avoid full guardianship/conservatorship.")]),
            ("less_restrictive_alternatives", [("18-C §5-502", "Protective arrangements are favored as less restrictive than guardianship."), ("18-C §5-503", "Conservatorship counterpart.")]),
        ],
    },
    "PP-107": {
        "summary": "Petition for appointment of conservator of a minor.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-401", "Basis for appointing a conservator (applies to a minor's property)."),
            ("18-C §5-402", "Petition contents."),
            ("18-C §5-410", "Who may be conservator; priorities."),
            ("18-C §5-413", "Emergency conservator."),
        ],
        "per_question": [
            ("minor_need_description", [("18-C §5-401", "Need to manage/protect the minor's property (e.g. inheritance, settlement).")]),
            ("conservatorship_scope", [("18-C §5-414", "Powers needing court approval frame the scope.")]),
            ("nominee_bankruptcy", [("18-C §5-117", "Disclosure of bankruptcy/criminal history.")]),
        ],
    },
    "PP-108": {
        "summary": "Acceptance of appointment by conservator — minor.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-108", "Letters of office."),
            ("18-C §5-109", "Effect of acceptance."),
        ],
        "per_question": [
            ("conservator_signature", [("18-C §5-109", "Acceptance subjects the conservator to the court's authority and duties.")]),
        ],
    },
    "GS-008": {
        "summary": "Acceptance of appointment by guardian (minor).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-108", "Letters of office."),
            ("18-C §5-109", "Effect of acceptance."),
            ("18-C §5-201", "Appointment and status of a guardian of a minor."),
        ],
        "per_question": [
            ("appointment_type", [("18-C §5-201", "Parental vs judicial appointment affects status/duration."), ("18-C §5-202", "Parental appointment of a guardian.")]),
        ],
    },
    "GS-008.vA": {
        "summary": "Acceptance of appointment by guardian (minor) — variant A.",
        "transition": T_GC,
        "governing": [
            ("18-C §5-108", "Letters of office."),
            ("18-C §5-109", "Effect of acceptance."),
            ("18-C §5-201", "Appointment and status of a guardian of a minor."),
        ],
        "per_question": [
            ("appointment_type", [("18-C §5-201", "Parental vs judicial appointment affects status/duration.")]),
        ],
    },
    "GS-014": {
        "summary": "Status report of the guardian (minor).",
        "transition": T_GC,
        "governing": [
            ("18-C §5-207", "Duties of a guardian of a minor."),
            ("18-C §5-210", "Modification or termination of a minor guardianship; review."),
        ],
        "per_question": [
            ("conclusions_recommendations", [("18-C §5-210", "Report supports the court's periodic review of the minor guardianship.")]),
            ("parent_contact_details", [("18-C §5-207", "Guardian's duties include facilitating appropriate parental contact.")]),
        ],
    },
    "PB-007": {
        "summary": "Guardian ad litem joint appointment order (minor).",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-111", "Guardian ad litem appointment authority."),
            ("18-C §5-115", "Guardian ad litem in guardianship/conservatorship proceedings."),
        ],
        "per_question": [
            ("good_cause_findings", [("18-C §1-111", "The order's good-cause basis for appointing a GAL."), ("18-C §5-212", "GAL for a minor in a guardianship context.")]),
            ("appointment_factors", [("18-C §1-111", "Scope of the GAL's appointment and duties.")]),
        ],
    },
    "PB-007.vA": {
        "summary": "Guardian ad litem joint appointment order (minor) — variant A.",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-111", "Guardian ad litem appointment authority."),
            ("18-C §5-115", "Guardian ad litem in guardianship/conservatorship proceedings."),
        ],
        "per_question": [
            ("good_cause_findings", [("18-C §1-111", "The order's good-cause basis for appointing a GAL.")]),
        ],
    },
    # ===================== ADOPTION (AD-*) ================================
    "AD-007": {
        "summary": "Confidential statement (adoption).",
        "transition": T_ADOPT,
        "governing": [
            ("18-C §9-304", "Investigation and registry in an adoption."),
            ("18-C §9-310", "Adoption records are confidential."),
        ],
        "per_question": [
            ("adoptee_name", [("18-C §9-310", "Identifying information is filed under confidentiality protections.")]),
        ],
        "cross_refs": ["22 M.R.S."],
    },
    "AD-008": {
        "summary": "Report of disbursements (adoption).",
        "transition": T_ADOPT,
        "governing": [
            ("18-C §9-306", "Allowable payments and expenses in connection with an adoption."),
        ],
        "per_question": [
            ("disbursements", [("18-C §9-306", "Only statutorily allowable adoption-related payments may be reported/made.")]),
            ("living_expenses_details", [("18-C §9-306", "Birth-parent living expenses are allowable only within statutory limits.")]),
        ],
    },
    "AD-009": {
        "summary": "Certificate of counseling (adoption).",
        "transition": T_ADOPT,
        "governing": [
            ("18-C §9-202", "Surrender and release / consent — counseling precedes a valid surrender."),
        ],
        "per_question": [
            ("counseling_topic", [("18-C §9-202", "Counseling supports a knowing, voluntary surrender of parental rights.")]),
        ],
    },
    "AD-011": {
        "summary": "Petition to recognize a foreign adoption.",
        "transition": T_ADOPT,
        "governing": [
            ("18-C §9-312", "Foreign adoptions and their recognition."),
        ],
        "per_question": [
            ("date_of_decree", [("18-C §9-312", "A valid foreign decree is the basis for recognition.")]),
            ("change_of_name_requested", [("18-C §9-301", "Name change may be requested as part of the adoption/recognition.")]),
        ],
        "cross_refs": ["22 M.R.S."],
    },
    "AD-026": {
        "summary": "Petition for adult adoption.",
        "transition": T_ADOPT,
        "governing": [
            ("18-C §9-301", "Petition for adoption and change of name."),
            ("18-C §9-302", "Consent for adoption."),
            ("18-C §9-308", "Final decree and effect of adoption."),
        ],
        "per_question": [
            ("consent_required", [("18-C §9-302", "Adult adoption requires the adoptee's consent (and possibly a spouse's).")]),
            ("inheritance_acknowledgment", [("18-C §9-105", "Adoption changes inheritance rights."), ("18-C §2-117", "Effect of an adoption order on inheritance from/through former and adoptive parents.")]),
            ("birth_parents_inheritance_request", [("18-C §2-117", "Whether birth-parent inheritance is preserved is governed by §2-117.")]),
        ],
    },
    "AD-028": {
        "summary": "Affidavit of parentage (adoption).",
        "transition": T_ADOPT,
        "governing": [
            ("18-C §9-201", "Determination of parentage."),
        ],
        "per_question": [
            ("putative_parent_name", [("18-C §9-201", "Identifies a putative parent whose rights must be addressed."), ("18-C §2-115", "Parentage determination also affects intestate succession.")]),
        ],
    },
    # ===================== NAME CHANGE ====================================
    "CN-1": {
        "summary": "Name change petition (adult).",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-701", "Process to change a name in the Probate Court."),
        ],
        "per_question": [
            ("reasons_for_change", [("18-C §1-701", "Courts grant a change absent fraud/evasion."), ("18-C §1-105", "Fraud/evasion bars relief.")]),
            ("request_confidential_order", [("18-C §1-701", "Confidentiality may be ordered for safety reasons.")]),
            ("request_no_notification", [("18-C §1-701", "Notice to others may be dispensed with for good cause (e.g. safety).")]),
        ],
    },
    "NC-001": {
        "summary": "Petition for name change of a minor.",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-701", "Process to change a name in the Probate Court."),
        ],
        "per_question": [
            ("reason_for_change", [("18-C §1-701", "Best-interest considerations frame a minor's name change."), ("19-A M.R.S.", "Both parents'/custodians' positions matter; a pending custody case can affect the court's approach.")]),
        ],
        "cross_refs": ["19-A M.R.S."],
    },
    # ===================== APPEALS / MISC =================================
    "APP-1": {
        "summary": "Notice of appeal to the Law Court.",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-308", "Appeals from the Probate Court."),
            ("M.R. Prob. P.", "Appellate timing/procedure is set by rule (and the Maine Rules of Appellate Procedure)."),
        ],
        "per_question": [
            ("judgment_date", [("18-C §1-308", "The appeal must be filed within the statutory/rule period running from the judgment.")]),
        ],
        "cross_refs": ["M.R. Prob. P."],
    },
    "APP-2": {
        "summary": "Transcript order (appeal).",
        "transition": T_PROC,
        "governing": [
            ("18-C §1-308", "Appeals; the record on appeal includes the transcript."),
            ("M.R. Prob. P.", "Transcript ordering follows the appellate rules."),
        ],
        "per_question": [
            ("hearing_1_date", [("18-C §1-308", "Identify the proceedings to be transcribed for the appellate record.")]),
        ],
        "cross_refs": ["M.R. Prob. P."],
    },
    "MISC-101": {
        "summary": "Motion form (general).",
        "transition": T_PROC,
        "governing": [
            ("M.R. Prob. P.", "Motion practice in the Probate Court is governed by the rules."),
            ("18-C §1-302", "The court's subject-matter jurisdiction underlies the relief sought."),
        ],
        "per_question": [
            ("motion_for", [(None, "Relief sought should tie to a specific statutory basis in the underlying matter (cite that section in the motion).")]),
            ("certificate_of_service_name", [("M.R. Prob. P.", "Service on interested persons is required.")]),
        ],
        "cross_refs": ["M.R. Prob. P."],
    },
    "MISC-102": {
        "summary": "Witness subpoena.",
        "transition": T_PROC,
        "governing": [
            ("M.R. Prob. P.", "Subpoena power/practice follows the Probate (and incorporated civil) rules."),
            ("18-C §1-302", "Issuance presupposes a matter within the court's jurisdiction."),
        ],
        "per_question": [
            ("produce_designated_things", [("M.R. Prob. P.", "A subpoena duces tecum must reasonably describe the items and allow objection.")]),
            ("objection_notice_date", [("M.R. Prob. P.", "The recipient's objection deadline is set by rule.")]),
        ],
        "cross_refs": ["M.R. Prob. P."],
    },
}


def load_index() -> tuple[dict, dict]:
    sec = json.loads((IDX / "18c-sections.json").read_text(encoding="utf-8"))["sections"]
    xref = json.loads((IDX / "cross-refs.json").read_text(encoding="utf-8"))["cross_refs"]
    return sec, xref


def load_caselaw() -> dict:
    return json.loads((IDX / "caselaw.json").read_text(encoding="utf-8"))["cases"]


def cases_for_form(sidecar: dict, caselaw: dict) -> list[dict]:
    """Attach a case to a form when a statute the case bears on is among the
    statutes the form cites. This keeps the case<->form tie statute-driven
    (non-hallucinated) rather than hand-asserted."""
    form_cites = set()
    for g in sidecar.get("governing", []):
        form_cites.add(g["cite"])
    for pq in sidecar.get("per_question", []):
        for c in pq.get("considerations", []):
            if c.get("cite"):
                form_cites.add(c["cite"])
    for x in sidecar.get("cross_refs", []):
        form_cites.add(x["cite"])
    out = []
    for case_id, case in sorted(caselaw.items()):
        shared = sorted(set(case.get("statutes", [])) & form_cites)
        if shared:
            out.append({
                "name": case["name"],
                "cite": case["cite"],
                "year": case["year"],
                "url": case["url"],
                "topic": case["topic"],
                "holding": case["holding"],
                "via": shared,
                "decided_under": case.get("decided_under"),
                "holding_source": case.get("holding_source"),
            })
    return out


def resolve_title(cite: str, sec: dict, xref: dict) -> str | None:
    """Return the authoritative title for a cite, or None if unknown."""
    if cite in xref:
        return xref[cite]["title"]
    if cite.startswith("18-C §"):
        key = cite[len("18-C §"):]
        if key in sec:
            return sec[key]["title"]
    return None


def resolve_url(cite: str, sec: dict, xref: dict) -> str | None:
    if cite in xref:
        return xref[cite].get("url")
    if cite.startswith("18-C §"):
        key = cite[len("18-C §"):]
        if key in sec:
            return sec[key]["url"]
    return None


def schema_field_ids(form_id: str) -> set[str]:
    sp = FORMS_DIR / form_id / "schema.json"
    data = json.loads(sp.read_text(encoding="utf-8"))
    return {f.get("field_id") or f.get("id") for f in (data.get("fields") or [])}


def build_sidecar(form_id: str, spec: dict, sec: dict, xref: dict, errors: list[str]) -> dict:
    field_ids = schema_field_ids(form_id)
    out: dict = {
        "form_id": form_id,
        "summary": spec["summary"],
        "applies": "Title 18-C (Maine Uniform Probate Code), effective 2019-09-01 — see transition_18a.",
        "governing": [],
        "per_question": [],
        "transition_18a": spec["transition"],
        "cross_refs": [],
        "caselaw": [],
        "disclaimer": DISCLAIMER,
        "source": "Authored from docs/statute-reference/_index/18c-sections.json (verbatim from legislature.maine.gov).",
    }
    for cite, why in spec.get("governing", []):
        title = resolve_title(cite, sec, xref)
        if title is None:
            errors.append(f"{form_id}: governing cite not in index/xref: {cite}")
            continue
        out["governing"].append({"cite": cite, "title": title, "url": resolve_url(cite, sec, xref), "why": why})
    for field_id, considerations in spec.get("per_question", []):
        if field_id not in field_ids:
            errors.append(f"{form_id}: per_question field_id not in schema: {field_id}")
            continue
        items = []
        for cite, note in considerations:
            item: dict = {"note": note}
            if cite is not None:
                title = resolve_title(cite, sec, xref)
                if title is None:
                    errors.append(f"{form_id}: per_question cite not in index/xref: {cite} (field {field_id})")
                    continue
                item = {"cite": cite, "title": title, "url": resolve_url(cite, sec, xref), "note": note}
            items.append(item)
        out["per_question"].append({"field_id": field_id, "considerations": items})
    for cite in spec.get("cross_refs", []):
        title = resolve_title(cite, sec, xref)
        if title is None:
            errors.append(f"{form_id}: cross_ref cite not in index/xref: {cite}")
            continue
        out["cross_refs"].append({"cite": cite, "title": title, "url": resolve_url(cite, sec, xref)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only; write nothing")
    args = ap.parse_args()

    sec, xref = load_index()
    caselaw = load_caselaw()
    all_forms = sorted([d.name for d in FORMS_DIR.iterdir() if d.is_dir()])
    missing = [f for f in all_forms if f not in FORMS]
    if missing:
        print(f"ERROR: {len(missing)} forms have no curated entry: {', '.join(missing)}", file=sys.stderr)

    errors: list[str] = []
    # Validate every statute a case is tied to resolves to the trusted index.
    for case_id, case in caselaw.items():
        for cite in case.get("statutes", []):
            if resolve_title(cite, sec, xref) is None:
                errors.append(f"caselaw {case_id}: statute cite not in index/xref: {cite}")

    written = 0
    for form_id in all_forms:
        if form_id not in FORMS:
            continue
        sidecar = build_sidecar(form_id, FORMS[form_id], sec, xref, errors)
        sidecar["caselaw"] = cases_for_form(sidecar, caselaw)
        if not args.check:
            (FORMS_DIR / form_id / "statutes.json").write_text(
                json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            written += 1

    if errors:
        print(f"\n{len(errors)} VALIDATION ERROR(S):", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)

    if args.check:
        print(f"OK: {len(FORMS)} curated forms validate (cites resolve, field_ids exist).")
    else:
        print(f"wrote {written} statutes.json sidecars; all cites + field_ids valid.")
    if missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
