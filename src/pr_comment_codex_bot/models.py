from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

SessionStatus = Literal[
    "interviewing",
    "ready_to_implement",
    "implementing",
    "implemented",
    "blocked",
]


class RepoRef(BaseModel):
    owner: str
    name: str
    full_name: str


class PullRequestRef(BaseModel):
    number: int
    title: str
    body: str | None = None
    base_ref: str
    head_ref: str
    head_sha: str
    clone_url: str
    html_url: str


class PullRequestContext(BaseModel):
    repo: RepoRef
    pr: PullRequestRef
    latest_comment: dict[str, Any]
    issue_comments: list[dict[str, Any]] = Field(default_factory=list)
    review_comments: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    commits: list[dict[str, Any]] = Field(default_factory=list)
    files: list[dict[str, Any]] = Field(default_factory=list)
    diff: str = ""


class InterviewQuestion(BaseModel):
    question: str
    recommended_answer: str
    why_it_matters: str | None = None


class InterviewDecision(BaseModel):
    status: Literal["needs_answer", "ready_to_implement", "blocked"]
    reply_body: str
    questions: list[InterviewQuestion] = Field(default_factory=list)
    resolved_decisions: list[str] = Field(default_factory=list)
    unresolved_decisions: list[str] = Field(default_factory=list)
    codebase_evidence: list[str] = Field(default_factory=list)
    implementation_brief: str | None = None


class SessionState(BaseModel):
    repo_full_name: str
    pr_number: int
    status: SessionStatus = "interviewing"
    resolved_decisions: list[str] = Field(default_factory=list)
    unresolved_decisions: list[str] = Field(default_factory=list)
    implementation_brief: str | None = None
    debate_job_id: str | None = None
    debate_thread_id: str | None = None
    codex_job_id: str | None = None
    codex_thread_id: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    last_processed_comment_id: int | None = None
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def apply_decision(self, decision: InterviewDecision) -> None:
        self.resolved_decisions = decision.resolved_decisions
        self.unresolved_decisions = decision.unresolved_decisions
        self.implementation_brief = decision.implementation_brief
        if decision.status == "ready_to_implement":
            self.status = "ready_to_implement"
        elif decision.status == "blocked":
            self.status = "blocked"
        else:
            self.status = "interviewing"
        self.updated_at = datetime.now(timezone.utc)


class CodexJobResult(BaseModel):
    job_id: str | None = None
    thread_id: str | None = None
    status: str = "unknown"
    summary: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    error: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CodexDebateResult(BaseModel):
    decision: InterviewDecision
    job_id: str | None = None
    thread_id: str | None = None
    status: str = "completed"
    raw: dict[str, Any] = Field(default_factory=dict)
