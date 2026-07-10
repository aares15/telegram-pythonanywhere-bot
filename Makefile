.PHONY: install test run

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

test:
	.venv/bin/pytest tests/ -v

# Run the bot locally via polling. Reads .env automatically.
# Prompts before touching an already-registered webhook.
run:
	@test -f .env || { echo "ERROR: .env not found. Copy .env.example to .env first."; exit 1; }
	.venv/bin/python run_local.py
