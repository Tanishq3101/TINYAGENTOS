# Release Checklist — v0.1.0

## Pre-Release

- [ ] Merge all pending PRs (branch protection isn't configured yet —
      see Known Issues in `RELEASE_NOTES.md` — so this is manual for
      now, not enforced)
- [ ] Confirm the latest `main` CI run is green — `ci.yml` already
      runs flake8, black, mypy, bandit (`-ll`, fails on medium/high),
      pip-audit, the full pytest suite with coverage, and a live
      docker-compose smoke test with a seeded `sk-test` key on every
      push. Nothing here needs to be re-run manually if CI passed.
- [ ] **Set `FERNET_KEY` before deploying** — `docker-compose.yml`'s
      `environment:` block was missing a `FERNET_KEY=${FERNET_KEY}`
      line entirely, meaning encryption at rest was silently inactive
      in any docker-compose deployment even though the Day 28
      checklist marked it done. Fixed in the copy delivered alongside
      this checklist — confirm you're deploying from that version, and
      that a real `FERNET_KEY` is set in your deployment `.env`
      (generate one the same way as `SECRET_KEY`, via
      `python -c "import secrets; print(secrets.token_urlsafe(64))"`)
- [ ] Confirm `SECRET_KEY` is a real 32+ char value in the target
      environment's `.env` — `config.py`'s validator refuses a
      placeholder, but only at app startup, so a missing/weak value
      still needs to exist before you deploy, not just before you test
- [ ] If your model weights aren't in `models/` on the deploy target,
      confirm `TINYAGENT_SKIP_LLM_LOAD` is **unset** (or `0`) —
      `docker-compose.yml` defaults it to `0`, but it's worth a manual
      double-check since CI intentionally sets it to `1`
- [ ] If deploying `k8s/ingress.yaml`: confirm `ingress-nginx` and
      `cert-manager` are actually installed in the target cluster —
      neither is verified by the manifest itself
- [ ] If deploying `k8s/backup-cronjob.yaml`: generate the real backup
      script ConfigMap via `kubectl create configmap ... --from-file
      scripts/backup_db.sh` (the YAML alone is a placeholder), and
      confirm your storage class supports the PVC being mounted by
      both the app pod and the backup Job
- [ ] Replace `tinyagentos.example.com` and `letsencrypt-prod` in
      `k8s/ingress.yaml` with your real domain and ClusterIssuer
- [ ] Confirm `APP_VERSION` in `config.py` matches the tag you're
      about to cut (`0.1.0`) — there's no `CHANGELOG.md` in this repo
      yet, so this version check is the only pre-release source of
      truth until one exists

## Release Day

- [ ] Tag release: `git tag v0.1.0`
- [ ] Push tag: `git push origin v0.1.0`
- [ ] Build Docker image
- [ ] Push to your registry
- [ ] Create GitHub release, attach `RELEASE_NOTES.md`
- [ ] Apply k8s manifests if deploying there: `kubectl apply -f k8s/`
      then `kubectl rollout status deployment/tinyagentos`

## Post-Release (24 hours after)

- [ ] Monitor logs for errors (`logs/app.json` or
      `docker-compose logs -f`)
- [ ] Watch the stall watchdog / error tracker output specifically —
      this is new since Day 20 and hasn't seen real production traffic
      yet
- [ ] Verify `/api/v1/health` reports `model_loaded: true` in the
      deployed environment, not just locally
- [ ] Confirm the backup CronJob actually ran and produced a real,
      decryptable backup in S3 — don't assume the YAML applying
      cleanly means the job succeeded
- [ ] Address critical issues; release a 0.1.1 hotfix if needed

## Explicitly Not Blocking This Release

(Carried over from Day 28's open items — real, but not release
blockers)

- GitHub branch protection / required PR review — a settings change,
  not a code change; do it when convenient
- Real end-to-end throughput measurement under concurrent load — the
  extrapolated number in `RELEASE_NOTES.md` is good enough to ship on;
  measuring it for real is follow-up work, not a gate