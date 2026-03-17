.PHONY: test test-core test-bb test-cr test-cw test-sr test-fd test-dash lint fix clean plist-status plist-list health logs logs-errors logs-stats db-stats db-vacuum

UV := ~/.local/bin/uv

# Run all tests across all packages
test: test-core test-bb test-cr test-cw test-sr test-fd test-dash

test-core:
	$(UV) run --package genlab-core pytest genlab-core/tests/ -k "not postgres and not integration" --tb=short -q

test-bb:
	$(UV) run --package blackbox-brief pytest BlackboxBrief/tests/ --tb=short -q

test-cr:
	$(UV) run --package criticalrush pytest CriticalRush/tests/ --tb=short -q

test-cw:
	$(UV) run --package clutchwire pytest ClutchWire/tests/ --tb=short -q

test-sr:
	$(UV) run --package splicereel pytest SpliceReel/tests/ --tb=short -q

test-fd:
	$(UV) run --package framedrift pytest FrameDrift/tests/ --tb=short -q

# Lint
lint:
	$(UV) tool run ruff check .

# Auto-fix lint issues
fix:
	$(UV) tool run ruff check . --fix

test-dash:
	$(UV) run --package genlab-dashboard pytest dashboard/tests/ --tb=short -q

# LaunchAgent management
plist-status:
	@echo "=== Active GenLab LaunchAgents ==="
	@for p in ~/Library/LaunchAgents/com.genlab.*.plist; do \
		label=$$(basename "$$p" .plist); \
		pid=$$(launchctl list 2>/dev/null | grep "$$label" | awk '{print $$1}'); \
		if [ -n "$$pid" ] && [ "$$pid" != "-" ]; then \
			echo "  [RUNNING pid=$$pid] $$label"; \
		else \
			echo "  [STOPPED]          $$label"; \
		fi; \
	done
	@echo ""
	@echo "Total: $$(ls ~/Library/LaunchAgents/com.genlab.*.plist 2>/dev/null | wc -l | tr -d ' ') plists"

plist-list:
	@ls ~/Library/LaunchAgents/com.genlab.*.plist 2>/dev/null | xargs -I{} basename {} .plist | sort

# Health check
health:
	$(UV) run --package genlab-core python -m genlab_core.monitoring.token_health

# Log aggregation
logs:
	python3 scripts/log_aggregator.py -n 100

logs-errors:
	python3 scripts/log_aggregator.py --errors -n 50

logs-stats:
	python3 scripts/log_aggregator.py --stats

# Database maintenance
db-stats:
	@PGPASSWORD=genlab_dev psql -h localhost -p 5432 -U genlab -d genlab -c "SELECT relname AS table, pg_size_pretty(pg_total_relation_size(relid)) AS size, n_live_tup AS rows FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC;"

db-vacuum:
	./scripts/db_maintenance.sh

# Clean ephemeral artifacts
clean:
	find . -name "__pycache__" -type d -not -path "*/node_modules/*" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned __pycache__ and .pytest_cache"
