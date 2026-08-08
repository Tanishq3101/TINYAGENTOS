# TinyAgentOS

Production-grade multi-agent AI framework powered by Phi-3 Mini (GGUF).

**Status:** 🚧 Phase 1 — Day 1/30 (project scaffold)

## Quickstart (Development)

```bash
# Clone
git clone <repo-url> tinyagentos
cd tinyagentos

# Create environment
conda env create -f environment.yml
conda activate tinyagentos

# Configure
cp .env.example .env
# edit .env and set a real JWT_SECRET

# Run tests
pytest

# Run the API (once api/app.py exists)
uvicorn api.app:app --reload
```

## Project Structure

See `docs/ARCHITECTURE.md` (Week 4) for the full architecture guide. Top-level layout:

```
core/            # Orchestration, agent base, LLM runtime, pipeline
agents/          # Specialized agents (summarizer, extractor, critic)
infrastructure/  # Logging, config, security, validators
storage/         # Database, cache, models
api/             # FastAPI app, routes, schemas, middleware
tests/           # Unit, integration, performance tests
scripts/         # Setup, model download, benchmarks
config/          # YAML configs (default, production, logging, security)
docker/          # Dockerfile(s), docker-compose
docs/            # API, architecture, security, deployment docs
```

## Development Standards

- Formatting: `black` (line length 100)
- Linting: `flake8`
- Type checking: `mypy --strict`
- Security scanning: `bandit`
- Pre-commit hooks enforce all of the above — see `.pre-commit-config.yaml`

## License

MIT
