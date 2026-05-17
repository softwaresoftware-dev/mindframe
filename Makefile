.PHONY: test test-e2e e2e-live

# Hermetic tests — pytest, no daemons, no credentials, CI-safe.
# Includes the hermetic e2e suite under tests/e2e/.
test:
	python3 -m pytest tests/ -v

# Just the hermetic end-to-end suite.
test-e2e:
	python3 -m pytest tests/e2e/ -v

# Live layer — talks to the running bundle daemons. NOT for CI.
# See tests/e2e/README.md for the env vars it honors.
e2e-live:
	bash tests/e2e/live/healthcheck.sh
	bash tests/e2e/live/smoke.sh
