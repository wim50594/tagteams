"""Persistent administrator-controlled application settings."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import SystemSetting, utcnow


class ApplicationSettingsService:
    DEFAULT_MAX_UPLOAD_SIZE_MB = 100
    DEFAULT_MIN_PASSWORD_LENGTH = 8

    _MAX_UPLOAD_SIZE_KEY = "max_upload_size_mb"
    _MIN_PASSWORD_LENGTH_KEY = "min_password_length"

    @classmethod
    async def _get_int(cls, db: AsyncSession, key: str, default: int) -> int:
        setting = await db.get(SystemSetting, key)
        return default if setting is None else int(setting.value)

    @classmethod
    async def _set_int(cls, db: AsyncSession, key: str, value: int) -> None:
        setting = await db.get(SystemSetting, key)
        if setting is None:
            setting = SystemSetting(key=key, value=str(value))
        else:
            setting.value = str(value)
            setting.updated_at = utcnow()
        db.add(setting)

    @classmethod
    async def get_max_upload_size_mb(cls, db: AsyncSession) -> int:
        return await cls._get_int(db, cls._MAX_UPLOAD_SIZE_KEY, cls.DEFAULT_MAX_UPLOAD_SIZE_MB)

    @classmethod
    async def get_min_password_length(cls, db: AsyncSession) -> int:
        return await cls._get_int(db, cls._MIN_PASSWORD_LENGTH_KEY, cls.DEFAULT_MIN_PASSWORD_LENGTH)

    @classmethod
    async def save(
        cls,
        db: AsyncSession,
        *,
        max_upload_size_mb: int,
        min_password_length: int,
    ) -> None:
        await cls._set_int(db, cls._MAX_UPLOAD_SIZE_KEY, max_upload_size_mb)
        await cls._set_int(db, cls._MIN_PASSWORD_LENGTH_KEY, min_password_length)
        await db.commit()

    @classmethod
    async def validate_password(cls, db: AsyncSession, password: str) -> None:
        minimum = await cls.get_min_password_length(db)
        if len(password) < minimum:
            raise ValueError(f"Password must be at least {minimum} characters long")
