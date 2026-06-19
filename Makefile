# Maine probate forms — fill geometry maintenance.
# PIPELINE points at the detection pipeline checkout (holding trees/ + output_fused/).
PIPELINE ?= ../detection-pipeline
REPO     ?= .

.PHONY: help verify check geometry geometry-commit statutes statutes-check align align-check manifest manifest-check check-upstream test smoke audit probe-all

help:
	@echo "make test                              run the unit + smoke + adversarial suite (CI gate)"
	@echo "make smoke                             fill+verify every shipped example case (network)"
	@echo "make audit                             systematic geometry audit -> catalog/geometry_audit.json"
	@echo "make questions                         question/field-quality audit -> catalog/question_audit.json"
	@echo "make value-guides                      (re)generate per-form value_guide.json sidecars"
	@echo "make value-guides-check                validate value guides + flag under-typed fields (CI gate)"
	@echo "make qa FORM=<id>                      per-form QA: prompts->fields->placeholder fill->read-back PNGs"
	@echo "make probe-all [OUT=dir]               render overlay PNGs for every page of every form (review)"
	@echo "make saturate [OUT=pdf]                fill every box to capacity + render, for alignment review"
	@echo "make verify                            validate shipped fill_geometry.json (CI gate)"
	@echo "make manifest                          (re)build catalog/pdf_manifest.json from source_urls"
	@echo "make manifest-check                    validate pdf_manifest.json structure + guard (CI gate)"
	@echo "make check-upstream                    re-probe source URLs; flag re-issued forms (network)"
	@echo "make check PIPELINE=<path>             maintainer-only (needs the private pipeline checkout): staleness check vs shipped"
	@echo "make geometry PIPELINE=<path>          maintainer-only (needs the private pipeline checkout): regenerate geometry"
	@echo "make geometry-commit PIPELINE=<path>   maintainer-only (needs the private pipeline checkout): regenerate + commit"
	@echo "make statutes                          rebuild statute index + sidecars + reference docs"
	@echo "make statutes-check                    validate the statute-consideration layer (CI gate)"
	@echo "make align                             rebuild per-field text-justification map from the schema"
	@echo "make align-check                       validate field_alignment.json vs schema + review flags (CI gate)"
	@echo "  (see docs/maintenance.md)"

test:
	python3 -m pytest tests/ -q

smoke:
	python3 -m pytest tests/test_smoke_examples.py -q

audit:
	python3 scripts/audit_form_geometry.py --out catalog/geometry_audit.json

questions:
	python3 scripts/audit_form_questions.py --out catalog/question_audit.json

value-guides:
	python3 scripts/build_value_guide.py

value-guides-check:
	python3 scripts/verify_value_guide.py

qa:
	python3 scripts/form_qa.py --form "$(FORM)" --out-dir $(or $(OUT),/tmp/qa)

probe-all:
	python3 tools/render_corpus.py --out-dir $(or $(OUT),/tmp/corpus_probe)

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
