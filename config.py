"""Configuration for the Maine Probate Forms AcroForm pipeline."""

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
FORMS_DIR = BASE_DIR / "forms"
SAMPLES_DIR = BASE_DIR / "samples"
INTERMEDIATE_DIR = BASE_DIR / "intermediate"
OUTPUT_DIR = BASE_DIR / "output"

ANALYSIS_DIR = INTERMEDIATE_DIR / "analysis"
DETECTION_DIR = INTERMEDIATE_DIR / "detection"
VALIDATION_DIR = INTERMEDIATE_DIR / "validation"
NAMING_DIR = INTERMEDIATE_DIR / "naming"

# ── Download ───────────────────────────────────────────────────────────────
DOWNLOAD_TIMEOUT = 30  # seconds per request
DOWNLOAD_RETRIES = 3
DOWNLOAD_DELAY = 0.5  # seconds between requests (politeness)

# ── Category folder mapping (URL path segment → local subdirectory) ───────
CATEGORY_MAP = {
    "Estates": "estates",
    "GC Adults": "gc_adults",
    "GC%20Adults": "gc_adults",
    "GC Minor": "gc_minor",
    "GC%20Minor": "gc_minor",
    "Guardian Minor": "guardian_minor",
    "Guardian%20Minor": "guardian_minor",
    "Adoption": "adoption",
    "Name Change Adult": "name_change",
    "Name%20Change%20Adult": "name_change",
    "Notices": "notices",
    "notices": "notices",
    "affidavits": "affidavits",
    "appeals": "appeals",
    "miscellaneous": "miscellaneous",
}

# ── Field detection thresholds ─────────────────────────────────────────────

# Text input lines
MIN_LINE_WIDTH = 50.0  # pt — minimum width to consider as a field line
MAX_LINE_HEIGHT = 3.0  # pt — maximum stroke height for a field line
LABEL_SEARCH_ABOVE = 30.0  # pt — how far above a line to look for a label
LABEL_SEARCH_LEFT = 150.0  # pt — how far left to search for inline label
FIELD_HEIGHT_ABOVE_LINE = 18.0  # pt — height of the text-input rect above the line

# Checkboxes / Radio buttons
CHECKBOX_MIN_SIZE = 8.0  # pt
CHECKBOX_MAX_SIZE = 16.0  # pt
CHECKBOX_ASPECT_TOLERANCE = 0.3  # max deviation from 1:1
RADIO_GROUP_MAX_DISTANCE = 60.0  # pt — max vertical span to group checkboxes as radio
RADIO_KEYWORDS = [  # labels suggesting mutually-exclusive options
    "yes",
    "no",
    "male",
    "female",
    "formal",
    "informal",
    "petitioner",
    "respondent",
    "check one",
    "select one",
]

# Signature fields
SIGNATURE_MIN_WIDTH = 200.0  # pt
SIGNATURE_KEYWORDS = ["signature", "sign", "subscribed", "sworn"]

# Date fields
DATE_KEYWORDS = ["date", "dob", "dod", "mm/dd/yyyy", "mm/dd/yy", "month", "day", "year"]

# Currency fields
CURRENCY_KEYWORDS = ["$", "amount", "value", "total", "balance", "dollars"]

# Case number header
CASE_NUMBER_KEYWORDS = ["docket no", "case no", "docket number", "case number"]
HEADER_Y_THRESHOLD = 100.0  # pt from top — area to look for case numbers

# Address blocks
ADDRESS_KEYWORDS = ["address", "street", "city", "state", "zip", "mailing"]
ADDRESS_STACK_MAX_VERTICAL = 80.0  # pt — max vertical span for stacked address lines
ADDRESS_MIN_LINES = 2

# Negative patterns (lines to skip)
FULL_PAGE_WIDTH_MARGIN = (
    20.0  # pt — lines within this margin of page width are page rules
)
TABLE_GRID_PROXIMITY = (
    5.0  # pt — lines within this distance of perpendicular lines form a grid
)
MIN_TABLE_INTERSECTIONS = 3  # number of intersections to qualify as table grid
LINE_THICKNESS_MAX = 3.0  # pt — lines thicker than this are decorative

# Label search — extended directions
LABEL_SEARCH_BELOW = 20.0  # pt — how far below a line to look for a label
LABEL_SEARCH_RIGHT = 100.0  # pt — how far right of a checkbox to look for a label
SECTION_HEADER_SEARCH = (
    120.0  # pt — how far up to search for section headers as context
)

# Implied fields (whitespace gaps below labeled text with no drawn line)
IMPLIED_FIELD_MIN_GAP = 20.0  # pt — min vertical gap between label and next element
IMPLIED_FIELD_MAX_GAP = 50.0  # pt — max gap to still consider an implied field
IMPLIED_FIELD_WIDTH = 400.0  # pt — default width for implied fields

# Table column header association
TABLE_HEADER_SEARCH = (
    200.0  # pt — how far above a table cell to search for column header
)

# Confidence scores
CONFIDENCE_HIGH = 0.9
CONFIDENCE_MEDIUM = 0.7
CONFIDENCE_LOW = 0.5
CONFIDENCE_VLM_BOOST = 0.15  # boost when VLM confirms heuristic detection

# ── VLM validator (Phase 2, 2026-04-30) ──────────────────────────────────
# Replaces the prior OpenRouter/Kimi-K2.5 validator with the local
# llama-router fleet (systemd: llama-router.service). qwen3.6-35b on each
# node is vision-capable via paired mmproj-F16 — same OpenAI-compatible API
# Kimi spoke, just hosted locally.
#
# Endpoints. Pick by latency / load. Each is an OpenAI-compatible server;
# list your own local / LAN / remote hosts here.
#   primary   http://localhost:8083/v1   ← default, lowest latency
#   host 2    http://localhost:8080/v1
#   host 3    http://localhost:8080/v1
VLM_API_BASE = "http://localhost:8083/v1"
VLM_FANOUT_ENDPOINTS = (
    "http://localhost:8083/v1",
    "http://localhost:8080/v1",     # second host
    "http://localhost:8080/v1",     # third host
    "http://localhost:8080/v1",     # fallback
    "http://localhost:8080/v1",     # fallback
)
VLM_MODEL = "qwen3.6-35b"
VLM_API_KEY_ENV = "OPENROUTER_API_KEY"  # legacy var kept for back-compat;
                                          # local router accepts any value
VLM_MAX_TOKENS = 16384  # ~100 chars/candidate JSON × 100+ candidates needs headroom
VLM_TEMPERATURE = 0.1
# Default = naming-only: VLM attaches semantic_name + field_type to every
# heuristic candidate, never gates. Switch to "gating" to enable keep/reject
# (drops F1 ~11pp on our benchmark, see reports/2026-04-30).
VLM_MODE = "naming"  # "naming" | "gating"
# Render target — 2400px long edge gives qwen3.6-vl strong document
# fidelity without exhausting context. Letter at 8.5×11in lands ~218 DPI.
RENDER_TARGET_LONG_EDGE_PX = 2400

# ── AcroForm writer ──────────────────────────────────────────────────────
WIDGET_BORDER_WIDTH = 0.5  # thin visible border
WIDGET_BORDER_COLOR = (0.6, 0.6, 0.6)  # gray border
WIDGET_FILL_COLOR = (0.93, 0.95, 1.0)  # light blue fill to show fillable areas
WIDGET_TEXT_COLOR = (0, 0, 0)  # black text
FONT_SIZE_AUTO_SCALE = 0.7  # fraction of field height to use as font size
