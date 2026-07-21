MODEL_JOBS ?= 8146 8096

.PHONY: submit dry-run local

submit:
	python3 scripts/submit.py $(MODEL_JOBS)

dry-run:
	python3 scripts/submit.py --dry-run $(MODEL_JOBS)

local:
	MODEL_JOBS='$(subst  ,,$(MODEL_JOBS))' bash run.sh
