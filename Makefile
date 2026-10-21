.PHONY: setup run test demo record sample clean

setup:
	python -m venv .venv
	.venv/bin/pip install -r requirements.txt || .venv\Scripts\pip install -r requirements.txt
	cp -n .env.example .env 2>/dev/null || true

run:
	uvicorn backend.app.main:app --reload || .venv\Scripts\uvicorn backend.app.main:app --reload

test:
	pytest backend/tests -q || .venv\Scripts\python -m pytest backend/tests -q

sample:
	python backend/scripts/make_sample_pdfs.py || .venv\Scripts\python backend/scripts/make_sample_pdfs.py

record:
	python backend/scripts/record_demo.py || .venv\Scripts\python backend/scripts/record_demo.py

clean:
	rm -rf data/runs/* data/cache/* .pytest_cache backend/**/__pycache__
