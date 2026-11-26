"""Request bodies."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class DecisionIn(BaseModel):
    action: Literal["approve", "reject", "edit", "defer"]
    note: str = Field(default="", max_length=1000)
    proposed_value: str | None = Field(default=None, max_length=2000)


class CommitmentIn(BaseModel):
    description: str = Field(min_length=3, max_length=500)
    customer_id: str
    opportunity_id: str | None = None
    owner_name: str = Field(min_length=2, max_length=200)
    owner_side: str
    importance: str
    due_date: date
    status: str = "New"


class CommitmentPatch(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    owner_name: str | None = None
    owner_side: str | None = None
    importance: str | None = None
    due_date: date | None = None
    status: str | None = None
    note: str | None = Field(default=None, max_length=1000)


class SettingsPatch(BaseModel):
    poll_interval_minutes: int = Field(ge=5, le=1440)
