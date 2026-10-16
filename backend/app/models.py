"""
SQLModel table definitions.

Core entities:
  User, Project, ProjectMember, Invitation, AccountInvitation, SystemSetting,
  TaxonomyNode, Annotation, FinalDecision,
  Item, ItemRef, TableUpload

All primary keys are autoincrement integers.  Username is indexed and unique.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    """Naive UTC timestamp for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── User ──────────────────────────────────────────────────────

class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int = Field(default=None, primary_key=True)
    username: str = Field(max_length=40, index=True, unique=True, nullable=False)
    display_name: str
    hashed_password: str
    is_admin: bool = Field(default=False)
    language: str = Field(default="en", max_length=5)  # "en", "de", …
    created_at: datetime = Field(default_factory=utcnow)

    # relationships
    owned_projects: list["Project"] = Relationship(
        back_populates="owner",
        sa_relationship_kwargs={"foreign_keys": "Project.owner_id"},
    )
    memberships: list["ProjectMember"] = Relationship(
        back_populates="user", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    annotations: list["Annotation"] = Relationship(
        back_populates="annotator", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    decisions: list["FinalDecision"] = Relationship(
        back_populates="resolved_by_user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


# ── Project ───────────────────────────────────────────────────

class Project(SQLModel, table=True):
    __tablename__ = "projects"

    id: int = Field(default=None, primary_key=True)
    name: str
    mode: str = Field(default="split")  # "split" | "verification"
    k_verifiers: int = Field(default=1)  # reviewers per item in verification mode
    created_at: datetime = Field(default_factory=utcnow)
    # Persistent batch assignments: {username: [item_id, ...]}
    batches: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    owner_id: int = Field(foreign_key="users.id", nullable=False)

    # relationships
    owner: "User" = Relationship(
        back_populates="owned_projects",
        sa_relationship_kwargs={"foreign_keys": "Project.owner_id"},
    )
    members: list["ProjectMember"] = Relationship(
        back_populates="project", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    taxonomy_nodes: list["TaxonomyNode"] = Relationship(
        back_populates="project", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    annotations: list["Annotation"] = Relationship(
        back_populates="project", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    final_decisions: list["FinalDecision"] = Relationship(
        back_populates="project", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    invitations: list["Invitation"] = Relationship(
        back_populates="project", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    item_refs: list["ProjectItemRef"] = Relationship(
        back_populates="project", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )

    def to_payload(self) -> dict:
        """Legacy-shaped session dict for frontend compatibility."""
        return {
            "id": str(self.id),
            "name": self.name,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else "",
            "mode": self.mode,
            "k_verifiers": self.k_verifiers,
            "verification_mode": self.mode == "verification",
            "verifiers_per_item": self.k_verifiers,
            "owner_id": self.owner_id,
        }


# ── ProjectMember (RBAC) ──────────────────────────────────────

class ProjectMember(SQLModel, table=True):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )

    id: int = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="projects.id", nullable=False)
    user_id: int = Field(foreign_key="users.id", nullable=False)
    role: str = Field(default="annotator")  # "owner" | "maintainer" | "annotator"

    project: "Project" = Relationship(back_populates="members")
    user: "User" = Relationship(back_populates="memberships")


# ── Invitation ────────────────────────────────────────────────

class Invitation(SQLModel, table=True):
    __tablename__ = "invitations"

    id: int = Field(default=None, primary_key=True)
    token: str = Field(index=True, unique=True, nullable=False)
    project_id: int = Field(foreign_key="projects.id", nullable=False)
    role: str = Field(default="annotator")  # "maintainer" | "annotator"
    created_by: int = Field(foreign_key="users.id", nullable=False)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: Optional[datetime] = Field(default=None)
    used: bool = Field(default=False)

    project: "Project" = Relationship(back_populates="invitations")


class AccountInvitation(SQLModel, table=True):
    """One-use invitation that creates an account without a project."""

    __tablename__ = "account_invitations"

    id: int = Field(default=None, primary_key=True)
    token: str = Field(index=True, unique=True, nullable=False)
    created_by: int = Field(foreign_key="users.id", nullable=False)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: Optional[datetime] = Field(default=None)
    used: bool = Field(default=False)


class SystemSetting(SQLModel, table=True):
    """Small persistent store for settings editable by administrators."""

    __tablename__ = "system_settings"

    key: str = Field(primary_key=True, max_length=100)
    value: str = Field(nullable=False)
    updated_at: datetime = Field(default_factory=utcnow)


# ── TaxonomyNode ──────────────────────────────────────────────

class TaxonomyNode(SQLModel, table=True):
    __tablename__ = "taxonomy_nodes"

    id: int = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="projects.id", nullable=False)
    name: str
    level: int = Field(default=1)
    full_path: str
    parent_id: Optional[int] = Field(
        default=None, foreign_key="taxonomy_nodes.id", nullable=True
    )

    project: "Project" = Relationship(back_populates="taxonomy_nodes")
    children: list["TaxonomyNode"] = Relationship(
        back_populates="parent",
        sa_relationship_kwargs={
            "foreign_keys": "TaxonomyNode.parent_id",
            "cascade": "all, delete-orphan",
        },
    )
    parent: Optional["TaxonomyNode"] = Relationship(
        back_populates="children",
        sa_relationship_kwargs={"foreign_keys": "TaxonomyNode.parent_id", "remote_side": "TaxonomyNode.id"},
    )


# ── Annotation (immutable log) ────────────────────────────────

class Annotation(SQLModel, table=True):
    __tablename__ = "annotations"

    id: int = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="projects.id", nullable=False)
    item_id: str = Field(foreign_key="items.id", nullable=False)
    annotator_id: int = Field(foreign_key="users.id", nullable=False)
    labels: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    # Track versions: each save creates a new row (append-only log)
    version: int = Field(default=1)

    project: "Project" = Relationship(back_populates="annotations")
    annotator: "User" = Relationship(back_populates="annotations")


# ── FinalDecision (conflict resolution) ───────────────────────

class FinalDecision(SQLModel, table=True):
    __tablename__ = "final_decisions"

    id: int = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="projects.id", nullable=False)
    item_id: str = Field(foreign_key="items.id", nullable=False)
    resolved_labels: list = Field(default_factory=list, sa_column=Column(JSON))
    resolved_by: int = Field(foreign_key="users.id", nullable=False)
    created_at: datetime = Field(default_factory=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "item_id", name="uq_final_decision"),
    )

    project: "Project" = Relationship(back_populates="final_decisions")
    resolved_by_user: "User" = Relationship(back_populates="decisions")


# ── Items (uploaded files / table rows) ───────────────────────

class Item(SQLModel, table=True):
    __tablename__ = "items"

    id: str = Field(primary_key=True)  # uuid4 string
    name: str
    type: str  # "image" | "pdf" | "document" | "text" | "table"
    ext: Optional[str] = None
    filename: Optional[str] = None
    content: Optional[str] = None
    size: Optional[int] = None

    content_hash: Optional[str] = Field(default=None, index=True)
    source_file: Optional[str] = None
    source_hash: Optional[str] = Field(default=None, index=True)

    data: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    refs: list["ProjectItemRef"] = Relationship(
        back_populates="item", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class ProjectItemRef(SQLModel, table=True):
    """Tracks which projects reference a given item."""
    __tablename__ = "project_item_refs"

    id: int = Field(default=None, primary_key=True)
    item_id: str = Field(foreign_key="items.id", nullable=False)
    project_id: int = Field(foreign_key="projects.id", nullable=False)

    item: "Item" = Relationship(back_populates="refs")
    project: "Project" = Relationship(back_populates="item_refs")


class BatchAssignment(SQLModel, table=True):
    """Persistent batch assignments: one row per (project, annotator_username, item_id)."""
    __tablename__ = "batch_assignments"
    __table_args__ = (
        UniqueConstraint("project_id", "annotator_username", "item_id", name="uq_batch_assignment"),
    )

    id: int = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="projects.id", nullable=False, index=True)
    annotator_username: str = Field(max_length=40, nullable=False)
    item_id: str = Field(max_length=128, nullable=False)


# Keep the old table name for backward compat alias
ItemRef = ProjectItemRef


class TableUpload(SQLModel, table=True):
    """Caches the row items produced for a previously-uploaded table file."""
    __tablename__ = "table_uploads"

    source_hash: str = Field(primary_key=True)
    row_ids: list = Field(sa_column=Column(JSON))
    columns: list = Field(sa_column=Column(JSON))
