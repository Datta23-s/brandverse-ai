"""Output shapes shared by the real agents and their mocks (Architecture.md section 7)."""

from pydantic import BaseModel, ConfigDict, Field


class CreativeOutput(BaseModel):
    """What A6 returns: the copy, by part (headline, body, call to action, footer). NOT the
    statements in it and NOT which claims it rests on: the claim classifier extracts those from
    the copy at step 11, so a writer cannot mark its own homework
    (app/core/claim_classifier.py)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    copy_: dict[str, str] = Field(alias="copy")
