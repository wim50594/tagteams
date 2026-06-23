"""
Project management, annotation, progress tracking, conflict resolution, CSV export.

This replaces the old session_routes.py with a Project-based architecture
using immutable annotations (append-only log) and separate FinalDecision table.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import shutil
import zipfile
from typing import Annotated, Any

import aiofiles
from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import asc, desc, select

from app._sqlmodel_compat import col_in
from app.auth import User, get_current_user, hash_password, require_admin
from app.config import get_settings
from app.database import (
    cache_delete,
    cache_get,
    cache_key_item,
    cache_key_session,
    cache_set,
    get_session,
)
from app.models import (
    Annotation,
    BatchAssignment,
    FinalDecision,
    Item,
    Project,
    ProjectItemRef,
    ProjectMember,
    TableUpload,
    User as UserModel,
)
from app.services.conflict import ConflictService
from app.services.iam import IAMService
from app.services.taxonomy import TaxonomyService
from app.services.workload import WorkloadService

router = APIRouter(prefix="/api", tags=["projects"])


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _project_item_id(project_id: int, content_hash: str) -> str:
    """Project-scoped item ID: hash of project_id + content hash."""
    return _sha256(f"{project_id}||{content_hash}".encode())


async def _read_batches(db: AsyncSession, project_id: int) -> dict[str, list[str]]:
    """Read batch assignments from the BatchAssignment table."""
    result = await db.exec(
        select(BatchAssignment).where(BatchAssignment.project_id == project_id)
    )
    batches: dict[str, list[str]] = {}
    for row in result.all():
        batches.setdefault(row.annotator_username, []).append(row.item_id)
    return batches


async def _write_batches(
    db: AsyncSession,
    project_id: int,
    batches: dict[str, list[str]],
) -> None:
    """Replace all batch assignments for a project."""
    # Delete old assignments
    await db.exec(
        select(BatchAssignment).where(BatchAssignment.project_id == project_id)
    )
    old = (await db.exec(
        select(BatchAssignment).where(BatchAssignment.project_id == project_id)
    )).all()
    for row in old:
        await db.delete(row)
    await db.flush()
    # Insert new assignments
    for username, items in batches.items():
        for item_id in items:
            db.add(BatchAssignment(
                project_id=project_id,
                annotator_username=username,
                item_id=item_id,
            ))
    await db.flush()

def cache_key_project(project_id: int) -> str:
    return f"cache:project:{project_id}"


def cache_key_project_progress(project_id: int) -> str:
    return f"cache:progress:{project_id}"


# ── Helpers ───────────────────────────────────────────────────

async def _item_meta(db: AsyncSession, item_id: str) -> tuple[str, str]:
    """Return (name, type) for an item, with caching."""
    cached = await cache_get(cache_key_item(item_id))
    if cached:
        return cached.get("name", "Unknown"), cached.get("type", "unknown")
    item = await db.get(Item, item_id)
    if not item:
        return "Unknown", "unknown"
    await cache_set(cache_key_item(item_id), {"name": item.name, "type": item.type})
    return item.name, item.type


async def _resolve_project_id(db: AsyncSession, raw_id: str) -> int | None:
    """Resolve a project ID from a string that may be a legacy UUID.

    Tries integer parse first, then falls back to looking up the
    old session ID in a migration mapping (if available).
    """
    # Try integer first (new-style project IDs)
    try:
        pid = int(raw_id)
        project = await db.get(Project, pid)
        if project:
            return pid
    except (ValueError, TypeError):
        pass

    # Legacy UUID – not supported for direct access after migration.
    # The backward-compat /api/sessions/{uuid} routes handle this case.
    return None


async def _attach_project_to_items(
    db: AsyncSession, project_id: int, item_ids: list[str]
) -> None:
    """Attach items to a project with project-scoped item IDs.

    Each draft item (content-hash ID) is copied into a project-scoped item
    (hash of project_id + content_hash). Files are copied, not shared.
    Project-scoped items that are removed get their files deleted.
    """
    media_path = get_settings().media_path

    # Get existing project-scoped refs
    result = await db.exec(
        select(ProjectItemRef).where(ProjectItemRef.project_id == project_id)
    )
    existing_refs = {r.item_id for r in result.all()}

    # Compute desired project-scoped IDs
    desired_scoped_ids: set[str] = set()
    scoped_map: dict[str, str] = {}  # draft_id -> scoped_id
    for draft_id in item_ids:
        # If already a project-scoped ID (e.g., from editing), keep as-is
        existing_check = await db.exec(
            select(ProjectItemRef).where(
                ProjectItemRef.project_id == project_id,
                ProjectItemRef.item_id == draft_id,
            )
        )
        if existing_check.first():
            desired_scoped_ids.add(draft_id)
            continue
        scoped_id = _project_item_id(project_id, draft_id)
        desired_scoped_ids.add(scoped_id)
        scoped_map[draft_id] = scoped_id

    # Remove project-scoped items no longer needed
    removed_ids = existing_refs - desired_scoped_ids
    for scoped_id in removed_ids:
        item = await db.get(Item, scoped_id)
        if item:
            if item.filename:
                file_path = media_path / item.filename
                if file_path.exists():
                    file_path.unlink()
            if item.source_hash:
                upload = await db.get(TableUpload, item.source_hash)
                if upload:
                    await db.delete(upload)
            # Deleting the item cascades to ProjectItemRef
            await db.delete(item)

    # Add new project-scoped items
    new_scoped = desired_scoped_ids - existing_refs
    for draft_id, scoped_id in scoped_map.items():
        if scoped_id not in new_scoped:
            continue
        # Already exists? (from a previous edit)
        if await db.get(Item, scoped_id):
            db.add(ProjectItemRef(item_id=scoped_id, project_id=project_id))
            continue

        draft_item = await db.get(Item, draft_id)
        if not draft_item:
            continue

        # Copy file if this is a binary item
        new_filename = None
        if draft_item.filename and draft_item.ext:
            new_filename = f"{scoped_id}{draft_item.ext}"
            src = media_path / draft_item.filename
            dst = media_path / new_filename
            if src.exists():
                shutil.copy2(src, dst)

        scoped_item = Item(
            id=scoped_id,
            name=draft_item.name,
            type=draft_item.type,
            ext=draft_item.ext,
            filename=new_filename,
            content=draft_item.content,
            content_hash=scoped_id,
            size=draft_item.size,
            data=draft_item.data,
            source_file=draft_item.source_file,
            source_hash=draft_item.source_hash,
        )
        db.add(scoped_item)

        db.add(ProjectItemRef(item_id=scoped_id, project_id=project_id))

        # Delete draft item and its file — no longer needed
        if draft_item.filename:
            draft_path = media_path / draft_item.filename
            if draft_path.exists():
                draft_path.unlink()
        await db.delete(draft_item)

    await db.flush()


# ── Project CRUD ──────────────────────────────────────────────

@router.post("/projects", status_code=201)
async def create_project(
    payload: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Create a new project. The creator becomes the owner."""
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")

    mode = payload.get("verification_mode", False)
    mode_str = "verification" if mode else "split"
    k_verifiers = payload.get("verifiers_per_item", 1) if mode else 1

    project = Project(
        name=name,
        mode=mode_str,
        k_verifiers=k_verifiers,
        owner_id=current_user.id,
    )
    db.add(project)
    await db.flush()

    # Owner membership (creator is always owner)
    db.add(ProjectMember(project_id=project.id, user_id=current_user.id, role="owner"))
    added_user_ids = {current_user.id}
    member_usernames = [current_user.username]

    # Add members with roles
    annotators_raw = payload.get("annotators", [])
    if annotators_raw:
        # Accept two formats: list of strings or list of {username, role} dicts
        desired: dict[str, str] = {}
        if annotators_raw and isinstance(annotators_raw[0], dict):
            for entry in annotators_raw:
                desired[entry["username"].lower()] = entry.get("role", "annotator")
        else:
            for u in annotators_raw:
                desired[u.lower()] = "annotator"

        users_result = await db.exec(
            select(UserModel).where(col_in(UserModel.username, list(desired.keys())))
        )
        for user in users_result.all():
            if user.id in added_user_ids:
                continue
            role = desired.get(user.username, "annotator")
            db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
            added_user_ids.add(user.id)
            member_usernames.append(user.username)

    # Attach items (transforms draft IDs to project-scoped IDs)
    item_ids = list(payload.get("item_ids") or [])
    if item_ids:
        await _attach_project_to_items(db, project.id, item_ids)

    # Get project-scoped item IDs after attachment
    refs_result = await db.exec(
        select(ProjectItemRef).where(ProjectItemRef.project_id == project.id)
    )
    scoped_item_ids = [r.item_id for r in refs_result.all()]

    # Import taxonomy
    taxonomy = payload.get("taxonomy", [])
    if taxonomy:
        await TaxonomyService.import_taxonomy(db, project.id, taxonomy)

    # Compute and store initial batch assignments
    batches = WorkloadService.compute_batches(scoped_item_ids, member_usernames, mode_str, k_verifiers)
    await _write_batches(db, project.id, batches)

    await db.commit()
    await db.refresh(project)

    return {
        "id": str(project.id),
        "name": project.name,
        "mode": project.mode,
        "created_at": project.created_at.isoformat() + "Z" if project.created_at else "",
    }


@router.get("/projects")
async def list_projects(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """List projects. Admins see all; others see only their projects."""
    if current_user.is_admin:
        result = await db.exec(select(Project).order_by(desc(Project.created_at)))
        projects = result.all()
    else:
        # Get project IDs where user is a member
        member_result = await db.exec(
            select(ProjectMember).where(
                ProjectMember.user_id == current_user.id
            )
        )
        project_ids = [m.project_id for m in member_result.all()]
        if not project_ids:
            return []
        result = await db.exec(
            select(Project).where(col_in(Project.id, project_ids)).order_by(desc(Project.created_at))
        )
        projects = result.all()

    output = []
    for p in projects:
        # Get member count and annotator usernames
        member_result = await db.exec(
            select(ProjectMember).where(ProjectMember.project_id == p.id)
        )
        members = member_result.all()
        user_map: dict[int, UserModel] = {}
        if members:
            user_ids = [m.user_id for m in members]
            users_result = await db.exec(select(UserModel).where(col_in(UserModel.id, user_ids)))
            for u in users_result.all():
                user_map[u.id] = u

        annotators = [user_map[m.user_id].username for m in members if m.user_id in user_map]

        # Determine current user's role in this project
        member_match = next((m for m in members if m.user_id == current_user.id), None)
        current_user_role = member_match.role if member_match else None

        # Get item count
        refs_result = await db.exec(
            select(ProjectItemRef).where(ProjectItemRef.project_id == p.id)
        )
        item_ids = [r.item_id for r in refs_result.all()]

        # Read batches from table
        batches = await _read_batches(db, p.id)
        if not batches:
            batches = p.batches or {}
        my_batch = batches.get(current_user.username, [])
        my_progress = None
        if my_batch:
            annotations_result = await db.exec(
                select(Annotation)
                .where(
                    Annotation.project_id == p.id,
                    Annotation.annotator_id == current_user.id,
                )
                .order_by(asc(Annotation.item_id), desc(Annotation.version))
            )
            labeled_count = 0
            seen: set[str] = set()
            for ann in annotations_result.all():
                if ann.item_id not in seen:
                    seen.add(ann.item_id)
                    if ann.labels:
                        labeled_count += 1
            total = len(my_batch)
            my_progress = {
                "labeled": labeled_count,
                "total": total,
                "pct": math.floor(labeled_count / total * 100) if total else 0,
            }

        output.append({
            "id": str(p.id),
            "name": p.name,
            "created_at": p.created_at.isoformat() + "Z" if p.created_at else "",
            "owner_id": p.owner_id,
            "annotators": annotators,
            "item_ids": item_ids,
            "batches": batches,
            "verification_mode": p.mode == "verification",
            "verifiers_per_item": p.k_verifiers,
            "taxonomy": await TaxonomyService.get_taxonomy_flat(db, p.id),
            "item_stats": {},
            "current_user_role": current_user_role,
            "my_progress": my_progress,
        })

    return output


@router.get("/projects/{project_id}")
async def get_project(
    project_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Get project details."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    can_access = await IAMService.can_annotate_project(db, project_id, current_user)
    if not can_access:
        raise HTTPException(status_code=403, detail="Not a member of this project")

    # Get members
    member_result = await db.exec(
        select(ProjectMember).where(ProjectMember.project_id == project.id)
    )
    members = member_result.all()
    user_map: dict[int, UserModel] = {}
    if members:
        user_ids = [m.user_id for m in members]
        users_result = await db.exec(select(UserModel).where(col_in(UserModel.id, user_ids)))
        for u in users_result.all():
            user_map[u.id] = u

    annotators = [user_map[m.user_id].username for m in members if m.user_id in user_map]
    members_with_roles = [{"username": user_map[m.user_id].username, "role": m.role}
                         for m in members if m.user_id in user_map]

    # Determine current user's role in this project
    member_match = next((m for m in members if m.user_id == current_user.id), None)
    current_user_role = member_match.role if member_match else None

    # Get item IDs
    refs_result = await db.exec(
        select(ProjectItemRef).where(ProjectItemRef.project_id == project.id)
    )
    item_ids = [r.item_id for r in refs_result.all()]

    # Get taxonomy
    taxonomy = await TaxonomyService.get_taxonomy_flat(db, project.id)

    # Use stored batches
    batches = await _read_batches(db, project.id)
    if not batches:
        batches = project.batches or {}

    # Fetch item names for the response
    items_list = []
    if item_ids:
        items_result = await db.exec(select(Item).where(col_in(Item.id, item_ids)))
        for it in items_result.all():
            items_list.append({
                "item_id": it.id,
                "name": it.name,
                "type": it.type,
                "source_file": it.source_file,
            })

    payload = {
        "id": str(project.id),
        "name": project.name,
        "created_at": project.created_at.isoformat() + "Z" if project.created_at else "",
        "owner_id": project.owner_id,
        "annotators": annotators,
        "members": members_with_roles,
        "item_ids": item_ids,
        "items": items_list,
        "batches": batches,
        "verification_mode": project.mode == "verification",
        "verifiers_per_item": project.k_verifiers,
        "taxonomy": taxonomy,
        "display_columns": [],  # stored elsewhere if needed
        "item_stats": {},
        "current_user_role": current_user_role,
    }
    return payload


@router.put("/projects/{project_id}")
async def update_project(
    project_id: int,
    payload: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Update project details. Only admins, owner, and maintainers can edit."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    can_manage = await IAMService.can_manage_project(db, project_id, current_user)
    if not can_manage:
        raise HTTPException(
            status_code=403, detail="Only admins, owner, and maintainers can edit"
        )

    # Update basic fields
    if "name" in payload:
        project.name = payload["name"].strip() or project.name

    if "verification_mode" in payload:
        project.mode = "verification" if payload["verification_mode"] else "split"
        project.k_verifiers = payload.get("verifiers_per_item", 1) if project.mode == "verification" else 1

    # Update items
    if "item_ids" in payload:
        await _attach_project_to_items(db, project.id, payload["item_ids"])

    # Update owner (via owner_id or owner_username)
    new_owner_id: int | None = None
    if "owner_id" in payload and payload["owner_id"] != project.owner_id:
        new_owner_id = payload["owner_id"]
    elif "owner_username" in payload:
        owner_result = await db.exec(
            select(UserModel).where(UserModel.username == payload["owner_username"].lower())
        )
        owner_user = owner_result.first()
        if owner_user and owner_user.id != project.owner_id:
            new_owner_id = owner_user.id

    # Update members with roles (may also handle owner transfer)
    desired: dict[str, str] = {}
    if "annotators" in payload:
        raw = payload["annotators"]
        if raw and isinstance(raw[0], dict):
            for entry in raw:
                desired[entry["username"].lower()] = entry.get("role", "annotator")
                # Detect owner from role in annotators list
                if entry.get("role") == "owner":
                    owner_user_result = await db.exec(
                        select(UserModel).where(UserModel.username == entry["username"].lower())
                    )
                    ou = owner_user_result.first()
                    if ou and ou.id != project.owner_id:
                        new_owner_id = ou.id
        else:
            for u in raw:
                desired[u.lower()] = "annotator"

        # Get existing members
        existing_result = await db.exec(
            select(ProjectMember, UserModel)
            .join(UserModel)
            .where(ProjectMember.project_id == project.id)
        )
        existing: dict[str, ProjectMember] = {
            row[1].username.lower(): row[0] for row in existing_result.all()
        }

        # Remove members not in desired list (keep owner — they're handled separately)
        for username, member in existing.items():
            if member.role == "owner":
                continue
            if username not in desired:
                await db.delete(member)

        # Add or update members
        for username_lower, role in desired.items():
            if role == "owner":
                continue  # owner handled via new_owner_id below
            if username_lower in existing:
                member = existing[username_lower]
                if member.role == "owner":
                    continue  # existing owner, handled below
                if member.role != role:
                    member.role = role
                    db.add(member)
            else:
                user_result = await db.exec(
                    select(UserModel).where(UserModel.username == username_lower)
                )
                user = user_result.first()
                if user:
                    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))

    # Execute owner transfer
    if new_owner_id is not None:
        # Demote old owner
        old_owner_result = await db.exec(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == project.owner_id,
            )
        )
        old_owner = old_owner_result.first()
        if old_owner:
            # Check if old owner has a desired role from annotators
            old_owner_user_result = await db.exec(
                select(UserModel).where(UserModel.id == project.owner_id)
            )
            old_owner_user = old_owner_user_result.first()
            if old_owner_user:
                desired_role = desired.get(old_owner_user.username.lower(), "maintainer")
                if desired_role == "owner":
                    desired_role = "maintainer"  # can't stay owner
                old_owner.role = desired_role
            else:
                old_owner.role = "maintainer"
            db.add(old_owner)

        # Promote new owner
        new_owner_member_result = await db.exec(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == new_owner_id,
            )
        )
        new_owner_member = new_owner_member_result.first()
        if new_owner_member:
            new_owner_member.role = "owner"
            db.add(new_owner_member)

        project.owner_id = new_owner_id

    # Update taxonomy — with cascading label removal
    if "taxonomy" in payload:
        # Capture old full_paths before replacing
        old_nodes = await TaxonomyService.get_taxonomy_flat(db, project.id)
        old_paths = {n["full_path"] for n in old_nodes}

        await TaxonomyService.import_taxonomy(db, project.id, payload["taxonomy"])

        # Find removed paths and clean up annotations
        new_nodes = await TaxonomyService.get_taxonomy_flat(db, project.id)
        new_paths = {n["full_path"] for n in new_nodes}
        removed_paths = old_paths - new_paths

        if removed_paths:
            annotations_result = await db.exec(
                select(Annotation).where(Annotation.project_id == project.id)
            )
            for ann in annotations_result.all():
                old_labels = set(ann.labels)
                new_labels = [label for label in ann.labels if label not in removed_paths]
                if len(new_labels) != len(old_labels):
                    ann_copy = Annotation(
                        project_id=ann.project_id,
                        item_id=ann.item_id,
                        annotator_id=ann.annotator_id,
                        labels=new_labels,
                        version=ann.version + 1,
                    )
                    db.add(ann_copy)

            # Also clean up final decisions
            decisions_result = await db.exec(
                select(FinalDecision).where(FinalDecision.project_id == project.id)
            )
            for fd in decisions_result.all():
                old_labels = set(fd.resolved_labels)
                new_labels = [label for label in fd.resolved_labels if label not in removed_paths]
                if len(new_labels) != len(old_labels):
                    fd.resolved_labels = new_labels
                    db.add(fd)

    # Recompute batches if members, items, or mode changed
    members_changed = "annotators" in payload
    items_changed = "item_ids" in payload
    mode_changed = "verification_mode" in payload
    if members_changed or items_changed or mode_changed:
        # Get current members
        member_result = await db.exec(
            select(ProjectMember, UserModel)
            .join(UserModel)
            .where(ProjectMember.project_id == project.id)
        )
        current_members: dict[str, str] = {}
        for row in member_result.all():
            current_members[row[1].username] = row[0].role
        member_usernames = list(current_members.keys())

        # Get current items
        refs_result = await db.exec(
            select(ProjectItemRef).where(ProjectItemRef.project_id == project.id)
        )
        current_item_ids = [r.item_id for r in refs_result.all()]

        # Full recompute: stable deterministic distribution for all members
        batches = WorkloadService.compute_batches(
            current_item_ids, member_usernames, project.mode, project.k_verifiers
        )
        await _write_batches(db, project.id, batches)

    db.add(project)
    await db.commit()
    await db.refresh(project)

    await cache_delete(
        cache_key_project(project_id),
        cache_key_project_progress(project_id),
        cache_key_session(str(project_id)),
    )
    return {"status": "ok", "id": str(project.id)}


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Delete a project. Only admins, owner, and maintainers."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    can_manage = await IAMService.can_manage_project(db, project_id, current_user)
    if not can_manage:
        raise HTTPException(
            status_code=403, detail="Only admins, owner, and maintainers can delete"
        )

    # Collect item IDs before detaching
    refs_result = await db.exec(
        select(ProjectItemRef).where(ProjectItemRef.project_id == project_id)
    )
    refs = refs_result.all()
    item_ids = [r.item_id for r in refs]

    # Delete items and their files (files are not shared between projects)
    deleted_files = 0
    deleted_items = 0
    for item_id in item_ids:
        item = await db.get(Item, item_id)
        if not item:
            continue
        # Delete file from disk
        if item.filename:
            media_path = get_settings().media_path / item.filename
            if media_path.exists():
                media_path.unlink()
                deleted_files += 1
        # Clean up TableUpload if this was a table source
        if item.source_hash:
            upload = await db.get(TableUpload, item.source_hash)
            if upload:
                await db.delete(upload)
        # Deleting the item cascades to ProjectItemRef
        await db.delete(item)
        deleted_items += 1

    # Delete batch assignments
    assignments = (await db.exec(
        select(BatchAssignment).where(BatchAssignment.project_id == project_id)
    )).all()
    for row in assignments:
        await db.delete(row)
    await db.flush()

    # Delete project (cascades to members, annotations, taxonomy, final_decisions)
    await db.delete(project)
    await db.commit()

    await cache_delete(
        cache_key_project(project_id),
        cache_key_project_progress(project_id),
        cache_key_session(str(project_id)),
    )
    return {"status": "deleted", "deleted_items": deleted_items, "deleted_files": deleted_files}


# ── Items ─────────────────────────────────────────────────────

@router.get("/items/{item_id}")
async def get_item(
    item_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    item = await db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item.model_dump()


# ── Labels (annotations) ──────────────────────────────────────

@router.post("/labels/{project_id}/{annotator_username}")
async def save_labels(
    project_id: str,
    annotator_username: str,
    payload: dict[str, list[str]],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Save labels for one item (append-only immutable log)."""
    pid = await _resolve_project_id(db, project_id)
    if pid is None:
        raise HTTPException(status_code=404, detail="Project not found")

    can_access = await IAMService.can_annotate_project(db, pid, current_user)
    if not can_access:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Determine the annotator user
    if current_user.is_admin:
        # Admin can save as any annotator
        annotator_result = await db.exec(
            select(UserModel).where(UserModel.username == annotator_username.lower())
        )
        annotator_user = annotator_result.first()
    else:
        # Regular user can only save as themselves
        if current_user.username != annotator_username.lower():
            raise HTTPException(status_code=403, detail="Cannot save labels for another user")
        annotator_user = current_user

    if not annotator_user:
        raise HTTPException(status_code=404, detail="Annotator not found")

    # Save each item's labels as an immutable annotation
    for item_id, labels in payload.items():
        # Expand labels with ancestor paths
        taxonomy_nodes = await TaxonomyService.get_taxonomy_nodes(db, pid)
        if taxonomy_nodes:
            labels = TaxonomyService.expand_labels(labels, taxonomy_nodes)

        await ConflictService.save_annotations(
            db=db,
            project_id=pid,
            annotator_id=annotator_user.id,
            item_id=item_id,
            labels=labels,
        )

    await cache_delete(cache_key_project_progress(pid))
    return {"status": "ok"}


@router.get("/labels/{project_id}/{annotator_username}")
async def get_labels(
    project_id: str,
    annotator_username: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Get the latest labels for an annotator. Returns {item_id: [labels]}."""
    pid = await _resolve_project_id(db, project_id)
    if pid is None:
        raise HTTPException(status_code=404, detail="Project not found")

    can_access = await IAMService.can_annotate_project(db, pid, current_user)
    if not can_access:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Admin can view any annotator; regular users only themselves
    if not current_user.is_admin:
        can_manage = await IAMService.can_manage_project(db, pid, current_user)
        if not can_manage and current_user.username != annotator_username.lower():
            raise HTTPException(status_code=403, detail="Cannot read labels for another user")

    annotator_result = await db.exec(
        select(UserModel).where(UserModel.username == annotator_username.lower())
    )
    annotator_user = annotator_result.first()
    if not annotator_user:
        return {}

    labels = await ConflictService.get_latest_annotations(
        db=db, project_id=pid, annotator_id=annotator_user.id
    )
    return labels


# ── Progress ──────────────────────────────────────────────────

@router.get("/projects/{project_id}/progress")
async def get_progress(
    project_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Get annotation progress for all annotators in a project."""
    cached = await cache_get(cache_key_project_progress(project_id))
    if cached:
        return cached

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get members
    member_result = await db.exec(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    )
    members = member_result.all()

    user_map: dict[int, str] = {}
    display_names: dict[int, str] = {}
    if members:
        user_ids = [m.user_id for m in members]
        users_result = await db.exec(select(UserModel).where(col_in(UserModel.id, user_ids)))
        for u in users_result.all():
            user_map[u.id] = u.username
            display_names[u.id] = u.display_name

    # Use stored batches for progress calculation
    batches = await _read_batches(db, project_id)

    # Get latest non-empty labels per (annotator_id, item_id)
    # Order by version DESC to get latest first
    annotations_result = await db.exec(
        select(Annotation)
        .where(Annotation.project_id == project_id)
        .order_by(asc(Annotation.annotator_id), asc(Annotation.item_id), desc(Annotation.version))
    )
    all_annotations = annotations_result.all()

    labeled_items: set[tuple[int, str]] = set()
    for ann in all_annotations:
        key = (ann.annotator_id, ann.item_id)
        if key not in labeled_items and ann.labels:
            labeled_items.add(key)

    progress = {}
    for member in members:
        if member.user_id not in user_map:
            continue
        username = user_map[member.user_id]
        display_name = display_names.get(member.user_id, username)
        annotator_batch = batches.get(username, [])
        if not annotator_batch:
            progress[username] = {"labeled": 0, "total": 0, "pct": 0, "display_name": display_name}
            continue
        labeled = sum(
            1 for iid in annotator_batch
            if (member.user_id, iid) in labeled_items
        )
        total = len(annotator_batch)
        pct = math.floor(labeled / total * 100) if total > 0 else 0
        progress[username] = {"labeled": labeled, "total": total, "pct": pct, "display_name": display_name}

    await cache_set(cache_key_project_progress(project_id), progress, ttl=30)
    return progress


# ── Conflicts & resolution ────────────────────────────────────

@router.get("/projects/{project_id}/conflicts")
async def get_conflicts(
    project_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Get conflict list for a project. Requires manage permission."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    can_manage = await IAMService.can_manage_project(db, project_id, current_user)
    if not can_manage:
        raise HTTPException(status_code=403, detail="Only admins, owner, and maintainers can view conflicts")

    conflicts = await ConflictService.get_conflicts(db, project)

    # Enrich with item names/types
    for c in conflicts:
        name, typ = await _item_meta(db, c["item_id"])
        c["name"] = name
        c["type"] = typ

    return {"conflicts": conflicts}


@router.post("/projects/{project_id}/resolve")
async def resolve_conflict(
    project_id: int,
    payload: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Resolve a conflict by setting final labels. Only admins, owner, maintainers."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    can_manage = await IAMService.can_manage_project(db, project_id, current_user)
    if not can_manage:
        raise HTTPException(
            status_code=403, detail="Only admins, owner, and maintainers can resolve conflicts"
        )

    item_id = payload.get("item_id")
    final_labels = payload.get("final_labels", [])

    # Store resolved labels (immutable – does not change annotations)
    await ConflictService.resolve_conflict(
        db=db,
        project_id=project_id,
        item_id=item_id,
        resolved_labels=final_labels,
        resolved_by=current_user.id,
    )

    return {"status": "ok"}


# ── Export ────────────────────────────────────────────────────

@router.get("/projects/{project_id}/export")
async def export_project(
    project_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    mode: str = Query("raw"),
):
    """Export annotations. Requires manage permission for merged mode."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    can_access = await IAMService.can_annotate_project(db, project_id, current_user)
    if not can_access:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get members
    member_result = await db.exec(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    )
    members = member_result.all()
    user_map: dict[int, UserModel] = {}
    if members:
        user_ids = [m.user_id for m in members]
        users_result = await db.exec(select(UserModel).where(col_in(UserModel.id, user_ids)))
        for u in users_result.all():
            user_map[u.id] = u

    # Get item IDs
    refs_result = await db.exec(
        select(ProjectItemRef).where(ProjectItemRef.project_id == project_id)
    )
    item_ids = [r.item_id for r in refs_result.all()]

    # Get all latest annotations per annotator
    annotator_labels: dict[str, dict[str, list[str]]] = {}
    for member in members:
        if member.user_id not in user_map:
            continue
        username = user_map[member.user_id].username
        labels = await ConflictService.get_latest_annotations(
            db, project_id, member.user_id
        )
        annotator_labels[username] = labels

    # Get final decisions
    final_decisions = await ConflictService.get_all_final_decisions(db, project_id)

    rows: list[dict] = []

    if mode == "merged":
        fieldnames = ["item_id", "item_name", "item_type", "final_labels", "agreed_annotators"]
        for item_id in item_ids:
            name, typ = await _item_meta(db, item_id)
            votes = {
                username: annotator_labels[username].get(item_id, [])
                for username in annotator_labels
                if item_id in annotator_labels.get(username, {})
            }
            if item_id in final_decisions:
                resolved = final_decisions[item_id]
                final_set = set(resolved)
                agreed = [
                    ann for ann, lbls in votes.items()
                    if set(lbls) == final_set
                ]
            else:
                first = None
                match = True
                for lbls in votes.values():
                    if first is None:
                        first = sorted(lbls)
                    elif first != sorted(lbls):
                        match = False
                        break
                resolved = list(first) if (match and first) else []
                agreed = list(votes.keys()) if match else []
            rows.append({
                "item_id": item_id,
                "item_name": name,
                "item_type": typ,
                "final_labels": " | ".join(TaxonomyService.collapse_hierarchy(resolved)),
                "agreed_annotators": ", ".join(agreed) if agreed else "None",
            })
    else:  # raw
        fieldnames = ["item_id", "item_name", "item_type", "annotator", "labels"]
        for username in annotator_labels:
            for item_id in annotator_labels[username]:
                labels = annotator_labels[username].get(item_id, [])
                name, typ = await _item_meta(db, item_id)
                rows.append({
                    "item_id": item_id,
                    "item_name": name,
                    "item_type": typ,
                    "annotator": username,
                    "labels": " | ".join(TaxonomyService.collapse_hierarchy(labels)),
                })

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=tagteams_{str(project_id)[:8]}_{mode}.csv"
        },
    )


# ── Full project export / import (admin only) ──────────────────

@router.get("/projects/{project_id}/export-full")
async def export_project_full(
    project_id: int,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Export a complete project as a ZIP archive including files, users, and annotations."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── Project metadata ──
        taxonomy = await TaxonomyService.get_taxonomy_flat(db, project_id)
        batches = await _read_batches(db, project_id)
        project_data = {
            "name": project.name,
            "mode": project.mode,
            "k_verifiers": project.k_verifiers,
            "created_at": project.created_at.isoformat() if project.created_at else "",
            "taxonomy": taxonomy,
            "batches": batches,
        }
        zf.writestr("project.json", json.dumps(project_data, indent=2))

        # ── Users (project members) ──
        member_result = await db.exec(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
        members = member_result.all()
        user_ids = [m.user_id for m in members]
        user_roles: dict[int, str] = {m.user_id: m.role for m in members}
        users_data = []
        if user_ids:
            users_result = await db.exec(select(UserModel).where(col_in(UserModel.id, user_ids)))
            for u in users_result.all():
                users_data.append({
                    "username": u.username,
                    "display_name": u.display_name,
                    "language": u.language,
                    "role": user_roles.get(u.id, "annotator"),
                })
        zf.writestr("users.json", json.dumps(users_data, indent=2))

        # ── Items metadata ──
        refs_result = await db.exec(
            select(ProjectItemRef).where(ProjectItemRef.project_id == project_id)
        )
        item_ids = [r.item_id for r in refs_result.all()]
        items_data: dict[str, dict] = {}
        if item_ids:
            items_result = await db.exec(select(Item).where(col_in(Item.id, item_ids)))
            for it in items_result.all():
                items_data[it.id] = {
                    "name": it.name,
                    "type": it.type,
                    "ext": it.ext,
                    "filename": it.filename,
                    "content": it.content,
                    "size": it.size,
                    "data": it.data,
                    "content_hash": it.content_hash,
                    "source_file": it.source_file,
                    "source_hash": it.source_hash,
                }
        zf.writestr("items.json", json.dumps(items_data, indent=2))

        # ── Files ──
        media_path = get_settings().media_path
        for item_id, meta in items_data.items():
            if meta.get("filename"):
                file_path = media_path / meta["filename"]
                if file_path.exists():
                    zf.write(file_path, f"files/{meta['filename']}")

        # ── Annotations ──
        annotations_result = await db.exec(
            select(Annotation)
            .where(Annotation.project_id == project_id)
            .order_by(asc(Annotation.item_id), asc(Annotation.annotator_id), asc(Annotation.version))
        )
        annotations_data = []
        user_map: dict[int, str] = {}
        for ann in annotations_result.all():
            if ann.annotator_id not in user_map:
                u = await db.get(UserModel, ann.annotator_id)
                user_map[ann.annotator_id] = u.username if u else f"user_{ann.annotator_id}"
            annotations_data.append({
                "item_id": ann.item_id,
                "annotator_username": user_map[ann.annotator_id],
                "labels": ann.labels,
                "version": ann.version,
                "created_at": ann.created_at.isoformat() if ann.created_at else "",
            })
        zf.writestr("annotations.json", json.dumps(annotations_data, indent=2))

        # ── Final decisions ──
        decisions_result = await db.exec(
            select(FinalDecision).where(FinalDecision.project_id == project_id)
        )
        decisions_data = []
        for fd in decisions_result.all():
            u = await db.get(UserModel, fd.resolved_by)
            decisions_data.append({
                "item_id": fd.item_id,
                "resolved_labels": fd.resolved_labels,
                "resolved_by_username": u.username if u else "unknown",
            })
        zf.writestr("final_decisions.json", json.dumps(decisions_data, indent=2))

    buf.seek(0)
    safe_name = project.name.replace(" ", "_")[:40]
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=tagteams_{safe_name}_{project_id}.zip"
        },
    )


@router.post("/projects/import", status_code=201)
async def import_project_full(
    file: UploadFile,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
    name: str = Form(None),
):
    """Import a complete project from a ZIP archive. Optional *name* overrides the original."""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted")

    content = await file.read()
    zf = zipfile.ZipFile(io.BytesIO(content))

    try:
        # ── Read metadata ──
        project_data = json.loads(zf.read("project.json"))
        users_data = json.loads(zf.read("users.json"))
        items_data: dict = json.loads(zf.read("items.json"))
        annotations_data: list = json.loads(zf.read("annotations.json"))
        decisions_data: list = json.loads(zf.read("final_decisions.json"))
    except (KeyError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid archive: missing or corrupt file: {e}")

    # ── Create users (if they don't exist) ──
    username_map: dict[str, int] = {}  # username -> user_id
    for u_data in users_data:
        username = u_data["username"].lower()
        result = await db.exec(select(UserModel).where(UserModel.username == username))
        existing = result.first()
        if existing:
            username_map[username] = existing.id
        else:
            import secrets
            new_user = UserModel(
                username=username,
                display_name=u_data.get("display_name", username),
                language=u_data.get("language", "en"),
                hashed_password=hash_password(secrets.token_urlsafe(32)),
            )
            db.add(new_user)
            await db.flush()
            username_map[username] = new_user.id

    # ── Create project ──
    project = Project(
        name=(name or project_data["name"]).strip(),
        mode=project_data.get("mode", "split"),
        k_verifiers=project_data.get("k_verifiers", 1),
        owner_id=username_map.get(users_data[0]["username"], 1),
    )
    db.add(project)
    await db.flush()

    # ── Create members ──
    for u_data in users_data:
        uid = username_map[u_data["username"].lower()]
        role = u_data.get("role", "annotator")
        db.add(ProjectMember(project_id=project.id, user_id=uid, role=role))

    # ── Import files and create items ──
    media_path = get_settings().media_path
    item_id_map: dict[str, str] = {}  # old_id -> new_id (same since content-hash)
    for old_id, meta in items_data.items():
        # Always write file from ZIP (overwrite if exists on disk)
        filename = meta.get("filename")
        if filename:
            try:
                file_data = zf.read(f"files/{filename}")
                dst = media_path / filename
                dst.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(dst, "wb") as out:
                    await out.write(file_data)
            except KeyError:
                pass  # file not in archive, skip

        # Items are content-addressed — may already exist (same-instance import).
        # If they do, just link them via ProjectItemRef; otherwise create them.
        existing = await db.get(Item, old_id)
        if not existing:
            item = Item(
                id=old_id,
                name=meta.get("name", ""),
                type=meta.get("type", "text"),
                ext=meta.get("ext"),
                filename=filename,
                content=meta.get("content"),
                size=meta.get("size"),
                data=meta.get("data"),
                content_hash=meta.get("content_hash"),
                source_file=meta.get("source_file"),
                source_hash=meta.get("source_hash"),
            )
            db.add(item)

        # Link item to project (even if it already existed)
        db.add(ProjectItemRef(item_id=old_id, project_id=project.id))
        item_id_map[old_id] = old_id

    # ── Import taxonomy ──
    taxonomy = project_data.get("taxonomy", [])
    if taxonomy:
        await TaxonomyService.import_taxonomy(db, project.id, taxonomy)

    # ── Import batches ──
    batches = project_data.get("batches", {})
    if batches:
        await _write_batches(db, project.id, batches)

    # ── Import annotations ──
    for ann_data in annotations_data:
        username = ann_data["annotator_username"].lower()
        annotator_id = username_map.get(username)
        if not annotator_id:
            continue
        item_id = ann_data["item_id"]
        if item_id not in item_id_map:
            continue
        annotation = Annotation(
            project_id=project.id,
            item_id=item_id,
            annotator_id=annotator_id,
            labels=ann_data.get("labels", []),
            version=ann_data.get("version", 1),
        )
        db.add(annotation)

    # ── Import final decisions ──
    for fd_data in decisions_data:
        username = fd_data["resolved_by_username"].lower()
        resolver_id = username_map.get(username)
        if not resolver_id:
            continue
        item_id = fd_data["item_id"]
        if item_id not in item_id_map:
            continue
        db.add(FinalDecision(
            project_id=project.id,
            item_id=item_id,
            resolved_labels=fd_data.get("resolved_labels", []),
            resolved_by=resolver_id,
        ))

    await db.commit()

    return {"status": "ok", "id": str(project.id), "name": project.name}

@router.post("/sessions/save-full")
async def save_session_compat(
    payload: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Backward-compatible endpoint that maps old session format to new project format."""
    # Check if this is an existing project (by ID) or a new one
    raw_id = payload.get("id", "")
    # Old sessions used UUID strings; new projects use int IDs.
    # If the ID looks like an old UUID and doesn't exist as a project, create one.

    if raw_id:
        try:
            # Try as integer (new project)
            project_id = int(raw_id)
            project = await db.get(Project, project_id)
            if project:
                # Update existing project
                return await update_project(
                    project_id=project_id,
                    payload=payload,
                    current_user=current_user,
                    db=db,
                )
        except (ValueError, TypeError):
            pass

    # Create a new project
    return await create_project(
        payload=payload,
        current_user=current_user,
        db=db,
    )


@router.get("/sessions")
async def list_sessions_compat(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Backward-compatible sessions list."""
    return await list_projects(
        current_user=current_user,
        db=db,
    )


@router.get("/sessions/{session_id}")
async def get_session_compat(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Backward-compatible session getter."""
    try:
        project_id = int(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Project not found")

    return await get_project(
        project_id=project_id,
        current_user=current_user,
        db=db,
    )


@router.delete("/sessions/{session_id}")
async def delete_session_compat(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Backward-compatible session deletion."""
    try:
        project_id = int(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Project not found")

    return await delete_project(
        project_id=project_id,
        current_user=current_user,
        db=db,
    )


@router.get("/sessions/{session_id}/progress")
async def get_session_progress_compat(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Backward-compatible progress."""
    try:
        project_id = int(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Project not found")

    return await get_progress(
        project_id=project_id,
        current_user=current_user,
        db=db,
    )


@router.get("/sessions/{session_id}/conflicts")
async def get_session_conflicts_compat(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Backward-compatible conflicts."""
    try:
        project_id = int(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Project not found")

    return await get_conflicts(
        project_id=project_id,
        current_user=current_user,
        db=db,
    )


@router.post("/sessions/{session_id}/resolve")
async def resolve_session_conflict_compat(
    session_id: str,
    payload: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """Backward-compatible conflict resolution."""
    try:
        project_id = int(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Project not found")

    return await resolve_conflict(
        project_id=project_id,
        payload=payload,
        current_user=current_user,
        db=db,
    )


@router.get("/sessions/{session_id}/export")
async def export_session_compat(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    mode: str = Query("raw"),
):
    """Backward-compatible export."""
    try:
        project_id = int(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Project not found")

    return await export_project(
        project_id=project_id,
        current_user=current_user,
        db=db,
        mode=mode,
    )
