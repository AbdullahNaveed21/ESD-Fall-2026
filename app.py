from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
import sqlite3
import string
import random
import time
import json
import os
import uuid
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

app = FastAPI()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("quicklink")

# Per-request id for logs (set in middleware)
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Cardinality experiment: hard cap unique request_id labels (assignment: stay at 100)
_CARDINALITY_CAP = 100
_seen_request_ids: set[str] = set()
_redirect_count = 0


def log_json(severity, message, **fields):
    """Emit one JSON log line to stdout (picked up by Filebeat). Never log secrets/PII."""
    rid = fields.pop("request_id", None) or _request_id_ctx.get()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "time": time.time(),
        "service": "quicklink",
        "severity": severity,
        "message": message,
    }
    if rid:
        entry["request_id"] = rid
    entry.update(fields)
    print(json.dumps(entry), flush=True)


db = sqlite3.connect("links.db", check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS links "
    "(code TEXT PRIMARY KEY, url TEXT, created_at REAL)"
)

cache = {}

links_created_total = Counter(
    "links_created_total",
    "total short links created",
)

links_stored_gauge = Gauge(
    "links_stored_total",
    "total short links currently in the database",
)

requests_failed_total = Counter(
    "http_requests_failed_total",
    "failed requests",
    ["endpoint"],
)

requests_in_progress = Gauge(
    "requests_in_progress",
    "requests currently being handled",
)

request_duration = Histogram(
    "http_request_duration_seconds",
    "request duration",
    ["endpoint"],
    buckets=[0.1, 0.5, 1],
)

lookup_duration = Summary(
    "redirect_lookup_duration_seconds",
    "time to resolve a short code",
)

# Experiment 2 demo: high-cardinality counter (do NOT use unbounded labels in real metrics)
demo_requests_total = Counter(
    "demo_requests_total",
    "cardinality experiment counter",
    ["request_id"] if os.getenv("CARDINALITY_EXPERIMENT", "0") == "1" else [],
)

FAULT_INJECT = os.getenv("FAULT_INJECT", "0") == "1"
CARDINALITY_EXPERIMENT = os.getenv("CARDINALITY_EXPERIMENT", "0") == "1"

# Initialise gauge from existing DB rows (survives app restart with same volume/file)
links_stored_gauge.set(db.execute("SELECT COUNT(*) FROM links").fetchone()[0])


def gen_code():
    return "".join(
        random.choices(
            string.ascii_letters + string.digits,
            k=6,
        )
    )


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    token = _request_id_ctx.set(request_id)
    requests_in_progress.inc()
    start = time.time()

    try:
        response = await call_next(request)

        duration = time.time() - start

        request_duration.labels(
            endpoint=request.url.path
        ).observe(duration)

        log_json(
            "INFO",
            "request handled",
            request_id=request_id,
            path=request.url.path,
            status=response.status_code,
            duration=duration,
        )

        return response

    except Exception as e:
        requests_failed_total.labels(
            endpoint=request.url.path
        ).inc()

        duration = time.time() - start

        request_duration.labels(
            endpoint=request.url.path
        ).observe(duration)

        log_json(
            "ERROR",
            "request failed",
            request_id=request_id,
            path=request.url.path,
            error=str(e),
            duration=duration,
        )

        raise

    finally:
        requests_in_progress.dec()
        _request_id_ctx.reset(token)


@app.post("/shorten")
def shorten(url: str):
    code = gen_code()

    db.execute(
        "INSERT INTO links VALUES (?, ?, ?)",
        (code, url, time.time()),
    )
    db.commit()

    cache[code] = url

    links_created_total.inc()

    total_links = db.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    links_stored_gauge.set(total_links)

    log_json(
        "INFO",
        "link created",
        code=code,
        # Log destination host only — avoid query-string secrets/tokens
        url_host=url.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0],
    )

    return {"code": code}


@app.get("/metrics")
def metrics():
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/demo/hit")
def demo_hit(request_id: str | None = None):
    """Cardinality experiment endpoint. Enable with CARDINALITY_EXPERIMENT=1.
    Caps unique request_id labels at 100 (assignment requirement).
    """
    rid = request_id or str(uuid.uuid4())
    if CARDINALITY_EXPERIMENT:
        if rid not in _seen_request_ids and len(_seen_request_ids) >= _CARDINALITY_CAP:
            # Reuse an existing id so series count stays at 100
            rid = next(iter(_seen_request_ids))
        _seen_request_ids.add(rid)
        demo_requests_total.labels(request_id=rid).inc()
        series = len(_seen_request_ids)
    else:
        demo_requests_total.inc()
        series = 1
    return {
        "ok": True,
        "request_id": rid,
        "cardinality_mode": CARDINALITY_EXPERIMENT,
        "unique_ids_tracked": series,
        "cap": _CARDINALITY_CAP if CARDINALITY_EXPERIMENT else None,
    }


@app.get("/{code}")
def redirect(code: str):
    global _redirect_count
    start = time.time()

    # Fault injection: every 5th redirect sleeps 500ms (reversible via FAULT_INJECT=0)
    _redirect_count += 1
    if FAULT_INJECT and (_redirect_count % 5 == 0):
        time.sleep(0.5)

    if code in cache:
        url = cache[code]

    else:
        row = db.execute(
            "SELECT url FROM links WHERE code=?",
            (code,),
        ).fetchone()

        if not row:
            requests_failed_total.labels(
                endpoint="/redirect"
            ).inc()

            log_json(
                "WARN",
                "link not found",
                code=code,
            )

            return JSONResponse(
                {"error": "not found"},
                status_code=404,
            )

        url = row[0]
        cache[code] = url

    lookup_duration.observe(
        time.time() - start
    )

    return RedirectResponse(url)
