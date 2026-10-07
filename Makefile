.PHONY: install install-optional ingest ask eval ablate test doctor es-up es-down

install:
	pip install -r requirements.txt

install-optional: install
	pip install -r requirements-optional.txt

ingest:
	python -m corvex_rag.cli ingest

ask:
	python -m corvex_rag.cli ask "$(Q)"

eval:
	python -m corvex_rag.cli eval $(ARGS)

ablate:
	python -m corvex_rag.cli ablate --axis $(AXIS)

test:
	python -m pytest

doctor:
	python -m corvex_rag.cli doctor

es-up:
	docker compose up -d

es-down:
	docker compose down
