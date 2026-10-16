"""Persistent, administrator-controlled invitation settings."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import SystemSetting, utcnow


class InvitationSettingsService:
    DEFAULT_EXPIRY_DAYS = 7
    _KEY = "account_invitation_expiry_days"

    @classmethod
    async def get_default_expiry_days(cls, db: AsyncSession) -> int | None:
        setting = await db.get(SystemSetting, cls._KEY)
        if setting is None:
            return cls.DEFAULT_EXPIRY_DAYS
        return None if setting.value == "never" else int(setting.value)

    @classmethod
    async def set_default_expiry_days(
        cls, db: AsyncSession, expires_in_days: int | None
    ) -> int | None:
        value = "never" if expires_in_days is None else str(expires_in_days)
        setting = await db.get(SystemSetting, cls._KEY)
        if setting is None:
            setting = SystemSetting(key=cls._KEY, value=value)
        else:
            setting.value = value
            setting.updated_at = utcnow()
        db.add(setting)
        await db.commit()
        return expires_in_days
