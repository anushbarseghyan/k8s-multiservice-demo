"""FastAPI service for the k8s health-check learning project.

The endpoints here are deliberately shaped around health-check experiments that
come in a later Kubernetes session:

- /healthz  -> liveness only, never touches the DB
- /readyz   -> true readiness, fails until the DB pool is usable
- /slow     -> a slow endpoint used later to blow a liveness timeout

The startup behaviour (a deliberate delay before the DB connect) exists to open
a long, observable "Running but not Ready" window. See init_db() below.
"""

import asyncio
import contextlib
import logging
import os

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")

# --- Config from environment, all with sensible defaults ---------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "demo")
DB_USER = os.getenv("DB_USER", "demo")
DB_PASSWORD = os.getenv("DB_PASSWORD", "demo")

# Deliberate delay (seconds) before the process even attempts to connect to the
# database on startup. Default is intentionally long so the not-ready window is
# easy to observe. See init_db().
STARTUP_DELAY = float(os.getenv("STARTUP_DELAY", "15"))

# How long /slow sleeps before returning. Used later to exceed a liveness probe
# timeout on purpose.
SLOW_DELAY = float(os.getenv("SLOW_DELAY", "5"))

# Connection retry policy: Postgres often isn't accepting connections the moment
# this container starts, so we retry rather than crash-looping.
CONNECT_ATTEMPTS = 30
CONNECT_RETRY_INTERVAL = 2.0

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS items (
    id    SERIAL PRIMARY KEY,
    name  TEXT NOT NULL,
    added TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def init_db(app: FastAPI) -> None:
    """Background task that brings the DB pool up.

    Runs as a background task (NOT inline in the lifespan) so the process starts
    serving HTTP immediately — that is what lets /healthz answer 200 and /readyz
    answer 503 during the startup window.
    """
    # DELIBERATE: sleep before connecting. This is not a bug and should not be
    # "fixed". It creates a long enough not-ready window to observe the gap
    # between a pod being Running and the app actually working.
    log.info("startup: sleeping %.1fs before connecting to DB", STARTUP_DELAY)
    await asyncio.sleep(STARTUP_DELAY)

    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            pool = await asyncpg.create_pool(
                host=DB_HOST,
                port=DB_PORT,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                min_size=1,
                max_size=10,
            )
            async with pool.acquire() as conn:
                await conn.execute(CREATE_TABLE_SQL)
            app.state.pool = pool
            log.info("DB pool ready after %d attempt(s)", attempt)
            return
        except Exception as exc:  # noqa: BLE001 - retry on any connect failure
            log.warning(
                "DB connect attempt %d/%d failed: %s",
                attempt,
                CONNECT_ATTEMPTS,
                exc,
            )
            await asyncio.sleep(CONNECT_RETRY_INTERVAL)

    log.error("giving up: DB pool could not be established")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Pool starts as None; readiness stays 503 until the background task sets it.
    app.state.pool = None
    task = asyncio.create_task(init_db(app))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if app.state.pool is not None:
            await app.state.pool.close()


app = FastAPI(title="k8s-multiservice-demo api", lifespan=lifespan)


class ItemIn(BaseModel):
    name: str


@app.get("/healthz")
async def healthz():
    """Liveness: 200 if the process is alive. Must NOT touch the database.

    Later this same shallow check is deliberately (mis)used as a readiness
    probe to demonstrate why a liveness-style check makes a bad readiness probe.
    """
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request):
    """Readiness: 200 only if the DB pool exists AND `SELECT 1` succeeds."""
    pool = request.app.state.pool
    if pool is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "reason": "db pool not initialized"},
        )
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "reason": f"db query failed: {exc}"},
        )
    return {"status": "ready"}


@app.get("/slow")
async def slow():
    """Sleeps SLOW_DELAY seconds then returns 200. Used later to blow a
    liveness probe timeout on purpose."""
    await asyncio.sleep(SLOW_DELAY)
    return {"status": "ok", "slept": SLOW_DELAY}


@app.get("/items")
async def list_items(request: Request):
    pool = request.app.state.pool
    if pool is None:
        return JSONResponse(
            status_code=503,
            content={"reason": "db not ready"},
        )
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, added FROM items ORDER BY id")
    return [
        {"id": r["id"], "name": r["name"], "added": r["added"].isoformat()}
        for r in rows
    ]


@app.post("/items", status_code=201)
async def create_item(item: ItemIn, request: Request):
    pool = request.app.state.pool
    if pool is None:
        return JSONResponse(
            status_code=503,
            content={"reason": "db not ready"},
        )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO items (name) VALUES ($1) RETURNING id, name, added",
            item.name,
        )
    return {
        "id": row["id"],
        "name": row["name"],
        "added": row["added"].isoformat(),
    }
