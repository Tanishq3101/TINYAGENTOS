# Contributing & Coding Standards

## Branching
- `main` — always deployable
- `feature/<name>` — feature branches, PR into `main`
- Squash-merge; commit messages follow Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`)

## Code Style
- Formatter: `black` (line length 100) — run before every commit
- Linter: `flake8`
- Type hints required on all public functions/classes; enforced via `mypy --strict`
- No bare `except:` — always catch specific exceptions
- No secrets, credentials, or API keys committed — ever (`.env` is gitignored, `.env.example` is the template)

## Testing
- All new code requires unit tests (`tests/unit/`)
- Cross-module behavior requires integration tests (`tests/integration/`)
- Target: 85%+ coverage (`pytest --cov`)
- Run full suite before opening a PR: `pytest`

## Security Baseline (see `docs/SECURITY.md` for full policy)
- All inputs validated at the API boundary (`infrastructure/validators.py`)
- No dynamic `eval`/`exec` on user input
- Dependencies scanned with `bandit` + Dependabot/CI security workflow
- Secrets loaded only via environment variables / `.env`, never hardcoded
- All API endpoints require authentication unless explicitly documented as public

## Pull Requests
- Must pass CI (lint, type-check, tests, security scan) before merge
- Require at least one review
- Link the relevant task/issue in the PR description
