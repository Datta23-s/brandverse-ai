"""Payloads and results of the system jobs (the queue's own smoke test)."""

from pydantic import BaseModel, ConfigDict, Field


class PingJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(min_length=1, max_length=200)


class PingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str
    worker_pid: int
