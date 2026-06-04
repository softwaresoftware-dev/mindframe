.PHONY: test test-unit test-contract tier3 install-recipes

# Default: unit tests + contract tests. CI-safe; no real Claude tokens.
# (The block-stream Tier-1 wire / Tier-2 real-agent suites were removed in the
# surface migration — they exercised blocks.jsonl + SSE, which no longer exist.)
test: test-unit test-contract

# Plain unit tests — dashboard graph builder + plugin manifest.
test-unit:
	python3 -m pytest dashboard/tests/ tests/test_manifest.py -v

# Contract tests — recipe / install / vault fixtures (model-agnostic).
test-contract:
	python3 -m pytest tests/e2e/ -v

# Tier 3 — fresh-install dry-run. Boots a clean env and walks install.txt.
# Slow; gated. NOTE: the harness still references the removed block-stream
# path (frame -> blocks -> SSE) and needs updating for the surface model.
tier3:
	bash tests/e2e_fresh/run.sh

# Copy example recipes from recipes/ into ~/.dispatcher/recipes/. No recipes
# ship in-tree right now — the block-stream demo (mindframe-poc) was removed in
# the surface migration; a surface recipe will be added in a later step. Kept
# so the target works once one lands.
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
	@echo "Done."
