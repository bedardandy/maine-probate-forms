# Form DE-502

## county
- type: `text`
- prompt: County

## docket_no
- type: `text`
- prompt: Docket No.

## decedent_name
- type: `text`
- prompt: Estate of (Decedent name)

## bond_demand
- type: `select_one`
- prompt: Bond Demand
- options:
  - `demands_bond` — Petitioner demands that the Personal Representative give Bond
  - `specific_amount` — Petitioner believes the Bond should be in the amount of $ for the following reasons
  - `court_assigns` — Petitioner requests that the Court assign the bond amount

## bond_amount
- type: `currency`
- prompt: Bond Amount
- when: `bond_demand == 'specific_amount'`

## mailing_method
- type: `select_one`
- prompt: Mailing Method (Check (a) or (b))
- options:
  - `mailed_copy` — (a) Petitioner has mailed a copy of this demand to the Personal Representative
  - `enclosed_original` — (b) Petitioner has enclosed an original and one copy of this demand and asks the Register to mail a copy to the Personal Representative if appointment and qualification have occurred

## date_signed
- type: `date`
- prompt: Dated

## petitioner_or_attorney_signature
- type: `text`
- prompt: Petitioner or Attorney Signature

## attorney_signature
- type: `text`
- prompt: Attorney Signature (Required by Rule 11)

## attorney_name
- type: `text`
- prompt: Attorney Name

## attorney_address
- type: `text`
- prompt: Attorney Address

## attorney_phone
- type: `text`
- prompt: Attorney Phone Number

## attorney_bar_number
- type: `text`
- prompt: Maine Bar Number

## attorney_email
- type: `text`
- prompt: Attorney Email Address
