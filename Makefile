# Maine probate forms — fill geometry maintenance.
# PIPELINE points at the detection pipeline checkout (holding trees/ + output_fused/).
PIPELINE ?= ../detection-pipeline
REPO     ?= .

.PHONY: help verify check geometry geometry-commit statutes statutes-check align align-check manifest manifest-check check-upstream

help:
	@echo "make verify                            validate shipped fill_geometry.json (CI gate)"
	@echo "make manifest                          (re)build catalog/pdf_manifest.json from source_urls"
	@echo "make manifest-check                    validate pdf_manifest.json structure + guard (CI gate)"
	@echo "make check-upstream                    re-probe source URLs; flag re-issued forms (network)"
	@echo "make check PIPELINE=<path>             staleness check: regenerate in memory, diff vs shipped"
	@echo "make geometry PIPELINE=<path>          regenerate geometry from pipeline outputs"
	@echo "make geometry-commit PIPELINE=<path>   regenerate + commit (push stays manual)"
	@echo "make statutes                          rebuild statute index + sidecars + reference docs"
	@echo "make statutes-check                    validate the statute-consideration layer (CI gate)"
	@echo "make align                             rebuild per-field text-justification map from the schema"
	@echo "make align-check                       validate field_alignment.json vs schema + review flags (CI gate)"
	@echo "  (see docs/maintenance.md)"

verify:
	python3 scripts/verify_fill_geometry.py --repo $(REPO)

manifest:
	python3 tools/build_pdf_manifest.py

manifest-check:
	python3 scripts/verify_manifest.py

check-upstream:
	python3 tools/check_upstream.py

align:
	python3 scripts/author_field_align.py

align-check:
	python3 scripts/verify_field_align.py

statutes:
	python3 scripts/build_statute_index.py
	python3 scripts/author_statutes.py
	python3 scripts/build_statute_reference.py

statutes-check:
	python3 scripts/verify_statutes.py

check:
	python3 scripts/regen_fill_geometry.py --pipeline-root $(PIPELINE) --repo $(REPO) --check

geometry:
	python3 scripts/regen_fill_geometry.py --pipeline-root $(PIPELINE) --repo $(REPO)

geometry-commit:
	python3 scripts/regen_fill_geometry.py --pipeline-root $(PIPELINE) --repo $(REPO) --commit
