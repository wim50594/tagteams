"""Invitation lifetimes and registration behaviour."""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.auth import hash_password
from app.models import AccountInvitation, Invitation, Project, ProjectMember, User, utcnow
from app.services.iam import IAMService
from app.services.invitation_settings import InvitationSettingsService


async def _admin_and_project(db):
    admin = User(
        username="admin",
        display_name="Admin",
        hashed_password=hash_password("test123"),
        is_admin=True,
    )
    db.add(admin)
    await db.flush()
    project = Project(name="Literature review", owner_id=admin.id)
    db.add(project)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=admin.id, role="owner"))
    await db.commit()
    return admin, project


@pytest.mark.asyncio
async def test_default_account_invitation_expiry_is_seven_days(db):
    assert await InvitationSettingsService.get_default_expiry_days(db) == 7

    await InvitationSettingsService.set_default_expiry_days(db, 14)
    assert await InvitationSettingsService.get_default_expiry_days(db) == 14

    await InvitationSettingsService.set_default_expiry_days(db, None)
    assert await InvitationSettingsService.get_default_expiry_days(db) is None


@pytest.mark.asyncio
async def test_account_invitation_creates_user_without_project_membership(db):
    admin, project = await _admin_and_project(db)
    invitation = await IAMService.create_account_invitation(db, admin.id, 7)

    user = await IAMService.register_with_account_invitation(
        db, invitation.token, "student", "Student", "secret1"
    )

    assert user.username == "student"
    assert user.is_admin is False
    memberships = (await db.exec(
        select(ProjectMember).where(ProjectMember.user_id == user.id)
    )).all()
    assert memberships == []
    stored_invitation = (await db.exec(
        select(AccountInvitation).where(AccountInvitation.token == invitation.token)
    )).first()
    assert stored_invitation is not None
    assert stored_invitation.used is True
    assert project.id is not None


@pytest.mark.asyncio
async def test_project_account_invitation_adds_one_new_user_to_project(db):
    admin, project = await _admin_and_project(db)
    invitation = await IAMService.create_invitation(
        db, project.id, "annotator", admin.id, 7
    )

    user = await IAMService.register_with_invitation(
        db, invitation.token, "student", "Student", "secret1"
    )

    membership = (await db.exec(select(ProjectMember).where(
        ProjectMember.project_id == project.id,
        ProjectMember.user_id == user.id,
    ))).first()
    assert membership is not None
    assert membership.role == "annotator"
    stored_invitation = (await db.exec(
        select(Invitation).where(Invitation.token == invitation.token)
    )).first()
    assert stored_invitation is not None
    assert stored_invitation.used is True


@pytest.mark.asyncio
async def test_unlimited_project_invitation_remains_valid(db):
    admin, project = await _admin_and_project(db)
    invitation = await IAMService.create_invitation(
        db, project.id, "annotator", admin.id, None
    )

    assert IAMService.is_unlimited_project_invitation(invitation)
    assert await IAMService.validate_invitation(db, invitation.token) == invitation


@pytest.mark.asyncio
async def test_legacy_unlimited_project_invitation_is_expired_after_seven_days(db):
    admin, project = await _admin_and_project(db)
    invitation = Invitation(
        token="legacy-token",
        project_id=project.id,
        role="annotator",
        created_by=admin.id,
        created_at=utcnow() - timedelta(days=8),
        expires_at=None,
    )
    db.add(invitation)
    await db.commit()

    with pytest.raises(ValueError, match="expired"):
        await IAMService.validate_invitation(db, invitation.token)
