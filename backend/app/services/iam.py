"""
Identity & Access Management service.
Handles registration, invitations, and profile management.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import hash_password
from app.models import AccountInvitation, Invitation, Project, ProjectMember, User, utcnow
from app.services.workload import WorkloadService


# ``Invitation.expires_at = NULL`` already exists in production and means a
# legacy unbounded link. Store newly configured "never" project links with an
# explicit sentinel so legacy links can still be retired after seven days.
_UNLIMITED_PROJECT_INVITATION_EXPIRY = datetime.max.replace(microsecond=0)


class IAMService:
    """Stateless service for user and invitation operations."""

    @staticmethod
    async def create_invitation(
        db: AsyncSession,
        project_id: int,
        role: str,
        created_by: int,
        expires_in_days: int | None = None,
    ) -> Invitation:
        """Generate a secure invite token for a project and role."""
        token = secrets.token_urlsafe(32)
        expires_at = (
            _UNLIMITED_PROJECT_INVITATION_EXPIRY
            if expires_in_days is None
            else utcnow() + timedelta(days=expires_in_days)
        )

        invitation = Invitation(
            token=token,
            project_id=project_id,
            role=role,
            created_by=created_by,
            expires_at=expires_at,
        )
        db.add(invitation)
        await db.commit()
        await db.refresh(invitation)
        return invitation

    @staticmethod
    async def validate_invitation(
        db: AsyncSession, token: str
    ) -> Invitation:
        """Validate an invitation token. Returns the Invitation or raises."""
        result = await db.exec(select(Invitation).where(Invitation.token == token))
        invitation = result.first()
        if not invitation:
            raise ValueError("Invalid invitation token")
        if invitation.used:
            raise ValueError("Invitation token has already been used")
        # Older invitations were created before expiry was mandatory. They
        # intentionally receive the original seven-day lifetime as well.
        if invitation.expires_at is None:
            expires_at = invitation.created_at + timedelta(days=7)
        elif invitation.expires_at == _UNLIMITED_PROJECT_INVITATION_EXPIRY:
            return invitation
        else:
            expires_at = invitation.expires_at
        if expires_at < utcnow():
            raise ValueError("Invitation token has expired")
        return invitation

    @staticmethod
    def is_unlimited_project_invitation(invitation: Invitation) -> bool:
        return invitation.expires_at == _UNLIMITED_PROJECT_INVITATION_EXPIRY

    @staticmethod
    async def create_account_invitation(
        db: AsyncSession,
        created_by: int,
        expires_in_days: int | None,
        is_admin: bool = False,
    ) -> AccountInvitation:
        """Create a one-use account invitation without project membership."""
        invitation = AccountInvitation(
            token=secrets.token_urlsafe(32),
            created_by=created_by,
            is_admin=is_admin,
            expires_at=(utcnow() + timedelta(days=expires_in_days))
            if expires_in_days is not None else None,
        )
        db.add(invitation)
        await db.commit()
        await db.refresh(invitation)
        return invitation

    @staticmethod
    async def validate_account_invitation(
        db: AsyncSession, token: str
    ) -> AccountInvitation:
        result = await db.exec(
            select(AccountInvitation).where(AccountInvitation.token == token)
        )
        invitation = result.first()
        if not invitation:
            raise ValueError("Invalid invitation token")
        if invitation.used:
            raise ValueError("Invitation token has already been used")
        if invitation.expires_at and invitation.expires_at < utcnow():
            raise ValueError("Invitation token has expired")
        return invitation

    @staticmethod
    async def validate_registration_invitation(
        db: AsyncSession, token: str
    ) -> tuple[str, Invitation | AccountInvitation]:
        """Find and validate either kind of account-creation invitation."""
        project_invitation = (await db.exec(
            select(Invitation).where(Invitation.token == token)
        )).first()
        if project_invitation:
            return "project", await IAMService.validate_invitation(db, token)
        return "account", await IAMService.validate_account_invitation(db, token)

    @staticmethod
    async def _claim_invitation(
        db: AsyncSession, invitation: Invitation | AccountInvitation
    ) -> None:
        """Atomically reserve a link so concurrent requests cannot reuse it."""
        invitation_type = type(invitation)
        result = await db.exec(
            update(invitation_type)
            .where(invitation_type.id == invitation.id, invitation_type.used.is_(False))
            .values(used=True)
        )
        if result.rowcount != 1:
            raise ValueError("Invitation token has already been used")

    @staticmethod
    async def _create_invited_user(
        db: AsyncSession, username: str, display_name: str, password: str
    ) -> User:
        result = await db.exec(select(User).where(User.username == username.lower()))
        if result.first():
            raise ValueError("Username already taken")
        user = User(
            username=username.lower(),
            display_name=display_name,
            hashed_password=hash_password(password),
            is_admin=False,
        )
        db.add(user)
        await db.flush()
        return user

    @staticmethod
    async def register_with_invitation(
        db: AsyncSession,
        token: str,
        username: str,
        display_name: str,
        password: str,
    ) -> User:
        """Register a new user using a valid invitation token.

        Creates the user, consumes the invitation, and adds the user as a
        project member with the invited role.
        """
        invitation = await IAMService.validate_invitation(db, token)

        await IAMService._claim_invitation(db, invitation)
        user = await IAMService._create_invited_user(
            db, username, display_name, password
        )

        # Add as project member
        membership = ProjectMember(
            project_id=invitation.project_id,
            user_id=user.id,
            role=invitation.role,
        )
        db.add(membership)

        # Redistribute batches: give new member unlabeled items from existing members
        project = await db.get(Project, invitation.project_id)
        if project:
            from app.routes.project_routes import _read_batches, _write_batches
            old_batches = await _read_batches(db, project.id)
            if not old_batches:
                old_batches = project.batches or {}
            if old_batches:
                # Temporarily set for redistribution
                project.batches = old_batches
                new_batches = await WorkloadService.redistribute_on_member_added(
                    db=db,
                    project=project,
                    new_member_username=user.username,
                )
                if new_batches:
                    await _write_batches(db, project.id, new_batches)

        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def register_with_account_invitation(
        db: AsyncSession,
        token: str,
        username: str,
        display_name: str,
        password: str,
    ) -> User:
        """Register a new user from an account-only invitation."""
        invitation = await IAMService.validate_account_invitation(db, token)
        await IAMService._claim_invitation(db, invitation)
        user = await IAMService._create_invited_user(
            db, username, display_name, password
        )
        user.is_admin = invitation.is_admin
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def get_project_role(
        db: AsyncSession, project_id: int, user_id: int
    ) -> str | None:
        """Return the user's role in a project, or None if not a member."""
        result = await db.exec(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
        member = result.first()
        return member.role if member else None

    @staticmethod
    async def can_manage_project(
        db: AsyncSession, project_id: int, user: User
    ) -> bool:
        """Check whether a user can manage (edit/delete/resolve) a project."""
        if user.is_admin:
            return True
        role = await IAMService.get_project_role(db, project_id, user.id)
        return role in ("owner", "maintainer")

    @staticmethod
    async def can_annotate_project(
        db: AsyncSession, project_id: int, user: User
    ) -> bool:
        """Check whether a user can annotate in a project."""
        if user.is_admin:
            return True
        role = await IAMService.get_project_role(db, project_id, user.id)
        return role is not None  # any member can annotate

    @staticmethod
    async def update_profile(
        db: AsyncSession,
        user: User,
        display_name: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> User:
        """Update a user's profile fields. Returns the updated user."""
        if display_name is not None:
            user.display_name = display_name
        if username is not None:
            new_username = username.lower()
            # Ensure new username is not taken by someone else
            result = await db.exec(
                select(User).where(
                    User.username == new_username, User.id != user.id
                )
            )
            if result.first():
                raise ValueError("Username already taken")
            user.username = new_username
        if password is not None:
            user.hashed_password = hash_password(password)

        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user
