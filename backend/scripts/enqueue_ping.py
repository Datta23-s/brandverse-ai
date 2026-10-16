"""Enqueue the trivial `ping` job and wait for the running worker to complete it.

Usage (from backend/, with Redis and `python -m app.workers` running):
    uv run python -m scripts.enqueue_ping [message]
Exit code 0 if the job completed within the timeout, 1 otherwise.
"""

import asyncio
import sys
import time

from app.core.config import get_settings
from app.core.redis import create_arq_pool
from app.models.jobs import PingJob, PingResult
from app.workers.jobs import PING

TIMEOUT_S = 15.0


async def main(message: str) -> int:
    pool = await create_arq_pool(get_settings())
    try:
        started = time.monotonic()
        job = await pool.enqueue_job(PING, PingJob(message=message).model_dump())
        if job is None:
            print("not enqueued")
            return 1
        print(f"enqueued job {job.job_id}")
        try:
            result = PingResult.model_validate(await job.result(timeout=TIMEOUT_S))
        except TimeoutError:
            print(f"no result within {TIMEOUT_S}s: is the worker running?")
            return 1
        elapsed_ms = (time.monotonic() - started) * 1000
        print(f"completed in {elapsed_ms:.0f} ms by worker pid {result.worker_pid}")
        print(f"result: {result.message!r}")
        return 0
    finally:
        await pool.aclose(close_connection_pool=True)


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "ping"
    sys.exit(asyncio.run(main(msg), loop_factory=asyncio.SelectorEventLoop))
