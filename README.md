# QuickLink — Observability Assignment

FastAPI URL shortener with Prometheus metrics, Grafana dashboards, and an ELK logging pipeline (Filebeat → Elasticsearch → Kibana), all via Docker Compose.

| Doc | Role |
|---|---|
| **`observability-assignment-checklist.md`** | Authoritative requirements checklist (mark done when artifacts work) |
| **`Markdown Live Preview.pdf`** | Official Assignment 1: Observability PDF |
| **`report.tex`** | **Deliverables report (Parts A–E) in LaTeX** — compile with `pdflatex report.tex` |
| **`NOTES.md`** | Report draft + cheat sheet (Parts A–E, queries, experiments) |
| **`README.md`** | This file — start / use / test / clean up |

Do **not** use any Make-or-Buy PDF; that is a different assignment.

---

## What it does

| Action | Endpoint | Result |
|---|---|---|
| Create short link | `POST /shorten?url=https://...` | `{"code":"Ab12Xy"}` |
| Follow short link | `GET /{code}` | `307` redirect |
| Metrics scrape | `GET /metrics` | Prometheus text |
| Cardinality demo | `POST /demo/hit` | Increments `demo_requests_total` (cap 100 unique IDs when experiment on) |
| API docs | `GET /docs` | Swagger UI |

Storage: SQLite (`links.db`) + in-memory cache.  
Logs: JSON stdout → Filebeat (JSON decode) → Elasticsearch → Kibana.  
Metrics: Prometheus client → Prometheus scrape → Grafana.

Env flags (`docker-compose.yml` → recreate app after change):

| Variable | Default | Effect |
|---|---|---|
| `FAULT_INJECT` | `0` | If `1`, every **5th** redirect sleeps 0.5s |
| `CARDINALITY_EXPERIMENT` | `0` | If `1`, `demo_requests_total` labeled by `request_id` (capped at 100) |

---

## Architecture

```
                 ┌─────────────┐
  browser/curl → │  app:8000   │──SQLite── links.db
                 │  FastAPI    │
                 └──────┬──────┘
            /metrics    │ stdout JSON logs
                 ┌──────▼──────┐         ┌────────────────┐
                 │ prometheus  │◄────────│ node-exporter   │
                 │   :9090     │         │    :9100        │
                 └──────┬──────┘         └────────────────┘
                        │
                 ┌──────▼──────┐
                 │  grafana    │
                 │   :3000     │
                 └─────────────┘

  app logs ──► filebeat ──► elasticsearch:9200 ──► kibana:5601
```

| Service | Port | Role |
|---|---|---|
| `app` | 8000 | URL shortener |
| `prometheus` | 9090 | Metrics store / scrape |
| `grafana` | 3000 | Dashboards (`admin` / `admin`) |
| `node-exporter` | 9100 | Host CPU/mem/disk/net (instance label `SUFYAN`) |
| `elasticsearch` | 9200 | Log storage |
| `kibana` | 5601 | Log UI |
| `filebeat` | — | Ships container logs to ES |

---

## Project files

```
ABD_HW/
├── app.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── prometheus.yml
├── filebeat.yml
├── grafana/provisioning/.../quicklink.json
├── observability-assignment-checklist.md
├── NOTES.md
├── README.md
├── Markdown Live Preview.pdf
├── verify-e2e.ps1
├── regression.ps1
└── .gitignore
```

---

## How to run

### Prerequisites

Docker Desktop running. Work from `c:\Users\sufya\OneDrive\Desktop\ABD_HW`.

```powershell
docker --version
docker compose version
```

### Start

```powershell
docker compose up --build -d
docker compose ps
```

Wait ~1–2 minutes for Elasticsearch + Kibana. Smoke:

```powershell
curl.exe -s -o NUL -w "app=%{http_code}`n" http://localhost:8000/docs
curl.exe -s -o NUL -w "prom=%{http_code}`n" http://localhost:9090/-/ready
curl.exe -s -o NUL -w "graf=%{http_code}`n" http://localhost:3000/api/health
```

### After code changes (prefer this — no full wipe)

```powershell
docker compose up -d --build app
```

### Stop / clean up

```powershell
docker compose down           # keep volumes
docker compose down -v        # wipe volumes (ES log history deleted)
```

### Optional: app without Docker

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload
```

---

## Use it

```powershell
curl.exe -X POST "http://localhost:8000/shorten?url=https://google.com"
curl.exe -i "http://localhost:8000/<code>"          # do NOT use -L when measuring app latency
curl.exe -i "http://localhost:8000/metrics"
1..50 | ForEach-Object { curl.exe -s -o NUL "http://localhost:8000/<code>" }
curl.exe -X POST "http://localhost:8000/demo/hit"
```

URLs: app docs `:8000/docs` · Prometheus `:9090` · Grafana `:3000` (dashboard **QuickLink Observability**) · Kibana `:5601`.

---

## Metrics (summary)

Full table + PromQL + chart meanings: **`NOTES.md` Part B**.

Types covered: Counter, Gauge, Histogram, Summary. p95 via:

```promql
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))
```

(Time window = **5m** rate.) Python Summary → average only; Histogram for percentiles.

---

## Logs

JSON fields: `timestamp`, `time`, `service`, `severity`, `message`, `request_id` (where relevant).  
Filebeat decodes them under **`ql.*`** for Kibana. Data view: `quicklink-logs-*` (`@timestamp`).  
Retention: ILM off — delete indices manually or `docker compose down -v`. Details in **`NOTES.md` Part C**.

---

## Experiments

Commands, predictions, and measured numbers: **`NOTES.md` Part E**.

- **E1:** `FAULT_INJECT=0|1` — every 5th redirect +500ms; compare p95.  
- **E2:** `CARDINALITY_EXPERIMENT=0|1` + `POST /demo/hit` — `count(demo_requests_total)` ≈ 100 vs 1.

---

## Test / verify

```powershell
powershell -ExecutionPolicy Bypass -File .\verify-e2e.ps1
powershell -ExecutionPolicy Bypass -File .\regression.ps1   # no rebuild; stack must be up
```

---

## Human-only remaining (checklist)

After compiling `report.tex` → PDF, still add: Grafana/Kibana screenshots into the PDF (or appendix), Exp1 recovery screenshot if you want a clean “near baseline” figure, optional architecture PNG, viva prep. Code/config/LaTeX report foundation is in this repo — see checklist status section.
