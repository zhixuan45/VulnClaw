"""Pydantic models for the Web UI backend."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

TaskCommand = Literal["run", "recon", "scan", "exploit", "persistent"]
TaskStatus = Literal["pending", "restoring", "running", "completed", "failed", "stopped"]


class TaskOptions(BaseModel):
    max_rounds: Optional[int] = Field(default=None, description="Override for autonomous rounds")
    rounds_per_cycle: Optional[int] = Field(
        default=None, description="Persistent mode rounds per cycle"
    )
    max_cycles: Optional[int] = Field(default=None, description="Persistent mode max cycles")
    cve: Optional[str] = Field(default=None, description="Exploit command CVE hint")
    cmd: Optional[str] = Field(default=None, description="Exploit command execution hint")
    only_port: Optional[int] = Field(
        default=None,
        ge=1,
        le=65535,
        description="Restrict task scope to a single port",
    )
    only_host: Optional[str] = Field(
        default=None, description="Restrict task scope to a single host"
    )
    only_path: Optional[str] = Field(
        default=None, description="Restrict task scope to a single path"
    )
    blocked_host: Optional[str] = Field(default=None, description="Explicitly blocked host")
    blocked_path: Optional[str] = Field(default=None, description="Explicitly blocked path")
    allow_actions: Optional[list[str]] = Field(
        default=None, description="Explicit allow-list for task actions"
    )
    block_actions: Optional[list[str]] = Field(
        default=None, description="Explicit block-list for task actions"
    )


class TaskCreateRequest(BaseModel):
    command: TaskCommand
    target: str
    resume: bool = True
    snapshot_id: Optional[str] = None
    options: TaskOptions = Field(default_factory=TaskOptions)


class TaskEvent(BaseModel):
    event: str
    task_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskSummary(BaseModel):
    target: str
    command: TaskCommand
    restored: bool = False
    snapshot_id: str = ""
    schema_version: int = 1
    phase: Optional[str] = None
    findings_count: int = 0
    verified_count: int = 0
    pending_count: int = 0
    executed_steps: int = 0
    resume_strategy: str = ""
    resume_reason: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    constraint_violations: list[str] = Field(default_factory=list)
    constraint_violation_events: list[dict[str, Any]] = Field(default_factory=list)


class TaskRecord(BaseModel):
    task_id: str
    command: TaskCommand
    target: str
    status: TaskStatus
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    resume: bool = True
    snapshot_id: Optional[str] = None
    options: TaskOptions = Field(default_factory=TaskOptions)
    latest_phase: Optional[str] = None
    latest_message: Optional[str] = None
    summary: Optional[TaskSummary] = None


class TargetSnapshotView(BaseModel):
    snapshot_id: str
    schema_version: int = 1
    last_saved_at: str = ""
    last_command: str = ""
    verified_findings: int = 0
    pending_findings: int = 0
    executed_steps: int = 0
    resume_strategy: str = ""


class TargetView(BaseModel):
    target: str
    schema_version: int = 1
    phase: Optional[str] = None
    findings_count: int = 0
    verified_count: int = 0
    pending_count: int = 0
    candidate_count: int = 0
    pending_verification_count: int = 0
    manual_review_count: int = 0
    resume_strategy: str = ""
    resume_reason: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    constraint_violations: list[str] = Field(default_factory=list)
    constraint_violation_events: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class TargetPreviewView(BaseModel):
    target: str
    schema_version: int = 1
    phase: Optional[str] = None
    snapshot_id: str = ""
    last_command: str = ""
    resume_strategy: str = ""
    resume_reason: str = ""
    findings_count: int = 0
    verified_count: int = 0
    pending_count: int = 0
    candidate_count: int = 0
    pending_verification_count: int = 0
    manual_review_count: int = 0
    priority_targets: list[str] = Field(default_factory=list)
    priority_recon_assets: list[str] = Field(default_factory=list)
    blocked_targets: list[str] = Field(default_factory=list)
    failed_targets: list[str] = Field(default_factory=list)
    recent_failed_steps: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    low_value_rounds: int = 0
    constraints: dict[str, Any] = Field(default_factory=dict)
    constraint_violations: list[str] = Field(default_factory=list)
    constraint_violation_events: list[dict[str, Any]] = Field(default_factory=list)


class TargetStateDiffView(BaseModel):
    target: str
    schema_version_from: int = 1
    schema_version_to: int = 1
    from_snapshot_id: str
    to_snapshot_id: str
    resume_strategy_from: str = ""
    resume_strategy_to: str = ""
    added_findings: list[str] = Field(default_factory=list)
    removed_findings: list[str] = Field(default_factory=list)
    updated_findings: list[str] = Field(default_factory=list)
    added_steps: list[str] = Field(default_factory=list)
    removed_steps: list[str] = Field(default_factory=list)
    added_notes: list[str] = Field(default_factory=list)
    removed_notes: list[str] = Field(default_factory=list)
    added_recon_assets: list[str] = Field(default_factory=list)
    removed_recon_assets: list[str] = Field(default_factory=list)


class ReportGenerateRequest(BaseModel):
    target: str
    output_path: Optional[str] = None
    report_format: str = "markdown"


class ConfigView(BaseModel):
    provider: str
    model: str
    base_url: str
    api_key_configured: bool
    output_dir: str
    max_rounds: int
    persistent_rounds_per_cycle: int
    persistent_max_cycles: int
    show_thinking: bool
    python_execute_enabled: bool
    python_execute_mode: str
    python_execute_max_lines: int
    python_execute_audit_enabled: bool


class ConfigUpdateRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    output_dir: Optional[str] = None
    max_rounds: Optional[int] = None
    persistent_rounds_per_cycle: Optional[int] = None
    persistent_max_cycles: Optional[int] = None
    show_thinking: Optional[bool] = None
    python_execute_enabled: Optional[bool] = None
    python_execute_mode: Optional[str] = None
    python_execute_max_lines: Optional[int] = None
    python_execute_audit_enabled: Optional[bool] = None


class ReportContentView(BaseModel):
    path: str
    kind: str
    content: str


class MCPServiceView(BaseModel):
    name: str
    enabled: bool
    priority: int
    transport_type: str
    execution_mode: str
    health_status: str
    attach_attempted: bool = False
    attach_succeeded: bool = False
    running: bool
    can_execute: bool
    tool_count: int = 0
    tools: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    last_error_type: Optional[str] = None
    started_at: Optional[str] = None
    description: str = ""
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0


class MCPDiagnosticsView(BaseModel):
    total_services: int = 0
    running_services: int = 0
    local_services: int = 0
    placeholder_services: int = 0
    tool_count: int = 0
    services: list[MCPServiceView] = Field(default_factory=list)


class ConstraintAuditEventView(BaseModel):
    target: str
    timestamp: str = ""
    code: str = ""
    severity: str = ""
    source: str = ""
    action: str = ""
    tool_name: str = ""
    phase: str = ""
    summary: str = ""
    detail: str = ""


class ConstraintAuditView(BaseModel):
    total_events: int = 0
    high_severity_events: int = 0
    by_source: dict[str, int] = Field(default_factory=dict)
    by_code: dict[str, int] = Field(default_factory=dict)
    recent_events: list[ConstraintAuditEventView] = Field(default_factory=list)


class StreamTokenPayload(BaseModel):
    round_num: int = 0
    event_type: str = ""
    content: str = ""
    metadata: dict = Field(default_factory=dict)
