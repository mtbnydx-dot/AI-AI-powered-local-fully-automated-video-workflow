"""ORM tables. Timestamps are epoch seconds (float) so SQLite and PostgreSQL behave identically."""
from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Package(Base):
    __tablename__ = "packages"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64))
    episode_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    key_id: Mapped[str] = mapped_column(String(32))
    prefix: Mapped[str] = mapped_column(String(255))
    manifest: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="active")
    imported_at: Mapped[float] = mapped_column(Float)


class Shot(Base):
    __tablename__ = "shots"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)  # "<package_id>/<shot_id>"
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), index=True)
    shot_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    # blocked | generating | reviewing | repairing | needs_human | approved | rejected
    state: Mapped[str] = mapped_column(String(16), index=True)
    difficulty: Mapped[str] = mapped_column(String(8))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    round: Mapped[int] = mapped_column(Integer, default=0)
    takes_used: Mapped[int] = mapped_column(Integer, default=0)
    bonus_rounds: Mapped[int] = mapped_column(Integer, default=0)
    spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved_take_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[float] = mapped_column(Float)


class Take(Base):
    __tablename__ = "takes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    shot_pk: Mapped[str] = mapped_column(ForeignKey("shots.id"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    candidate: Mapped[int] = mapped_column(Integer)
    # queued | generating | reviewing | passed | failed | error | superseded
    status: Mapped[str] = mapped_column(String(16))
    route: Mapped[str] = mapped_column(String(32))
    seed: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    probe: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_version: Mapped[str] = mapped_column(String(128), default="")
    gpu_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "pool", "priority"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # generate | review | plan_repair
    pool: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(16))
    shot_pk: Mapped[str] = mapped_column(ForeignKey("shots.id"), index=True)
    take_id: Mapped[str | None] = mapped_column(ForeignKey("takes.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16))  # queued | leased | done | failed | cancelled
    priority: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    leased_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    not_before: Mapped[float] = mapped_column(Float, default=0.0)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    take_id: Mapped[str] = mapped_column(ForeignKey("takes.id"), index=True)
    level: Mapped[str] = mapped_column(String(16))
    reviewer: Mapped[str] = mapped_column(String(128))
    verdict: Mapped[str] = mapped_column(String(16))
    report: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float)


class HumanTask(Base):
    __tablename__ = "human_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    shot_pk: Mapped[str] = mapped_column(ForeignKey("shots.id"), index=True)
    take_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # open | resolved
    proposal: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    resolved_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16))  # active | draining | offline
    status_reason: Mapped[str] = mapped_column(String(255), default="")
    profile: Mapped[str] = mapped_column(String(64), default="")
    desired_profile: Mapped[str] = mapped_column(String(64), default="")
    pools: Mapped[dict] = mapped_column(JSON, default=dict)  # pool -> instances in the running profile
    profiles: Mapped[dict] = mapped_column(JSON, default=dict)  # profile -> {pool: instances}
    telemetry: Mapped[dict] = mapped_column(JSON, default=dict)
    inflight: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[float] = mapped_column(Float)
    profile_since: Mapped[float] = mapped_column(Float, default=0.0)
    boot_id: Mapped[str] = mapped_column(String(32), default="")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    package_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    shot_pk: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    take_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
