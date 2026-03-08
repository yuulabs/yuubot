"""Role permission system — query and manage user roles."""

from yuubot.core.models import Role, RoleRecord


class RoleManager:
    def __init__(self, master_qq: int) -> None:
        self.master_qq = master_qq

    async def get(self, user_id: int, scope: str = "global") -> Role:
        """Get effective role for user in scope. Master always returns MASTER."""
        if user_id == self.master_qq:
            return Role.MASTER
        for s in (scope, "global"):
            row = await RoleRecord.filter(user_id=user_id, scope=s).first()
            if row is not None:
                return Role(row.role)
        return Role.FOLK

    async def set(self, user_id: int, role: Role, scope: str = "global") -> None:
        """Set role for user in scope."""
        await RoleRecord.update_or_create(
            defaults={"role": role.value},
            user_id=user_id,
            scope=scope,
        )

    async def remove(self, user_id: int, scope: str = "global") -> None:
        await RoleRecord.filter(user_id=user_id, scope=scope).delete()
