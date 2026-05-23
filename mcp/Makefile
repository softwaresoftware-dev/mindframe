.PHONY: install check test

install:
	python3 -m pip install -r requirements.txt

check:
	python3 -c "import server" 2>&1 | grep -v '^$$' || echo "import ok"

test:
	python3 -m pytest tests/ -v
