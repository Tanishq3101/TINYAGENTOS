# Bug Report Template

## Description
Clear description of the issue

## Steps to Reproduce
1. Step 1
2. Step 2
3. Step 3

## Expected Behavior
What should happen

## Actual Behavior
What actually happens

## Environment
- OS:
- Python version (conda env): 3.11
- TinyAgentOS version (`APP_VERSION` in config): 0.1.0
- Deployment mode: local / Docker / Kubernetes
- GPU layers (`N_GPU_LAYERS`): 0 (CPU-only default) / other
- Model (`MODEL_PATH`):

## Task Context (if applicable)
- `task_id`:
- `task_type`: full_pipeline / summarize / extract / evaluate
- Task status at time of bug (`PENDING` / `RUNNING` / `COMPLETED` / `FAILED` / `CANCELLED`):

## Logs
```
Paste relevant excerpt from logs/app.json here (never paste raw task input — only length/fingerprint are logged by design)
```

## Severity
- [ ] Critical (system crash)
- [ ] High (major feature broken)
- [ ] Medium (minor feature issue)
- [ ] Low (cosmetic)
