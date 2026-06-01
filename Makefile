.PHONY: test test-unit test-e2e-wire tier1 tier2 tier3 test-e2e e2e-live install-recipes

# Default: unit tests + Tier 1 wire tests. CI-safe; no real Claude tokens.
test: test-unit test-e2e-wire

# Plain unit tests — lib + mcp + dashboard graph builder.
test-unit:
	python3 -m pytest lib/tests/ mcp/tests/ dashboard/tests/ -v

# Tier 1 — wire integration tests. Spawns a real dispatcher + dashboard
# pair on OS-assigned ports against tmpdir state; stub spawner stands in
# for taskpilot so no LLM tokens are spent. ~10s wall clock. CI-safe.
test-e2e-wire tier1:
	python3 -m pytest tests/e2e_wire/ -v

# Tier 2 — real-agent smoke. Fires the demo recipe at the running local
# dispatcher with a real claude spawn. Burns tokens, requires the live
# bundle daemons. Not for CI; invoke explicitly.
tier2:
	python3 tests/e2e_real/smoke.py

# Tier 3 — fresh-install dry-run. Boots a clean Linux container and
# attempts to walk install.txt. Slow; gated. See tests/e2e_fresh/README.md.
tier3:
	bash tests/e2e_fresh/run.sh

# Copy every example recipe in recipes/ into ~/.dispatcher/recipes/.
# Idempotent — overwrites if the destination exists. Doesn't touch
# channels.yaml; the operator still owns routing.
install-recipes:
	@mkdir -p $$HOME/.dispatcher/recipes
	@for d in recipes/*/; do \
		[ -d "$$d" ] || continue; \
		[ -f "$$d/recipe.yaml" ] || continue; \
		name=$$(basename $$d); \
		echo "  installing $$name → $$HOME/.dispatcher/recipes/$$name"; \
		mkdir -p "$$HOME/.dispatcher/recipes/$$name"; \
		cp "$$d"/recipe.yaml "$$d"/brief.json "$$d"/CLAUDE.md "$$HOME/.dispatcher/recipes/$$name/" 2>/dev/null || true; \
	done
	@echo "Done. Wire a channels.yaml route with /dispatcher:route or edit ~/.dispatcher/channels.yaml directly."

# Legacy aliases.
test-e2e: test-e2e-wire
e2e-live: tier2
