# Form DE-101(I)

## county_probate_court
- type: `text`

## docket_number
- type: `text`

## decedent_name_caption
- type: `text`
- prompt: Estate of (Decedent name in case caption)

## applicant_full_name
- type: `text`

## applicant_contact_info
- type: `text`

## applicant_legal_interest
- type: `select_many`
- prompt: Legal interest of Applicant in Estate (Check all that apply)
- options:
  - `surviving_spouse` — Surviving spouse
  - `domestic_partner` — Domestic partner
  - `heir` — Heir (e.g. child, parent, etc.)
  - `creditor` — Creditor
  - `other` — Other

## applicant_legal_interest_other
- type: `text`
- when: `applicant_legal_interest == 'other'`

## personal_representative_name_address
- type: `text`

## personal_representative_relationship
- type: `select_one`
- prompt: Relationship to Decedent (Check one)
- options:
  - `surviving_spouse` — Surviving spouse
  - `domestic_partner` — Domestic partner
  - `other_heir` — Other heir (e.g. child, parent, sibling, etc.)
  - `creditor` — Creditor
  - `state_tax_assessor` — State tax assessor

## prior_equal_right_explanation
- type: `text`

## decedent_full_name
- type: `text`

## decedent_date_of_death
- type: `date`

## decedent_date_of_birth
- type: `text`

## decedent_domicile
- type: `text`
- prompt: Decedent's domicile (city and state only, e.g. "Portland, Maine"). Do NOT include street address — the Decedent's home street address belongs in Item 4 (Address of Decedent), not here.

## heir_1_name
- type: `text`
- prompt: Heir 1 — Name

## heir_1_address
- type: `text`
- prompt: Heir 1 — Address

## heir_1_dob
- type: `date`
- prompt: Heir 1 — Date of Birth (if under 18; otherwise leave blank)

## heir_1_relationship
- type: `text`
- prompt: Heir 1 — Relationship to Decedent (e.g. spouse, child, parent)

## heir_2_name
- type: `text`
- prompt: Heir 2 — Name

## heir_2_address
- type: `text`
- prompt: Heir 2 — Address

## heir_2_dob
- type: `date`
- prompt: Heir 2 — Date of Birth (if under 18; otherwise leave blank)

## heir_2_relationship
- type: `text`
- prompt: Heir 2 — Relationship to Decedent

## heir_3_name
- type: `text`
- prompt: Heir 3 — Name

## heir_3_address
- type: `text`
- prompt: Heir 3 — Address

## heir_3_dob
- type: `date`
- prompt: Heir 3 — Date of Birth (if under 18; otherwise leave blank)

## heir_3_relationship
- type: `text`
- prompt: Heir 3 — Relationship to Decedent

## heir_4_name
- type: `text`
- prompt: Heir 4 — Name

## heir_4_address
- type: `text`
- prompt: Heir 4 — Address

## heir_4_dob
- type: `date`
- prompt: Heir 4 — Date of Birth (if under 18; otherwise leave blank)

## heir_4_relationship
- type: `text`
- prompt: Heir 4 — Relationship to Decedent

## heir_5_name
- type: `text`
- prompt: Heir 5 — Name

## heir_5_address
- type: `text`
- prompt: Heir 5 — Address

## heir_5_dob
- type: `date`
- prompt: Heir 5 — Date of Birth (if under 18; otherwise leave blank)

## heir_5_relationship
- type: `text`
- prompt: Heir 5 — Relationship to Decedent

## heir_6_name
- type: `text`
- prompt: Heir 6 — Name

## heir_6_address
- type: `text`
- prompt: Heir 6 — Address

## heir_6_dob
- type: `date`
- prompt: Heir 6 — Date of Birth (if under 18; otherwise leave blank)

## heir_6_relationship
- type: `text`
- prompt: Heir 6 — Relationship to Decedent

## heir_7_name
- type: `text`
- prompt: Heir 7 — Name

## heir_7_address
- type: `text`
- prompt: Heir 7 — Address

## heir_7_dob
- type: `date`
- prompt: Heir 7 — Date of Birth (if under 18; otherwise leave blank)

## heir_7_relationship
- type: `text`
- prompt: Heir 7 — Relationship to Decedent

## heir_8_name
- type: `text`
- prompt: Heir 8 — Name

## heir_8_address
- type: `text`
- prompt: Heir 8 — Address

## heir_8_dob
- type: `date`
- prompt: Heir 8 — Date of Birth (if under 18; otherwise leave blank)

## heir_8_relationship
- type: `text`
- prompt: Heir 8 — Relationship to Decedent

## heir_9_name
- type: `text`
- prompt: Heir 9 — Name

## heir_9_address
- type: `text`
- prompt: Heir 9 — Address

## heir_9_dob
- type: `date`
- prompt: Heir 9 — Date of Birth (if under 18; otherwise leave blank)

## heir_9_relationship
- type: `text`
- prompt: Heir 9 — Relationship to Decedent

## non_registered_domestic_partner
- type: `select_one`
- prompt: Is there a domestic partner (non-registered)?
- options:
  - `yes` — YES
  - `no` — NO

## non_registered_domestic_partner_details
- type: `text`
- when: `non_registered_domestic_partner == 'yes'`

## real_estate_in_maine
- type: `select_one`
- prompt: Does the probate estate contain real estate in Maine?
- options:
  - `yes` — YES
  - `no` — NO

## real_estate_details
- type: `text`
- when: `real_estate_in_maine == 'yes'`

## domiciled_outside_maine
- type: `select_one`
- prompt: Was Decedent domiciled outside of Maine at date of death?
- options:
  - `yes` — YES
  - `no` — NO

## outside_maine_property_details
- type: `text`
- when: `domiciled_outside_maine == 'yes'`

## prior_personal_representative
- type: `select_one`
- prompt: Has a personal representative been appointed by any court prior to this date?
- options:
  - `yes` — YES
  - `no` — NO

## prior_personal_representative_details
- type: `text`
- when: `prior_personal_representative == 'yes'`

## died_more_than_3_years
- type: `select_one`
- prompt: Did Decedent die more than three (3) years before the date of this application?
- options:
  - `yes` — YES
  - `no` — NO

## died_more_than_3_years_circumstances
- type: `text`
- when: `died_more_than_3_years == 'yes'`

## demand_for_notice
- type: `select_one`
- prompt: Has the Applicant received a demand for notice or is aware of any demand?
- options:
  - `yes` — YES
  - `no` — NO

## demand_for_notice_1_name
- type: `text`
- prompt: Demand for Notice 1 — Name
- when: `demand_for_notice == 'yes'`

## demand_for_notice_1_address
- type: `text`
- prompt: Demand for Notice 1 — Address
- when: `demand_for_notice == 'yes'`

## demand_for_notice_2_name
- type: `text`
- prompt: Demand for Notice 2 — Name
- when: `demand_for_notice == 'yes'`

## demand_for_notice_2_address
- type: `text`
- prompt: Demand for Notice 2 — Address
- when: `demand_for_notice == 'yes'`

## demand_for_notice_3_name
- type: `text`
- prompt: Demand for Notice 3 — Name
- when: `demand_for_notice == 'yes'`

## demand_for_notice_3_address
- type: `text`
- prompt: Demand for Notice 3 — Address
- when: `demand_for_notice == 'yes'`

## demand_for_notice_4_name
- type: `text`
- prompt: Demand for Notice 4 — Name
- when: `demand_for_notice == 'yes'`

## demand_for_notice_4_address
- type: `text`
- prompt: Demand for Notice 4 — Address
- when: `demand_for_notice == 'yes'`

## request_register_service_notices
- type: `enabler`
- label: Request that the Register serve notices on Applicant's behalf

## request_publish_notice_creditors
- type: `enabler`
- label: Request the Register to publish notice to creditors

## bond_requirement
- type: `select_one`
- prompt: Bond Requirement (Check one)
- options:
  - `no_bond` — No bond is required
  - `personal_rep_bond` — A personal representative’s bond is required and is attached
  - `estate_tax_bond` — An estate tax bond is required and is attached

## testamentary_instrument
- type: `select_one`
- prompt: Testamentary Instrument (Check (a) or (b))
- options:
  - `known_unrevoked` — (a) I know of an unrevoked testamentary instrument...
  - `unaware` — (b) After exercise of reasonable diligence, I am unaware...

## applicant_date
- type: `date`

## applicant_signature
- type: `text`

## attorney_name
- type: `text`

## attorney_address
- type: `text`

## attorney_phone
- type: `text`

## attorney_bar_number
- type: `text`

## attorney_email
- type: `text`

## filing_fee
- type: `currency`

## mailing_notices_fee
- type: `currency`

## publication_fee
- type: `currency`
