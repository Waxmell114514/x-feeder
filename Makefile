.PHONY: install demo test lint clean

install:
	pip install -e ".[dev]"

demo:
	xfeeder demo --fresh

test:
	pytest -q

fixtures:
	python fixtures/build_fed_rate_demo.py

clean:
	rm -rf .xfeeder out .pytest_cache **/__pycache__
