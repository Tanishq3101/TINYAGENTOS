# Prometheus + Grafana — now fully wired

Everything's done in code now: `requirements.txt` already had
`prometheus-client==0.19.0`, and `app.py` / `docker-compose.yml` are
updated in this batch. Nothing left to hand-edit — just place the files
and bring it up.

## File placement (relative to your project root, `D:\tinyagentos`)

```
docker/
├── docker-compose.yml       ← replace with the updated one
├── prometheus.yml           ← new
└── grafana/
    └── provisioning/
        ├── datasources/prometheus.yml   ← new
        └── dashboards/
            ├── dashboards.yml            ← new
            └── json/tinyagentos.json     ← new

api/
├── app.py                   ← replace with the updated one
└── routes.py                ← already updated in the previous batch

core/orchestrator.py         ← already updated in the previous batch
infrastructure/prometheus_metrics.py  ← already added in the previous batch
api/dependencies.py          ← updated in this batch (auth failure counter)
```

## Bring it up

```bash
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
curl http://localhost:8000/metrics
```

Check http://localhost:9090/targets — `tinyagentos` job should read UP.
Then http://localhost:3000 (`admin` / `admin`, from
`GF_SECURITY_ADMIN_PASSWORD` in the compose file — change it) — the
TinyAgentOS dashboard is already provisioned, no manual import.

## Still open

- `/metrics` stays unauthenticated on purpose (see the comment in
  `api/routes.py`) since only Prometheus, inside the compose network,
  hits it. If port 8000 is ever exposed publicly, revisit that.
- `GF_SECURITY_ADMIN_PASSWORD=admin` is a placeholder — put a real value
  in `.env` and reference it as `${GRAFANA_ADMIN_PASSWORD}` before this
  goes anywhere but your machine.