#!/usr/bin/env python3
"""Build the trusted Maine Title 18-C section index.

This is the anti-hallucination backbone for the per-form statute-consideration
layer: every statute a form's `statutes.json` sidecar cites MUST resolve to a
section in the index this script emits. The index is derived verbatim from the
official chapter tables of contents at legislature.maine.gov (fetched and pasted
below), so section numbers and titles are authoritative rather than recalled.

Articles captured at section granularity: 1, 2, 3, 4, 5, 6, 8, 9, 10.
Article 7 (Trust Administration) is intentionally omitted — none of the 79
probate court forms in this repo invoke trust-administration sections; if a
future form needs it, paste its TOC into ARTICLE_TOCS below.

Source TOC pages (one per article):
    https://legislature.maine.gov/statutes/18-C/title18-Cch<N>sec0.html

Output:
    docs/statute-reference/_index/18c-sections.json

Usage:
    python3 scripts/build_statute_index.py
"""
from __future__ import annotations

import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "statute-reference" / "_index" / "18c-sections.json"

# Verbatim chapter tables of contents, copied from legislature.maine.gov.
# Lines are parsed as "<section>. <title>"; Part/Subpart headers and blank
# lines are ignored. Keep these blocks verbatim so the index stays auditable.
ARTICLE_TOCS: dict[int, str] = {
    1: """
1-101. Short title
1-102. Purposes; rule of construction
1-103. Supplementary general principles of law applicable
1-104. Construction against implied repeal
1-105. Effect of fraud and evasion
1-106. Evidence as to death or status
1-107. Acts by holder of general power
1-108. Cost-of-living adjustment of certain dollar amounts
1-109. Transfer for value
1-110. Powers of fiduciaries relating to compliance with environmental laws
1-111. Guardian ad litem
1-201. Definitions
1-301. Territorial application
1-302. Subject matter jurisdiction
1-303. Venue; multiple proceedings; transfer
1-304. Rule-making power
1-305. Records and certified copies; judicial supervision
1-306. No jury trial; removal
1-307. Register; powers
1-308. Appeals
1-309. Judges
1-310. Oath or affirmation on filed documents
1-401. Notice
1-402. Notice; waiver
1-403. Pleadings; when parties bound by others; notice
1-501. Election; bond; vacancies; salaries; copies
1-502. Condition of bond
1-503. Duties; records; binding of papers; facsimile signature
1-504. Certification of wills; appointments of personal representatives; elective share petitions involving real estate
1-505. Notices to devisees and heirs; furnishing of copies
1-506. Deputy register of probate
1-507. Inspection of register's conduct of office
1-508. Register incapable or neglects duties
1-509. Records in case of vacancy
1-510. Register or court employee; prohibited activities
1-511. Fees for approved blanks and forms
1-601. Costs in contested cases
1-602. Filing and certification fees
1-603. Registers to account monthly for fees
1-604. Expenses of partition
1-605. Compensation of court reporters
1-606. Court reporters to furnish copies
1-607. Surcharge for restoration, storage and preservation of records
1-608. Fees not established in statute
1-701. Process to change name
1-801. Commission established
1-802. Consultants; experts
1-803. Duties
1-804. Organization
1-805. Federal funds
""",
    2: """
2-101. Intestate estate
2-102. Share of spouse
2-103. Share of heirs other than surviving spouse
2-104. Requirement of survival by 120 hours; individual in gestation
2-105. No taker
2-106. Per capita at each generation
2-107. Kindred of half blood
2-108. Advancements
2-109. Debts to decedent
2-110. Alienage
2-111. Dower and curtesy abolished
2-112. Individuals related to decedent through 2 lines
2-113. Parent barred from inheriting
2-115. Determination of parentage for purposes of intestate succession
2-116. Effect of a pending petition
2-117. Effect of an order granting adoption on adoptee and adoptee's former parents
2-118. Child born after death of parent
2-201. Definitions
2-202. Elective share
2-203. Composition of the augmented estate; marital-property portion
2-204. Decedent's net probate estate
2-205. Decedent's nonprobate transfers to others
2-206. Decedent's nonprobate transfers to the surviving spouse
2-207. Surviving spouse's property and nonprobate transfers to others
2-208. Exclusions, valuation and overlapping application
2-209. Sources from which elective share payable
2-210. Personal liability of recipients
2-211. Proceeding for elective share; time limit
2-212. Right of election personal to surviving spouse
2-213. Waiver of right to elect and of other rights
2-214. Protection of payors and other 3rd parties
2-301. Entitlement of spouse; premarital will
2-302. Omitted children
2-401. Applicable law
2-402. Homestead allowance
2-403. Exempt property
2-404. Family allowance
2-405. Source, determination and documentation
2-501. Who may make a will
2-502. Execution; holographic wills
2-503. Self-proved will
2-504. Who may witness a will
2-505. Choice of law as to execution
2-506. Revocation by writing or by act
2-507. Revocation by change of circumstances
2-508. Revival of revoked will
2-509. Incorporation by reference
2-510. Uniform Testamentary Additions to Trusts Act
2-511. Events of independent significance
2-512. Separate writing identifying devise of certain types of tangible personal property
2-513. Contracts concerning succession
2-514. Disposition of will deposited with court
2-515. Duty of custodian of will; liability
2-516. Penalty clause for contest
2-517. Statutory wills
2-601. Scope
2-602. Will may pass all property and after-acquired property
2-603. Antilapse; deceased devisee; class gifts
2-604. Failure of testamentary provision
2-605. Increase in securities; accessions
2-606. Nonademption of specific devises; unpaid proceeds of sale, condemnation or insurance; sale by conservator or agent
2-607. Nonexoneration
2-608. Exercise power of appointment
2-609. Ademption by satisfaction
2-701. Scope
2-702. Requirement of survival by 120 hours
2-703. Choice of law as to meaning and effect of governing instrument
2-704. Power of appointment; compliance with specific reference requirement
2-705. Class gifts construed to accord with intestate succession; exceptions
2-706. Life insurance; retirement plan; account with POD designation; TOD designation; deceased beneficiary
2-707. Survivorship with respect to future interests under terms of trust; substitute takers
2-708. Class gifts to "descendants," "issue" or "heirs of the body"; form of distribution if none specified
2-709. Per capita at each generation; per stirpes or by representation
2-710. Worthier-title doctrine abolished
2-711. Interests in "heirs" and like
2-801. Effect of divorce, annulment and decree of separation
2-802. Effect of homicide on intestate succession, wills, trusts, joint assets, life insurance and beneficiary designations
2-803. Effect of criminal conviction on intestate succession, wills, joint assets, beneficiary designations and other property acquisition when restitution is owed to the decedent
2-804. Revocation of probate and nonprobate transfers by divorce; no revocation by other changes of circumstances
2-805. Reformation to correct mistakes
2-806. Modification to achieve transferor's tax objectives
2-807. Actions for wrongful death
2-901. Short title
2-902. Definitions
2-903. Scope
2-904. Part supplemented by other law
2-905. Power to disclaim; general requirements; when irrevocable
2-906. Disclaimer of interest in property
2-907. Disclaimer of rights of survivorship in jointly held property
2-908. Disclaimer of interest by trustee
2-909. Disclaimer of power of appointment or other power not held in fiduciary capacity
2-910. Disclaimer by appointee, object or taker in default of exercise of power of appointment
2-911. Disclaimer of power held in fiduciary capacity
2-912. Delivery or filing
2-913. When disclaimer barred or limited
2-914. Tax qualified disclaimer
2-915. Recording of disclaimer
2-916. Application to existing relationships
2-917. Relation to Electronic Signatures in Global and National Commerce Act
""",
    3: """
3-101. Devolution of estate at death; restrictions
3-102. Necessity of order of probate for will
3-103. Necessity of appointment for administration
3-104. Claims against decedent; necessity of administration
3-105. Proceedings affecting devolution and administration; jurisdiction of subject matter
3-106. Proceedings within the jurisdiction of court; service; jurisdiction over persons
3-107. Scope of proceedings; proceedings independent; exception
3-108. Probate, testacy and appointment proceedings; ultimate time limit
3-109. Statutes of limitation on decedent's cause of action
3-110. Discovery of property
3-201. Venue for first and subsequent estate proceedings; location of property
3-202. Appointment or testacy proceedings; conflicting claim of domicile in another state
3-203. Priority among persons seeking appointment as personal representative
3-204. Demand for notice of order or filing concerning decedent's estate
3-301. Informal probate or appointment proceedings; application; contents
3-302. Informal probate; duty of register; effect of informal probate
3-303. Informal probate; proof and findings required
3-304. Informal probate; unavailable in certain cases
3-305. Informal probate; register not satisfied
3-306. Informal probate; notice requirements
3-307. Informal appointment proceedings; delay in order; duty of register; effect of appointment
3-308. Informal appointment proceedings; proof and findings required
3-309. Informal appointment proceedings; register not satisfied
3-310. Informal appointment proceedings; notice requirements
3-311. Informal appointment unavailable in certain cases
3-401. Formal testacy proceedings; nature; when commenced
3-402. Formal testacy or appointment proceedings; petition; contents
3-403. Formal testacy proceeding; notice of hearing on petition
3-404. Formal testacy proceedings; written objections to probate
3-405. Formal testacy proceedings; uncontested cases; hearings and proof
3-406. Formal testacy proceedings; contested cases
3-407. Formal testacy proceedings; burdens in contested cases
3-408. Formal testacy proceedings; will construction; effect of final order in another jurisdiction
3-409. Formal testacy proceedings; order; foreign will
3-410. Formal testacy proceedings; probate of more than one instrument
3-411. Formal testacy proceedings; partial intestacy
3-412. Formal testacy proceedings; effect of order; vacation
3-413. Formal testacy proceedings; vacation of order for other cause
3-414. Formal proceedings concerning appointment of personal representative
3-501. Supervised administration; nature of proceeding
3-502. Supervised administration; petition; order
3-503. Supervised administration; effect on other proceedings
3-504. Supervised administration; powers of personal representative
3-505. Supervised administration; interim orders; distribution and closing orders
3-601. Qualification
3-602. Acceptance of appointment; consent to jurisdiction
3-603. Bond not required without court order; exceptions
3-604. Bond amount; security; procedure; reduction
3-605. Demand for bond by interested person
3-606. Terms and conditions of bonds
3-607. Order restraining personal representative
3-608. Termination of appointment; general
3-609. Termination of appointment; death or disability
3-610. Termination of appointment; voluntary
3-611. Termination of appointment by removal; cause; procedure
3-612. Termination of appointment; change of testacy status
3-613. Successor personal representative
3-614. Special administrator; appointment
3-615. Special administrator; who may be appointed
3-616. Special administrator; appointed informally; powers and duties
3-617. Special administrator; formal proceedings; power and duties
3-618. Termination of appointment; special administrator
3-619. Public administrators
3-701. Time of accrual of duties and powers
3-702. Priority among different letters
3-703. General duties; relation and liability to persons interested in estate; standing to sue
3-704. Personal representative to proceed without court order; exception
3-705. Duty of personal representative; information to heirs and devisees
3-706. Duty of personal representative; inventory and appraisal
3-707. Employment of appraisers
3-708. Duty of personal representative; supplementary inventory
3-709. Duty of personal representative; possession of estate
3-710. Power to avoid transfers
3-711. Powers of personal representatives; in general
3-712. Improper exercise of power; breach of fiduciary duty
3-713. Sale, encumbrance or transaction involving conflict of interest; voidable; exceptions
3-714. Persons dealing with personal representative; protection
3-715. Transactions authorized for personal representatives; exceptions
3-716. Powers and duties of successor personal representative
3-717. Corepresentatives; when joint action required
3-718. Powers of surviving personal representative
3-719. Compensation of personal representative
3-720. Expenses in estate litigation
3-721. Proceedings for review of employment of agents and compensation of personal representatives and employees of estate
3-801. Notice to creditors
3-802. Statutes of limitations
3-803. Limitations on presentation of claims
3-804. Manner of presentation of claims
3-805. Classification of claims
3-806. Allowance of claims
3-807. Payment of claims
3-808. Individual liability of personal representative
3-809. Secured claims
3-810. Claims not due and contingent or unliquidated claims
3-811. Counterclaims
3-812. Execution and levies prohibited
3-813. Compromise of claims
3-814. Encumbered assets
3-815. Administration in more than one state; duty of personal representative
3-816. Final distribution to domiciliary representative
3-817. Survival of actions
3-818. Damages limited to actual damages
3-901. Successors' rights if no administration
3-902. Distribution; order in which assets appropriated; abatement
3-903. Right of retainer
3-904. Interest on general pecuniary devise
3-905. Penalty clause for contest
3-906. Distribution in kind; valuation; method
3-907. Distribution in kind; evidence
3-908. Distribution; right or title of distributee
3-909. Improper distribution; liability of distributee
3-910. Purchasers from distributees protected
3-911. Partition for purpose of distribution
3-912. Private agreements among successors to decedent binding on personal representative
3-913. Distributions to trustee
3-914. Disposition of unclaimed assets
3-915. Distribution to person under disability
3-916. Uniform Estate Tax Apportionment Act
3-1001. Formal proceedings terminating administration; testate or intestate; order of general protection
3-1002. Formal proceedings terminating testate administration; order construing will without adjudicating testacy
3-1003. Closing estates; by sworn statement of personal representative
3-1004. Liability of distributees to claimants
3-1005. Limitations on proceedings against personal representative
3-1006. Limitations on actions and proceedings against distributees
3-1007. Certificate discharging liens securing fiduciary performance
3-1008. Subsequent administration
3-1101. Effect of approval of agreements involving trusts, inalienable interests or interests of 3rd persons
3-1102. Procedure for securing court approval of compromise
3-1201. Collection of personal property by affidavit
3-1202. Effect of affidavit
3-1203. Small estates; summary administrative procedure
3-1204. Small estates; closing by sworn statement of personal representative
3-1205. Social security payments
""",
    4: """
4-101. Definitions
4-201. Payment of debt and delivery of property to domiciliary foreign personal representative without local administration
4-202. Payment or delivery discharges
4-203. Resident creditor notice
4-204. Proof of authority; bond
4-205. Powers
4-206. Power of representatives in transition
4-207. Ancillary and other local administrations; provisions governing
4-301. Jurisdiction by act of foreign personal representative
4-302. Jurisdiction by act of decedent
4-303. Service on foreign personal representative
4-401. Effect of adjudication for or against personal representative
""",
    5: """
5-101. Short title
5-102. Definitions
5-103. Facility of transfer
5-104. Subject matter jurisdiction
5-105. Transfer of proceeding
5-106. Venue
5-107. Practice in court
5-108. Letters of office
5-109. Effect of acceptance of appointment
5-110. Coguardian; coconservator
5-111. Judicial appointment of successor guardian or successor conservator
5-112. Effect of death, removal or resignation of guardian or conservator
5-113. Notice of hearing
5-114. Waiver of notice
5-115. Guardian ad litem
5-116. Request for notice
5-117. Disclosure of bankruptcy or criminal history
5-118. Multiple appointments or nominations
5-119. Compensation and expenses; in general
5-120. Liability of guardian or conservator for act of individual subject to guardianship or conservatorship
5-121. Petition after appointment for instructions or ratification
5-122. Third-party acceptance of authority of guardian or conservator
5-123. Use of agent by guardian or conservator
5-124. Temporary substitute guardian or conservator
5-125. Registration of order; effect
5-126. Grievance against guardian or conservator
5-127. Delegation by parent or guardian
5-201. Appointment and status of guardian
5-202. Parental appointment of guardian
5-203. Objection by minor or others to parental appointment
5-204. Judicial appointment of guardian; conditions for appointment
5-205. Judicial appointment of guardian; procedure
5-206. Terms of order appointing guardian
5-207. Duties of guardian
5-208. Powers of guardian
5-209. Rights and immunities of guardian
5-210. Modification or termination of guardianship; other proceedings after appointment
5-211. Transitional arrangement for minors; continued contact with former guardian after termination
5-212. Appointment of guardian ad litem for minor
5-213. Indian Child Welfare Act of 1978 and Maine Indian Child Welfare Act
5-301. Basis for appointment of guardian for adult
5-302. Petition for appointment of guardian for adult
5-303. Notice and hearing
5-304. Appointment of visitor
5-305. Appointment and role of attorney for adult
5-306. Professional evaluation
5-307. Attendance and rights at hearing
5-308. Confidentiality of records
5-309. Who may be guardian of adult; priorities
5-310. Order of appointment
5-311. Notice of order of appointment; rights
5-312. Emergency guardian
5-313. Duties of guardian for adult
5-314. Powers of guardian for adult
5-315. Special limitations on guardian's power
5-316. Guardian's plan
5-317. Guardian's report; monitoring of guardianship
5-318. Removal of guardian for adult; appointment of successor
5-319. Termination or modification of guardianship for adult
5-401. Basis for appointment of conservator
5-402. Petition for appointment of conservator
5-403. Notice and hearing
5-404. Petition for protective order
5-405. Appointment and role of visitor
5-406. Appointment and role of attorney
5-407. Professional evaluation
5-408. Attendance and rights at hearing
5-409. Confidentiality of records
5-410. Who may be conservator; priorities
5-411. Order of appointment
5-412. Notice of order of appointment; rights
5-413. Emergency conservator
5-414. Powers of conservator requiring court approval
5-415. Petition for order subsequent to appointment
5-416. Bond or alternative asset-protection arrangement
5-417. Terms and requirements of bond
5-418. Duties of conservator
5-419. Conservator's plan
5-420. Inventory; records
5-421. Administrative powers of conservator not requiring court approval
5-422. Distribution from conservatorship estate
5-423. Conservator's report and accounting; monitoring
5-424. Attempted transfer of property by individual subject to conservatorship
5-425. Transaction involving conflict of interest
5-426. Protection of person dealing with conservator
5-427. Death of individual subject to conservatorship
5-428. Presentation and allowance of claim
5-429. Personal liability of conservator
5-430. Removal of conservator; appointment of successor
5-431. Termination or modification of conservatorship
5-501. Authority for protective arrangements
5-502. Basis for protective arrangements instead of guardianship for adult
5-503. Basis for protective arrangements instead of conservatorship for adult or minor
5-504. Petition
5-505. Notice and hearing
5-506. Appointment of visitor
5-507. Appointment and role of attorney
5-508. Professional evaluation
5-509. Attendance and rights at hearing
5-510. Notice of order
5-511. Confidentiality of records
5-601. Short title
5-602. Definitions
5-603. International application of Part
5-604. Communication between courts
5-605. Cooperation between courts
5-606. Taking testimony in another state
5-621. Definitions; significant-connection factors
5-622. Exclusive basis
5-623. Jurisdiction
5-624. Special jurisdiction
5-625. Exclusive and continuing jurisdiction
5-626. Appropriate forum
5-627. Jurisdiction declined by reason of conduct
5-628. Notice of proceeding
5-629. Proceedings in more than one state
5-631. Transfer of guardianship or conservatorship to another state
5-632. Accepting guardianship or conservatorship transferred from another state
5-641. Uniformity of application and construction
5-642. Relation to Electronic Signatures in Global and National Commerce Act
5-643. Transitional provisions
5-701. Public guardians and conservators; general
5-702. Priority of private guardian or conservator
5-703. Exclusiveness of public guardian or conservator
5-704. Nomination of public guardian or conservator
5-705. Acceptance by public guardian or conservator; plan
5-706. Officials authorized to act as public guardian or conservator
5-707. Duties and powers of a public guardian or conservator
5-708. No change in rights to services
5-709. No change in powers and duties of agency heads and trustees
5-710. Bond not required
5-711. Compensation
5-712. Individuals subject to guardianship; guardian ad litem costs
5-713. Limited public guardianships
5-801. Short title
5-802. Definitions
5-803. Advance health care directives
5-803-A. Remote signing of advance health care directives in health care facilities
5-804. Revocation of advance health care directive
5-805. Optional form
5-806. Decisions by surrogate
5-807. Decisions by guardian
5-808. Obligations of health care provider
5-809. Health care information
5-810. Immunities
5-811. Statutory damages
5-812. Capacity
5-813. Effect of copy
5-814. Effect of Part
5-815. Judicial relief
5-816. Uniformity of application and construction
5-817. Military advanced medical directives
5-901. Short title
5-902. Definitions
5-903. Applicability
5-904. Power of attorney is durable
5-905. Execution of power of attorney; notices
5-906. Validity of power of attorney
5-907. Meaning and effect of power of attorney
5-908. Nomination of conservator or guardian; relation of agent to court-appointed fiduciary
5-909. When power of attorney effective
5-910. Termination of power of attorney or agent's authority
5-911. Coagents and successor agents
5-912. Reimbursement and compensation of agent
5-913. Agent's acceptance
5-914. Agent's duties
5-915. Exoneration of agent
5-916. Judicial relief
5-917. Agent's liability
5-918. Agent's resignation; notice
5-919. Acceptance of and reliance upon acknowledged power of attorney
5-920. Liability for refusal to accept acknowledged power of attorney
5-921. Principles of law and equity
5-922. Laws applicable to financial institutions and entities
5-923. Remedies under other law
5-931. Authority that requires specific grant; grant of general authority
5-932. Incorporation of authority
5-933. Construction of authority generally
5-934. Real property
5-935. Tangible personal property
5-936. Stocks and bonds
5-937. Commodities and options
5-938. Banks and other financial institutions
5-939. Operation of entity or business
5-940. Insurance and annuities
5-941. Estate, trust and other beneficial interest
5-942. Claims and litigation
5-943. Personal and family maintenance
5-944. Benefits from governmental programs or civil or military service
5-945. Retirement plans
5-946. Taxes
5-947. Gifts
5-951. Agent's certification
5-961. Uniformity of application and construction
5-962. Relation to Electronic Signatures in Global and National Commerce Act
5-963. Effect on existing powers of attorney
""",
    6: """
6-101. Nonprobate transfers on death
6-102. Liability of nonprobate transferees for creditor claims and statutory allowances
6-201. Definitions
6-202. Limitation on scope of Part
6-203. Types of account; existing accounts
6-204. Forms
6-205. Designation of agent
6-206. Applicability of Part
6-211. Ownership during lifetime
6-212. Rights at death
6-213. Alteration of rights
6-214. Accounts and transfers nontestamentary
6-221. Authority of financial institution
6-222. Payment on multiple-party account
6-223. Payment on POD designation
6-224. Payment to designated agent
6-225. Payment to minor
6-226. Discharge
6-227. Setoff
6-301. Definitions
6-302. Registration in beneficiary form; sole or joint tenancy ownership
6-303. Registration in beneficiary form; applicable law
6-304. Origination of registration in beneficiary form
6-305. Form of registration in beneficiary form
6-306. Effect of registration in beneficiary form
6-307. Ownership on death of owner
6-308. Protection of registering entity
6-309. Nontestamentary transfer on death
6-310. Terms, conditions and forms for registration
6-311. Application of Part
6-401. Short title
6-402. Definitions
6-403. Applicability
6-404. Nonexclusivity
6-405. Transfer on death deed authorized
6-406. Transfer on death deed revocable
6-407. Transfer on death deed nontestamentary
6-408. Capacity of transferor; undue influence of transferor
6-409. Requirements
6-410. Notice, delivery, acceptance, consideration not required
6-411. Revocation by instrument authorized; revocation by act not permitted
6-412. Effect of transfer on death deed during transferor's life
6-413. Effect of transfer on death deed at transferor's death
6-414. Notice of death affidavit
6-415. Disclaimer
6-416. Liability for creditor claims and statutory allowances
6-417. Optional template for transfer on death deed
6-418. Optional template for revocation
6-419. Uniformity of application and construction
6-420. Relation to Electronic Signatures in Global and National Commerce Act
6-421. Effective date (REPEALED)
""",
    8: """
8-101. Estates of absentees; petition
8-102. Warrant
8-103. Notice
8-104. Publication
8-105. Hearing; appointment of receiver of property; bond
8-106. Possession by receiver
8-107. Collection of debts
8-108. Appointment of receiver for absentee's debts
8-109. Perishable goods
8-110. Support of dependents
8-111. Arbitration of claims
8-112. Compensation; cessation of duties
8-113. Termination of receivership
8-114. Limitations
8-201. Applicability to proceedings on other bonds
8-202. Surety on bond may cite trust officers for accounting
8-203. Agreement with sureties for joint control
8-204. Approval of bond by judge
8-205. Insufficient sureties
8-206. Discharge of surety
8-207. New bonds or removal of principal
8-208. Reduction of liability where signed by surety company
8-209. Actions on bonds
8-210. Principal made party in action against surety
8-211. Proceedings and judgment
8-212. Limitation of actions on bonds
8-213. Judicial authorization of actions
8-214. Forfeiture for failure to account when ordered
8-215. Judgment in trust for all interested
8-301. Time of taking effect; provisions for transition
""",
    9: """
9-101. Short title
9-102. Definitions
9-103. Jurisdiction
9-104. Venue; transfer
9-105. Rights of adopted persons
9-106. Legal representation
9-107. Indian Child Welfare Act of 1978 and Maine Indian Child Welfare Act
9-108. Application of prior laws
9-109. Mediation
9-201. Determination of parentage
9-202. Surrender and release; consent
9-203. Duties and responsibilities subsequent to surrender and release
9-204. Termination of parental rights
9-205. Review
9-301. Petition for adoption and change of name; filing fee
9-302. Consent for adoption
9-303. Petition
9-304. Investigation; guardian ad litem; registry
9-305. Evidence; procedure
9-306. Allowable payments; expenses
9-307. Adoption not granted
9-308. Final decree; dispositional hearing; effect of adoption
9-309. Appeals
9-310. Records confidential
9-311. Interstate placements
9-312. Foreign adoptions
9-313. Advertisement
9-314. Immunity from liability for good faith reporting; proceedings
9-315. Annulment of the adoption decree
9-316. Confirmatory adoptions
9-401. Authorization; special needs children
9-402. Adoption assistance
9-403. Administration
9-404. Rules
""",
    10: """
10-101. Short title
10-102. Definitions
10-103. Applicability
10-104. User direction for disclosure of digital assets
10-105. Terms of service agreement
10-106. Procedure for disclosing digital assets
10-107. Disclosure of content of electronic communications of deceased user
10-108. Disclosure of other digital assets of deceased user
10-109. Disclosure of content of electronic communications of principal
10-110. Disclosure of other digital assets of principal
10-111. Disclosure of digital assets held in trust when trustee is original user
10-112. Disclosure of content of electronic communications held in trust when trustee is not original user
10-113. Disclosure of other digital assets held in trust when trustee is not original user
10-114. Disclosure of digital assets to conservator of protected person
10-115. Fiduciary duty and authority
10-116. Custodian compliance and immunity
10-117. Uniformity of application and construction
10-118. Relation to Electronic Signatures in Global and National Commerce Act
""",
}

ARTICLE_TITLES = {
    1: "General Provisions, Definitions and Jurisdiction",
    2: "Intestate Succession and Wills",
    3: "Probate of Wills and Administration",
    4: "Foreign Personal Representatives; Ancillary Administration",
    5: "Maine Uniform Guardianship, Conservatorship and Protective Proceedings",
    6: "Nonprobate Transfers on Death",
    8: "Receiverships; Bonds; Effective Date and Transition",
    9: "Adoption",
    10: "Maine Revised Uniform Fiduciary Access to Digital Assets Act",
}

SEC_RE = re.compile(r"^(?:18-C\s*§?\s*)?(\d+-\d+(?:-[A-Z])?)\.\s+(.*?)\s*$")


def section_url(sec: str) -> str:
    return f"https://legislature.maine.gov/statutes/18-C/title18-Csec{sec}.html"


def build() -> dict:
    sections: dict[str, dict] = {}
    for article, toc in ARTICLE_TOCS.items():
        for line in toc.strip().splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            m = SEC_RE.match(line)
            if not m:
                continue
            sec, title = m.group(1), m.group(2)
            repealed = False
            if title.upper().endswith("(REPEALED)"):
                repealed = True
                title = title[: -len("(REPEALED)")].strip()
            sections[sec] = {
                "title": title,
                "article": article,
                "url": section_url(sec),
            }
            if repealed:
                sections[sec]["repealed"] = True
    return {
        "title": "Maine Revised Statutes Title 18-C (Maine Uniform Probate Code)",
        "effective": "2019-09-01",
        "source": "legislature.maine.gov chapter tables of contents (Articles 1-6, 8-10)",
        "note": (
            "Trusted citation index for the per-form statute-consideration layer. "
            "Article 7 (Trust Administration) is omitted; no form in this repo cites it. "
            "Section titles are verbatim from the official TOCs."
        ),
        "article_titles": {str(k): v for k, v in ARTICLE_TITLES.items()},
        "section_count": len(sections),
        "sections": dict(sorted(sections.items())),
    }


def main() -> None:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)} — {data['section_count']} sections")


if __name__ == "__main__":
    main()
