# Form DE-601

## county_probate_court
- type: `text`
- prompt: County (Probate Court header)

## docket_number
- type: `text`
- prompt: Docket No.

## estate_name
- type: `text`

## petitioner_contact
- type: `text`
- prompt: Full legal name, address and email address of Petitioner.

## petitioner_interest
- type: `select_many`
- prompt: Legal interest of Petitioner in this Estate:
- options:
  - `personal_representative` — Personal Representative named in the Will
  - `surviving_spouse` — Surviving spouse
  - `domestic_partner` — Domestic partner
  - `devisee` — Devisee
  - `heir` — Heir
  - `creditor` — Creditor
  - `other` — Other

## petitioner_interest_other
- type: `text`
- when: `petitioner_interest == 'other'`

## court_requests
- type: `select_many`
- prompt: Petitioner asks the Court (Check applicable sections.):
- options:
  - `testacy_status` — To make a determination of the testacy status of the Decedent.
  - `compel_account` — To compel the Personal Representative to file an account with the Court.
  - `approve_account` — To consider and approve the account of the Personal Representative.
  - `construe_will` — To construe the Will or a portion thereof.
  - `determine_heirs` — To determine the heirs of the Decedent.
  - `determine_distribution` — To determine the persons or entities entitled to distribution and the amounts to be distributed.
  - `final_settlement` — To order final settlement and distribution of Estate.
  - `discharge_representative` — To discharge the Personal Representative from further claims or demands of interested persons and to close administration of the Estate.
  - `other` — Other

## signature_date
- type: `date`
- prompt: Dated

## signature_name
- type: `text`
- prompt: Petitioner or Attorney for Petitioner

## attorney_name
- type: `text`
- prompt: Attorney Name

## attorney_address
- type: `text`
- prompt: Attorney Address

## attorney_phone
- type: `text`
- prompt: Attorney Phone Number
