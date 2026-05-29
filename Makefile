# Maine probate forms — fill geometry maintenance.
# PIPELINE points at the detection pipeline checkout (holding trees/ + output_fused/).
PIPELINE ?= ../detection-pipeline
REPO     ?= .

.PHONY: help verify check geometry geometry-commit

help:
	@echo "make verify                            validate shipped fill_geometry.json (CI gate)"
	@echo "make check PIPELINE=<path>             staleness check: regenerate in memory, diff vs shipped"
	@echo "make geometry PIPELINE=<path>          regenerate geometry from pipeline outputs"
	@echo "make geometry-commit PIPELINE=<path>   regenerate + commit (push stays manual)"
	@echo "  (see docs/maintenance.md)"

verify:
	python3 scripts/verify_fill_geometry.py --repo $(REPO)

check:
	python3 scripts/regen_fill_geometry.py --pipeline-root $(PIPELINE) --repo $(REPO) --check

geometry:
	python3 scripts/regen_fill_geometry.py --pipeline-root $(PIPELINE) --repo $(REPO)

geometry-commit:
	python3 scripts/regen_fill_geometry.py --pipeline-root $(PIPELINE) --repo $(REPO) --commit
