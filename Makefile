.PHONY: install demo test fixtures clean

install:
	pip install -e ".[dev]"

demo:
	chorus demo --fresh

test:
	pytest -q

fixtures:
	python fixtures/build_fed_rate_demo.py

clean:
	rm -rf .chorus out .pytest_cache **/__pycache__
