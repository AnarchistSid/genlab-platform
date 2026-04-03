# Contributing to GenLab

Thank you for your interest in contributing to GenLab.

## Development Setup

1. Install prerequisites: Python 3.12+, PostgreSQL, FFmpeg, [uv](https://docs.astral.sh/uv/)
2. Clone the repository and install dependencies:
   ```bash
   git clone <repo-url> && cd GenLab
   uv sync
   ```
3. Copy `.env.example` to `.env` and fill in the required API keys.
4. Verify the setup by running the test suite (see below).

## Running Tests

```bash
# Run genlab-core tests
uv run --package genlab-core pytest genlab-core/tests/ -x

# Run a specific channel's tests
uv run --package criticalrush pytest CriticalRush/tests/ -x

# Run dashboard tests
uv run --package dashboard pytest dashboard/tests/ -x
```

## Code Style

- **Linter/formatter**: [Ruff](https://docs.astral.sh/ruff/) (configured in `pyproject.toml`). Run `ruff check --fix . && ruff format .` before committing.
- **Type hints**: Use strict typing on all new code. Pydantic v2 for schemas.
- **Commit messages**: Use [Conventional Commits](https://www.conventionalcommits.org/) -- `feat(scope)`, `fix(scope)`, `refactor(scope)`, etc.

## Pull Request Process

1. Create a feature branch from `main`.
2. Make your changes with tests for any new functionality.
3. Ensure all tests pass and `ruff check` reports no errors.
4. Open a pull request with a clear description of the change and its motivation.
5. PRs require at least one approving review before merge.

## Architecture Guidelines

- All new shared code goes in `genlab-core/src/genlab_core/`.
- Never add shared code to individual channel directories.
- Read existing patterns before writing new code (e.g., read `backlog_client.py` before writing a new client).
- All pipeline stages accept and return typed Pydantic models.
- Never hardcode credentials -- all secrets belong in `.env` files.

## Reporting Issues

Open a GitHub issue with a clear description, steps to reproduce, and expected vs actual behavior.

For security vulnerabilities, see [SECURITY.md](SECURITY.md).
