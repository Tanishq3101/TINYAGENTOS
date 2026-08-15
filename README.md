# TinyAgentOS

A production-grade, resource-aware multi-agent AI framework for running LLM workflows on edge devices.

## Quick Start

### Installation (Conda)
```bash
git clone https://github.com/yourusername/tinyagentos.git
cd tinyagentos
conda create -n tinyagentos python=3.11 -y
conda activate tinyagentos
pip install -r requirements.txt
python scripts/download_model.py
```

### Basic Usage
```python
from core.orchestrator import Orchestrator
from agents.summarizer import SummarizerAgent
from core.llm_runtime import LLMRuntime

# Initialize
llm = LLMRuntime("./models/phi-3-mini.Q4_K_M.gguf")
agents = {'summarizer': SummarizerAgent(...)}
orchestrator = Orchestrator(agents)

# Execute
task_id = orchestrator.create_task("Your text here")
results = orchestrator.execute_pipeline(task_id)
```

### API
```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "X-API-Key: sk-your-key" \
  -H "Content-Type: application/json" \
  -d '{"text": "Your input here"}'
```

## Features
- ✅ Multi-agent pipeline execution
- ✅ Resource-aware scheduling
- ✅ RESTful API
- ✅ Production-grade logging
- ✅ Comprehensive monitoring
- ✅ Docker & Kubernetes support
- ✅ Extensive test coverage

## Technology Stack

**Backend:**
- Python 3.11+ (via Conda)
- FastAPI (async framework)
- SQLAlchemy 2.0 (ORM)
- Phi-3 Mini (3.8B parameters)
- llama.cpp (inference engine)

**Infrastructure:**
- Docker (containerization)
- Docker Compose (orchestration)
- Kubernetes (production)
- Prometheus (monitoring)
- GitHub Actions (CI/CD)

**Testing:**
- pytest (unit & integration)
- Locust (load testing)
- Bandit (security scanning)
- Coverage.py (coverage analysis)

## Performance Targets

| Metric | Target |
|--------|--------|
| P95 Latency | < 3 sec |
| Throughput | > 0.5 tasks/sec |
| Test Coverage | > 85% |
| Memory (idle) | < 300 MB |
| Uptime | > 99.9% |

## Documentation
- [API Documentation](docs/API.md)
- [Architecture Guide](docs/ARCHITECTURE.md)
- [Security Guide](docs/SECURITY.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Troubleshooting Guide](docs/TROUBLESHOOTING.md)

## Support
See [Troubleshooting Guide](docs/TROUBLESHOOTING.md) for common issues, or open a GitHub Issue with reproduction steps and logs.

---

## 📦 About the Implementation Plan

This repo was built from a **30-day production implementation plan**, made up of 4 planning documents:

| Document | Purpose |
|----------|---------|
| **IMPLEMENTATION_OVERVIEW.md** | Executive summary, timeline, architecture, success metrics |
| **TinyAgentOS_30Day_Production_Plan.md** | Day-by-day build playbook with specs and code examples |
| **TinyAgentOS_Technical_Specifications.md** | Security architecture, performance, monitoring, testing, deployment, compliance |
| **TinyAgentOS_Quick_Reference.md** | Daily checklist, quick-start commands, troubleshooting |

### Project At a Glance
```
Timeline:        30 days (8 hours/day = 240 total hours)
Team Size:       1-2 developers
Difficulty:      Advanced (requires Python, Docker, K8s knowledge)
Code Output:     ~8,500 lines of production Python
Test Cases:      50+ (unit, integration, E2E, performance)
```

### Build Milestones

**Week 1 (Days 1-5):** project structure, config management, structured logging, security utilities, database layer

**Week 2 (Days 6-10):** LLM runtime (Phi-3 Mini via llama.cpp), 3 specialized agents (Summarizer, Extractor, Critic), orchestration engine, resource monitoring

**Week 3 (Days 11-15):** FastAPI REST API, auth, test suite (>85% coverage), Docker + Kubernetes manifests

**Week 4 (Days 16-30):** documentation, performance optimization, monitoring/alerting, CI/CD, security hardening, release

### Daily Workflow
1. Read the day's section in `TinyAgentOS_30Day_Production_Plan.md`
2. Use `TinyAgentOS_Quick_Reference.md` for exact commands
3. Run tests to verify the implementation
4. Update progress log and check off deliverables
5. Commit

### Role-Based Reading Guide
- **Project Managers** → `IMPLEMENTATION_OVERVIEW.md`
- **Backend Developers** → `TinyAgentOS_30Day_Production_Plan.md`, with `TinyAgentOS_Technical_Specifications.md` for deep dives
- **DevOps Engineers** → Days 14-15 and 24 of the 30-day plan, plus Kubernetes sections in Technical Specifications
- **QA Engineers** → Day 13 (testing), plus the Testing Strategy section in Technical Specifications

### FAQ

**Can we do this faster than 30 days?** Theoretically with more developers, but more people adds coordination overhead.

**Do we need to follow every detail?** Solutions are production-grade; customize as needed, but security and testing are non-negotiable.

**What if we get stuck?** Check the [Troubleshooting Guide](docs/TROUBLESHOOTING.md) or the detailed steps in the 30-day plan.

**Can we swap technologies?** Yes, with adaptation — the plan is optimized for the stack listed above.

**What happens after Day 30?** System is production-ready; Phase 2 (hardware-aware scheduling, parallel execution, etc.) can begin.

### Success Criteria (Day 30)
- [ ] All code written and tested
- [ ] >85% test coverage achieved
- [ ] Security audit passed
- [ ] Performance targets met
- [ ] Documentation complete
- [ ] CI/CD pipelines operational
- [ ] Deployment procedures tested
- [ ] Team trained on operations

---

**Version:** 1.0
**Status:** PRODUCTION READY
**Estimated Duration:** 30 days (240 hours)
**Difficulty:** Advanced