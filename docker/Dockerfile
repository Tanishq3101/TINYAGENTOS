# Multi-stage build for a minimal final image.
#
# Adapted from the 30-day plan's draft Dockerfile with three real-code fixes:
#   1. Healthcheck hits /api/v1/health, not /health -- api/routes.py's
#      router has prefix="/api/v1", so /health alone 404s.
#   2. Dropped ENV APP_ENV=production -- infrastructure/config.py's
#      Settings class has no APP_ENV field, so it was never actually read
#      by the app; keeping it would just be a misleading no-op env var.
#   3. Base image pinned to python:3.10-slim, not 3.11 -- matches the
#      actual dev/test environment (conda env running 3.10.20, per your
#      pytest runs) rather than an arbitrary newer version. Keeps
#      C-extension dependencies (llama-cpp-python, pydantic, etc.)
#      behaving identically to what's already been tested locally,
#      instead of introducing an untested 3.10-vs-3.11 variable.
#   4. Install to /usr/local instead of `pip install --user` (/root/.local)
#      -- the runtime stage later switches to a non-root `appuser`, and
#      /root is mode 700 (root's home dir): appuser can't even traverse
#      into it to reach /root/.local/bin/uvicorn, regardless of that
#      file's own permissions. That's the exact "Permission denied"
#      error this caused. /usr/local is world-readable/traversable by
#      default, so copying that instead sidesteps the problem entirely.

# ---- Stage 1: Builder ----
FROM python:3.10-slim AS builder

WORKDIR /app

# build-essential/gcc/g++ are needed to compile llama-cpp-python from source.
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: Runtime ----
FROM python:3.10-slim

WORKDIR /app

# libgomp1 is a runtime dependency of llama-cpp-python (OpenMP).
RUN apt-get update && apt-get install -y \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local /usr/local
COPY . .

ENV PYTHONUNBUFFERED=1

# Create non-root user and hand over ownership of the app directory,
# including the models/ and logs/ mount points created below.
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/models /app/logs && \
    chown -R appuser:appuser /app

USER appuser

# Correct path: router prefix is /api/v1 (see api/routes.py).
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "import sys, requests; r = requests.get('http://localhost:8000/api/v1/health', timeout=3); sys.exit(0 if r.status_code == 200 else 1)"

EXPOSE 8000

# --host/--port here are the actual bind address for the running server;
# they intentionally do NOT come from Settings.HOST/Settings.PORT, which
# are only consulted by api/app.py's `if __name__ == "__main__":` block
# (i.e. `python api/app.py`), not by this `uvicorn` CLI invocation.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]