"""
File upload and label parsing routes.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Awaitable, Callable

import aiofiles
import pandas as pd
from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, UploadFile, status,
)
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import User, get_current_user
from app.config import get_settings
from app.database import cache_delete, cache_key_item, get_session
from app.models import Item, ProjectItemRef, TableUpload
from app.services.application_settings import ApplicationSettingsService

router = APIRouter(prefix="/api", tags=["upload"])


# ── File-type taxonomy ────────────────────────────────────────

class FileCategory(str, Enum):
    IMAGE = "image"
    PDF = "pdf"
    DOCUMENT = "document"  # Word/PowerPoint - download only, no in-browser preview
    TABLE = "table"
    TEXT = "text"


STATS_KEY: dict[FileCategory, str] = {
    FileCategory.IMAGE: "images",
    FileCategory.PDF: "pdfs",
    FileCategory.DOCUMENT: "documents",
    FileCategory.TABLE: "tables",
    FileCategory.TEXT: "texts",
}


def _empty_stats() -> dict[str, int]:
    return {v: 0 for v in STATS_KEY.values()}


# Extension-based classification. Anything not listed falls back to TEXT.
# Images: everything the browser can natively render.
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".bmp", ".ico", ".avif", ".apng",
}
PDF_EXTENSIONS = {".pdf"}
TABLE_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xls"}
# Office documents: stored as-is, exposed as download in the frontend.
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".ppt", ".pptx", ".odt", ".odp"}


def classify(filename: str, _content: bytes) -> tuple[str, FileCategory]:
    """Classify by filename extension only. Unknown extensions → TEXT."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return ext, FileCategory.IMAGE
    if ext in PDF_EXTENSIONS:
        return ext, FileCategory.PDF
    if ext in TABLE_EXTENSIONS:
        return ext, FileCategory.TABLE
    if ext in DOCUMENT_EXTENSIONS:
        return ext, FileCategory.DOCUMENT
    return ext or ".txt", FileCategory.TEXT


class BatchDeleteRequest(BaseModel):
    item_ids: list[str]


# ── Per-file result returned to the frontend ──────────────────

@dataclass
class UploadedFile:
    item_id: str
    name: str
    category: FileCategory
    size: int
    duplicate: bool       # True if content was already known (any project)
    row_count: int = 0    # for tables: number of rows produced

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "category": self.category.value,
            "size": self.size,
            "duplicate": self.duplicate,
            "row_count": self.row_count,
        }


# ── Hash helpers ──────────────────────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Per-category handlers ─────────────────────────────────────
# Each handler persists the file + item and returns an UploadedFile.
# Tables are special: they yield N items (one per row) and update `columns_set`.

CategoryHandler = Callable[..., Awaitable[list[UploadedFile]]]


async def _handle_binary(
    *,
    db: AsyncSession,
    content: bytes,
    base_name: str,
    ext: str,
    category: FileCategory,
    item_type: str,       # value persisted in item.type
    media_dir,
) -> list[UploadedFile]:
    item_id = _sha256(content)
    size = len(content)

    # Check for duplicate by content hash (the item ID)
    existing = await db.get(Item, item_id)
    if existing:
        return [UploadedFile(item_id, base_name, category, size, duplicate=True)]

    stored_name = f"{item_id}{ext}"
    async with aiofiles.open(media_dir / stored_name, "wb") as out:
        await out.write(content)

    item = Item(
        id=item_id,
        name=base_name,
        type=item_type,
        ext=ext,
        filename=stored_name,
        content_hash=item_id,
        size=size,
    )
    db.add(item)
    return [UploadedFile(item_id, base_name, category, size, duplicate=False)]


async def _handle_text(
    *, db: AsyncSession, content: bytes, base_name: str, **_
) -> list[UploadedFile]:
    item_id = _sha256(content)
    size = len(content)

    existing = await db.get(Item, item_id)
    if existing:
        return [UploadedFile(item_id, base_name, FileCategory.TEXT, size, duplicate=True)]

    text = content.decode("utf-8", errors="ignore")
    item = Item(
        id=item_id,
        name=base_name,
        type="text",
        content=text,
        content_hash=item_id,
        size=size,
    )
    db.add(item)
    return [UploadedFile(item_id, base_name, FileCategory.TEXT, size, duplicate=False)]


async def _handle_table(
    *,
    db: AsyncSession,
    content: bytes,
    base_name: str,
    ext: str,
    columns_set: set[str],
    **_,
) -> list[UploadedFile]:
    """Tables: dedup at *file* level. Same file → reuse all previously-created row items."""
    content_hash = _sha256(content)
    size = len(content)

    # Reuse previous rows if this exact file was uploaded before.
    cached = await db.get(TableUpload, content_hash)
    if cached:
        # Verify the cached rows still exist (they may have been consumed by a project)
        sample = await db.get(Item, cached.row_ids[0]) if cached.row_ids else None
        if sample:
            for col in cached.columns:
                columns_set.add(col)
            return [
                UploadedFile(
                    row_id, base_name, FileCategory.TABLE, size,
                    duplicate=True, row_count=len(cached.row_ids),
                )
                for row_id in cached.row_ids
            ]
        # Stale cache — rows were consumed, reprocess
        await db.delete(cached)

    try:
        if ext in {".xlsx", ".xls"}:
            df = pd.read_excel(io.BytesIO(content))
        else:
            sep = "\t" if ext == ".tsv" else None
            df = pd.read_csv(io.BytesIO(content), sep=sep, engine="python")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse {base_name}: {exc}",
        )

    df = df.where(pd.notnull(df), None)
    row_ids: list[str] = []
    local_cols: set[str] = set()
    for idx, row in df.iterrows():
        row_dict = {
            str(k): (str(v) if v is not None else "")
            for k, v in row.to_dict().items()
        }
        row_id = _sha256(json.dumps(row_dict, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        local_cols.update(row_dict.keys())
        # Skip if this exact row already exists (globally deduplicated)
        existing = await db.get(Item, row_id)
        if existing:
            row_ids.append(row_id)
            continue
        db.add(Item(
            id=row_id,
            name=f"Row#{idx} from {base_name}",
            type="table",
            data=row_dict,
            source_file=base_name,
            source_hash=content_hash,
        ))
        row_ids.append(row_id)

    columns_set.update(local_cols)
    db.add(TableUpload(source_hash=content_hash, row_ids=row_ids, columns=sorted(local_cols)))
    return [
        UploadedFile(
            row_id, base_name, FileCategory.TABLE, size,
            duplicate=False, row_count=len(row_ids),
        )
        for row_id in row_ids
    ]


# ── Universal upload ──────────────────────────────────────────

@router.post("/upload/items")
async def upload_universal(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    files: list[UploadFile] = File(...),
):
    settings = get_settings()
    media_dir = settings.media_path
    max_upload_size = (
        await ApplicationSettingsService.get_max_upload_size_mb(db) * 1024 * 1024
    )

    files_meta: list[dict[str, Any]] = []
    item_ids: list[str] = []
    columns_set: set[str] = set()
    stats = _empty_stats()
    skipped: list[dict[str, str]] = []

    for f in files:
        base_name = os.path.basename(f.filename or "")
        if not base_name or base_name.startswith("."):
            continue

        content = await f.read(max_upload_size + 1)
        if len(content) > max_upload_size:
            skipped.append({
                "name": base_name,
                "reason": f"file exceeds the {max_upload_size // (1024 * 1024)} MB limit",
            })
            continue
        if not content:
            skipped.append({"name": base_name, "reason": "empty file"})
            continue

        ext, category = classify(base_name, content)
        if category is None:
            skipped.append({"name": base_name, "reason": f"unsupported file type '{ext or '?'}'"})
            continue

        try:
            if category is FileCategory.IMAGE:
                produced = await _handle_binary(
                    db=db, content=content, base_name=base_name, ext=ext,
                    category=category, item_type="image", media_dir=media_dir,
                )
            elif category is FileCategory.PDF:
                produced = await _handle_binary(
                    db=db, content=content, base_name=base_name, ext=ext,
                    category=category, item_type="pdf", media_dir=media_dir,
                )
            elif category is FileCategory.DOCUMENT:
                produced = await _handle_binary(
                    db=db, content=content, base_name=base_name, ext=ext,
                    category=category, item_type="document", media_dir=media_dir,
                )
            elif category is FileCategory.TEXT:
                produced = await _handle_text(db=db, content=content, base_name=base_name)
            elif category is FileCategory.TABLE:
                produced = await _handle_table(
                    db=db, content=content, base_name=base_name, ext=ext,
                    columns_set=columns_set,
                )
            else:  # pragma: no cover - exhaustive
                continue
        except HTTPException:
            raise
        except Exception as exc:
            skipped.append({"name": base_name, "reason": str(exc)})
            continue

        for uf in produced:
            item_ids.append(uf.item_id)
            files_meta.append(uf.to_dict())
            # We increment by exactly 1 per produced item, which avoids
            # over-counting (e.g. 3x3=9) for table rows.
            stats[STATS_KEY[uf.category]] += 1

    await db.commit()

    return {
        "item_ids": item_ids,
        "files": files_meta,
        "columns": sorted(columns_set),
        "stats": stats,
        "skipped": skipped,
    }


# ── Batch draft item removal ───────────────────────────────────

@router.post("/items/draft/batch-delete")
async def batch_delete_draft_items(
    req: BatchDeleteRequest,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Batch cleanup for drafts.
    Skips destroying items that are active in other projects (REUSED).
    """
    deleted_count = 0
    detached_count = 0
    source_hashes_to_delete: set[str] = set()

    for item_id in req.item_ids:
        item = await db.get(Item, item_id)
        if not item:
            continue

        result = await db.exec(select(ProjectItemRef).where(ProjectItemRef.item_id == item_id))
        if result.first():
            # Reused by another launched project -> skip deletion, count as detached
            detached_count += 1
            continue

        # Clean file from disk
        if item.filename:
            media_path = get_settings().media_path / item.filename
            if media_path.exists():
                media_path.unlink()

        if item.source_hash:
            source_hashes_to_delete.add(item.source_hash)

        await db.delete(item)
        await cache_delete(cache_key_item(item_id))
        deleted_count += 1

    for sh in source_hashes_to_delete:
        upload = await db.get(TableUpload, sh)
        if upload:
            await db.delete(upload)

    await db.commit()

    return {
        "status": "success",
        "deleted_count": deleted_count,
        "detached_count": detached_count,
    }


# ── Draft item removal ────────────────────────────────────────

@router.delete("/items/{item_id}/draft")
async def delete_draft_item(
    item_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Safely handles item removal from a draft configuration.
    If the item is REUSED (has active session references), it simply unlinks it
    locally for the client without touching the file or global database registry.
    """
    item = await db.get(Item, item_id)
    if not item:
        return {"status": "not_found"}

    result = await db.exec(select(ProjectItemRef).where(ProjectItemRef.item_id == item_id))
    ref_count = len(result.all())

    # If other projects use it, do NOT delete it.
    if ref_count > 0:
        return {
            "status": "detached",
            "message": f"Item kept intact because it is active in {ref_count} other project(s).",
        }

    # If NO other projects are using it, completely scrub it from disk/db/cache
    if item.filename:
        media_path = get_settings().media_path / item.filename
        if media_path.exists():
            media_path.unlink()

    if item.source_hash:
        upload = await db.get(TableUpload, item.source_hash)
        if upload:
            await db.delete(upload)

    await db.delete(item)
    await db.commit()
    await cache_delete(cache_key_item(item_id))
    return {"status": "deleted"}


# ── Taxonomy / label parsing ──────────────────────────────────

@router.post("/upload/labels")
async def parse_labels(
    _user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
    has_header: bool = Form(True),
    delimiter: str = Form(""),
):
    content = await file.read()
    fname = (file.filename or "").lower()

    if fname.endswith(".txt"):
        text = content.decode("utf-8", errors="ignore")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        taxonomy: list[dict] = []
        for line in lines:
            if ">" in line:
                parts = [p.strip() for p in line.split(">") if p.strip()]
            elif ";" in line:
                parts = [p.strip() for p in line.split(";") if p.strip()]
            else:
                parts = [line]
            for i, part in enumerate(parts):
                path = " > ".join(parts[: i + 1])
                if not any(t["full_path"] == path for t in taxonomy):
                    taxonomy.append({
                        "name": part, "level": i + 1, "full_path": path,
                        "parent": " > ".join(parts[:i]) if i > 0 else None,
                    })
        return {"taxonomy": taxonomy}

    try:
        # Determine separator
        if fname.endswith(".tsv"):
            sep = "\t"
        elif delimiter and delimiter.strip():
            sep = delimiter.strip()
        else:
            text = content.decode("utf-8", errors="ignore")
            sep = next((s for s in [",", "\t", ";", "|"] if s in text), None)

        # Read raw text to find max fields and handle Excel separately
        if fname.endswith((".xlsx", ".xls")):
            df = pd.read_excel(
                io.BytesIO(content),
                header=0 if has_header else None,
                dtype=str,
            )
        else:
            text = content.decode("utf-8", errors="ignore")
            lines = [line for line in text.splitlines() if line.strip()]
            if not lines:
                return {"taxonomy": []}

            # Find maximum number of fields across all lines
            max_fields = 0
            for line in lines:
                fields = line.split(sep) if sep else [line]
                max_fields = max(max_fields, len(fields))

            skip = 1 if has_header and len(lines) > 0 else 0
            df = pd.read_csv(
                io.BytesIO(content),
                sep=sep,
                header=None,
                names=range(max_fields),
                skiprows=skip,
                engine="python",
                dtype=str,
            )

        df = df.where(pd.notnull(df), None)
        taxonomy = []
        for _, row in df.iterrows():
            parts = [
                str(x).strip() for x in row
                if x is not None and str(x).strip() not in ("", "nan", "None")
            ]
            if not parts:
                continue
            for i, part in enumerate(parts):
                path = " > ".join(parts[: i + 1])
                if not any(t["full_path"] == path for t in taxonomy):
                    taxonomy.append({
                        "name": part, "level": i + 1, "full_path": path,
                        "parent": " > ".join(parts[:i]) if i > 0 else None,
                    })
        return {"taxonomy": taxonomy}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
