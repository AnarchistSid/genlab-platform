# Code Style & Conventions

## Python
- Python 3.14, type hints used throughout
- Strategy classes per niche: `bb_strategies/`, `cw_strategies/`, `sr_strategies/`, `fd_strategies/`
- Prefix avoids sys.modules collisions when running all tests together
- Conventional commits: `feat(scope)`, `fix(scope)`, `refactor(scope)`
- Deterministic stable IDs via SHA-256 (story_id, candidate_id, etc.)
- All pipeline stages are idempotent (upserts, safe to re-run)
- genlab-core uses src-layout (`src/genlab_core/`)
- hatchling build system for genlab-core
- import-linter enforces layer boundaries

## TypeScript (Dashboard Frontend)
- React 19 + TypeScript strict mode
- Vite bundler
- Niche registry pattern (`niches/registry.ts`) — typed NicheDefinition with accent colors, pipeline stages, detail views

## Testing
- pytest for all Python packages
- No mocking of external services unless necessary
- Test files prefixed with `test_`
- Fixtures in `tests/fixtures/`, golden outputs in `tests/golden/`

## Task Completion Checklist
1. Run the relevant test suite(s) — all must pass
2. If touching genlab-core, run genlab-core tests AND downstream consumer tests
3. Verify no import errors across packages
4. Do NOT commit unless explicitly asked
