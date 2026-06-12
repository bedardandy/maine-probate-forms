"""Stage 1: Download all Maine Probate Court PDF forms."""

import logging
import time
from urllib.parse import unquote, urlparse

import requests

import config

logger = logging.getLogger(__name__)

# Complete list of form URLs organized by category.
# Scraped from https://www.maineprobate.net/welcome/probateforms-2019/
FORM_URLS: dict[str, list[str]] = {
    "estates": [
        "http://www.maineprobate.net/Forms2019/Estates/DE-101%20Petition%20for%20Formal%20Adjudication%20or%20Formal%20Appointment%20-%20Intestate%20(Rev%20%2007.01.19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-101(I)%20Application%20for%20Informal%20-%20Intestate%20(Rev.%2009-12-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-104%20PR%20Acceptance%20(Rev.%2007-01-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-201%20Petition%20for%20Formal%20Probate%20of%20Will%20or%20Appointmet%20of%20PR%20(Rev.%208-6-21).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-201(I)%20Application%20for%20Informal%20Probate%20of%20Will%20or%20Appointment%20(Rev.%2009-12-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-301%20Petition%20for%20Appointment%20of%20Special%20Administrator%20(Rev.%2007-01-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-301(I)%20Application%20for%20Informal%20Appt%20of%20Special%20Administrator%20(Rev.%2009-12-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-401(A)%20Certificate%20of%20Value%20Resident%20and%20Non%20Resident%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-403%20Bond%20For%20Personal%20Representative%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-405%20Inventory%20(Rev.%205-6-21).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-406%20Probate%20Account%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-407%20Renunciation-Nomination%20(Rev.%2003-01-25).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-501%20Petition%20with%20Respect%20to%20Supervised%20Administration%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-502%20Demand%20For%20Bond%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-503%20Claim%20Against%20Estate%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-504%20Petition%20to%20Resolve%20Disputed%20Claim%20and%20Allowance%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-505%20Petition%20with%20Respect%20to%20Pretermitted%20or%20Omitted%20Child%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-506%20Petition%20for%20Elective%20Share%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-507%20Petition%20to%20Reopen%20Estate%20(Rev.%2007-01-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-509%20Petition%20for%20Removal%20of%20Personal%20Representative%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-601%20Petition%20for%20Order%20of%20Complete%20Settlement%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-602%20Sworn%20Statement%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-603%20Closing%20Statement%20for%20Small%20Estate%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/Estates/DE-605%20Verified%20Application%20for%20Certificate%20of%20Discharge%20(Rev.%207-1-19).pdf",
    ],
    "gc_adults": [
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-201%20Petition%20for%20Appointment%20of%20Guardian%20%20(Rev.%2007-01-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-203%20Acceptance%20of%20Appointment%20by%20Guardian%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-205%20Joined%20Petition%20for%20Guardian%20and%20Conservator%20(Rev.%2007-01-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-207%20Acceptance%20of%20Appointment%20by%20Guardian%20and%20Conservator%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-209%20Interim%20and%20Annual%20Report%20of%20Guardian%20(Rev.%2007-01-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-210%20Registration%20of%20Guardianship%20or%20Conservatorship.pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-401%20Petition%20for%20Appointment%20of%20Conservator%20(Rev.%2007-01-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-402%20Acceptance%20of%20Appointment%20by%20Conservator%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-405%20Bond%20for%20Conservator%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-406%20Inventory%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-407%20Conservator%20Account%20(Rev.%2009-12-2019).pdf",
        # superseded 2026-04-30: live catalog now shows Rev. 03-03-26
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-407%20Conservator%20Account%20(Rev.%2003-03-26).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-408%20Claim%20Against%20Estate%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-409%20Petition%20to%20Resolve%20Disputed%20Claim%20and%20Petition%20for%20Allowance%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-410%20Petition%20for%20Interim%20Order%20(Rev.%2009-12-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-412%20Conservators%20Report%20(Rev.%208-6-21).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-413%20Petition%20for%20Termination,%20Removal%20or%20Resignation%20(Rev.%209-12-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-502%20Guardianship%20Plan-Adult%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-503%20Conservator%20Plan%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-504%20Joined%20Plan%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-505%20Physician's%20or%20Psychologist's%20Report%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-506%20Visitor's%20Report%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-507%20Affidavit%20for%20Emergency%20Guardian%20and-or%20Conservator%20(Rev.%2007-01-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-509%20Petition%20to%20Accept%20Transfer%20of%20Guardianship.Conservatorship%20(Rev.%209-12-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-510%20Petition%20to%20Transfer%20of%20Guardianship-Conservatorship%20and%20Provisional%20Order%20(Rev.%202-3-21).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Adults/PP-601%20Petition%20for%20Other%20Protective%20Arrangements%20(Rev.%207-1-19).pdf",
    ],
    "gc_minor": [
        "http://www.maineprobate.net/Forms2019/GC%20Minor/PP-107%20Petition%20for%20Appointment%20of%20Conservator%20of%20Minor%20(Rev.%2007-01-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Minor/PP-108%20Acceptance%20of%20Appt%20by%20Conservator%20-%20Minor%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/Forms2019/GC%20Minor/AD-GS-292%20Mot.%20for%20Special%20Findings%20(8-24).pdf",
    ],
    "guardian_minor": [
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-001%20Petition%20for%20Guardianship%20of%20a%20Minor.pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-003%20Mot.%20Emergency%20Appt.%20Guardian%20of%20Minor%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-006%20Affidavit%20of%20Notice%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-007%20Parent%20Consent%20to%20Guardian%20for%20Minor%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-008%20Acceptance%20of%20Appt%20by%20Guardian.pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-009%20Pet.%20for%20Mod.%20Termination%20Removal%20Resignation%20Guardian%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-012%20Minor's%20Consent-Objection-Nomination%203.4.20.pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-014%20Status%20Report%20of%20the%20Guardian%20.pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-016%20Child%20Support%20Affidavit.pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-018%20Guardianship%20Child%20Support%20Order%20(Rev.%2006-22)%20.pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/GS-021%20Parents%20Consent%20Appointment%20of%20Guardian%20ICWA%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/PB-007%20GAL%20Joint%20Appt.%20Order%203.4.20.pdf",
        "http://www.maineprobate.net/Forms2019/Guardian%20Minor/AD-001%20Petition%20for%20Adoption%20Rev%2010.23.pdf",
    ],
    "adoption": [
        "http://www.maineprobate.net/forms2019/Adoption/AD-001%20Pet.%20for%20Adoption%20&%20Name%20Change%20(Rev.%2010-23).pdf",
        # superseded 2026-04-30: live catalog now shows Rev. 1-26
        "http://www.maineprobate.net/forms2019/Adoption/AD-001%20Pet.%20for%20Adoption%20&%20Name%20Change%20(Rev.%201-26).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-003%20Consent%20of%20Other%20than%20Parent%203.4.20.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-004%20Consent%20to%20be%20Adopted%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-005%20Consent%20of%20Parent%20to%20Adoption%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-006%20Consent%20of%20Parent%20Outside%20Maine%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-007%20Confidential%20Statement.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-008%20Report%20of%20Disbursements.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-009%20Certificate%20of%20Counseling.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-011%20Pet%20to%20Recognize%20Foreign%20Adoption.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-012%20Pet.%20for%20Termination%20of%20Parental%20Rights%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-015%20Surrender%20of%20Child%20for%20Adoption%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-017,%20Waiver%20of%20Notice%20-%20Putative%20Parent,%20fillable%20locked%20Rev.%2010.19.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-020,%20Pet%20Annul%20of%20Adoption%20Decree,%20fillable%20locked%20Rev.%2010.19.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-022,%20Pet.%20for%20Info%20or%20to%20Examine%20Recs,%20fillable%20locked%20Rev.%2010.19.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-026%20Petition%20for%20Adult%20Adoption%20(Rev.%2011-03-24).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-028%20Affidavit%20of%20Parentage%20(Rev.%205-6-21).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-029%20Statement%20RE%20Tribal%20Affiliation%2005-2022.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-030%20Pet.%20%20for%20Confirmatory%20Adoption%20(Rev.%2010-23).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-032%20Auth%20for%20DHHS%20Child%20Abuse%20Background%20Check.pdf",
        "http://www.maineprobate.net/forms2019/Adoption/AD-GS-292%20Mot.%20for%20Special%20Findings%20(8-24).pdf",
        "http://www.maineprobate.net/forms2019/Adoption/MJB-Form-oth-153.pdf",
    ],
    "name_change": [
        "http://www.maineprobate.net/forms2019/Name%20Change%20Adult/CN-1%20Name%20Change%20Petition%20(Rev.%2011-03-24).pdf",
        "http://www.maineprobate.net/forms2019/Name%20Change%20Adult/NC-001%20Petition%20for%20Name%20Change%20of%20Minor.pdf",
        "http://www.maineprobate.net/forms2019/Name%20Change%20Adult/NC-003%20-%20Waiver%20&%20Consent.pdf",
    ],
    "notices": [
        "http://www.maineprobate.net/forms2019/Notices/N-105%20Demand%20for%20Notice%20(Rev.%206-25-23).pdf",
        "http://www.maineprobate.net/forms2019/Notices/N-106%20Notice%20of%20Removal%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/forms2019/Notices/N-107%20Waiver%20of%20Notice%20(Rev.%2003-01-25).pdf",
        "http://www.maineprobate.net/forms2019/Notices/N-108%20Waiver%20of%20Notice%20on%20Behalf%20of%20Minor%20or%20Individual%20Subject%20to%20G-C%20(Rev.%206-25-23).pdf",
        "http://www.maineprobate.net/forms2019/Notices/N-112%20Notice%20of%20Intent%20to%20Register%20Guardianship%20or%20Conservatorship.pdf",
        "http://www.maineprobate.net/forms2019/Notices/N-115%20Notice%20re%20Appointment%20of%20PR%20to%20Heirs,%20Devisees%20(Rev.%207-1-19).pdf",
        "http://www.maineprobate.net/forms2019/Notices/N-116%20Notice%20of%20Appt.%20of%20Domiciliary%20Foreign%20PR%20(Rev.%209-12-19).pdf",
        "http://www.maineprobate.net/forms2019/Notices/N-117%20Notice%20of%20Appointment%20of%20GC%20(Rev.%202-20-20).pdf",
        "http://www.maineprobate.net/forms2019/Notices/N-118%20Notice%20of%20Guardianship%20Conservatorship%20Proceeding%20(Rev.%209-28-20).pdf",
    ],
    "affidavits": [
        "http://www.maineprobate.net/forms2019/affidavits/AF-101%20Jurisdictional%20Affidavit%20(Rev.%2003-01-2025).pdf",
        "http://www.maineprobate.net/forms2019/affidavits/AF-102%20Small%20Estate%20Affidavit%20for%20Collection%20of%20Personal%20Property%20(Rev.%204-8-20).pdf",
        "http://www.maineprobate.net/forms2019/affidavits/AF-103%20Affidavit%20of%20Name%20Change%20for%20Adult.pdf",
        "http://www.maineprobate.net/forms2019/affidavits/AF-104%20Affidavit%20of%20Diligent%20Search%20(Rev.%208-6-21).pdf",
        "http://www.maineprobate.net/forms2019/affidavits/AF-105%20Indigency-Financial%20Affidavit%20(Rev.%2011-12-20).pdf",
        "http://www.maineprobate.net/forms2019/affidavits/FM-PB-009%20Affidavit%20(fillable).pdf",
    ],
    "appeals": [
        "http://www.maineprobate.net/forms2019/appeals/APP-1%20Notice%20of%20Appeal%20to%20Law%20Court%20(Rev.%209-12-19).pdf",
        "http://www.maineprobate.net/forms2019/appeals/APP-2%20Transcript%20Order%20(Rev.%206-25-20).pdf",
    ],
    "miscellaneous": [
        "http://www.maineprobate.net/forms2019/miscellaneous/MISC-101%20Motion%20Form%20(Rev.%209-12-19).pdf",
        "http://www.maineprobate.net/forms2019/miscellaneous/MISC-102%20Witness%20Subpoena%20(Rev.%208-6-21).pdf",
    ],
}


def _extract_form_id(filename: str) -> str:
    """Extract the form ID (e.g. 'DE-101') from a filename."""
    # Take everything before the first space, or the whole name if no space
    base = filename.rsplit(".", 1)[0]  # remove .pdf
    parts = base.split(" ", 1)
    return parts[0]


def _sanitize_filename(url: str) -> str:
    """Convert a URL into a safe local filename."""
    parsed = urlparse(url)
    raw = unquote(parsed.path.split("/")[-1])
    # Replace problematic characters
    safe = raw.replace(",", "_").replace("&", "and")
    # Collapse multiple spaces
    safe = " ".join(safe.split())
    return safe


def download_forms(categories: list[str] | None = None, force: bool = False) -> dict:
    """Download all forms, organized by category.

    Args:
        categories: If provided, only download these categories.
        force: If True, re-download even if file exists.

    Returns:
        Dict with 'success', 'skipped', 'failed' counts and 'errors' list.
    """
    stats = {"success": 0, "skipped": 0, "failed": 0, "errors": []}
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "MaineProbateFormDownloader/1.0 (legal form archival)"}
    )

    targets = (
        FORM_URLS
        if categories is None
        else {k: v for k, v in FORM_URLS.items() if k in categories}
    )

    for category, urls in targets.items():
        cat_dir = config.FORMS_DIR / category
        cat_dir.mkdir(parents=True, exist_ok=True)

        for url in urls:
            filename = _sanitize_filename(url)
            dest = cat_dir / filename

            if dest.exists() and not force:
                logger.debug("Skipping (exists): %s", filename)
                stats["skipped"] += 1
                continue

            for attempt in range(config.DOWNLOAD_RETRIES):
                try:
                    logger.info(
                        "Downloading [%s] %s (attempt %d)",
                        category,
                        filename,
                        attempt + 1,
                    )
                    resp = session.get(url, timeout=config.DOWNLOAD_TIMEOUT)
                    resp.raise_for_status()

                    dest.write_bytes(resp.content)
                    logger.info("  → saved %s (%d bytes)", dest.name, len(resp.content))
                    stats["success"] += 1
                    break
                except requests.RequestException as e:
                    logger.warning("  attempt %d failed: %s", attempt + 1, e)
                    if attempt == config.DOWNLOAD_RETRIES - 1:
                        msg = f"FAILED {category}/{filename}: {e}"
                        logger.error(msg)
                        stats["failed"] += 1
                        stats["errors"].append(msg)

            time.sleep(config.DOWNLOAD_DELAY)

    return stats


def list_downloaded_forms() -> list[dict]:
    """Return a list of all downloaded form files with metadata."""
    forms = []
    for cat_dir in sorted(config.FORMS_DIR.iterdir()):
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        for pdf_path in sorted(cat_dir.glob("*.pdf")):
            forms.append(
                {
                    "form_id": _extract_form_id(pdf_path.name),
                    "filename": pdf_path.name,
                    "category": category,
                    "path": str(pdf_path),
                    "size": pdf_path.stat().st_size,
                }
            )
    return forms


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    total_urls = sum(len(v) for v in FORM_URLS.values())
    logger.info(
        "Starting download of %d forms across %d categories", total_urls, len(FORM_URLS)
    )
    result = download_forms()
    logger.info(
        "Done: %d success, %d skipped, %d failed",
        result["success"],
        result["skipped"],
        result["failed"],
    )
    if result["errors"]:
        for e in result["errors"]:
            logger.error("  %s", e)
