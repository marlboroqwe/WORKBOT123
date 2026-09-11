from __future__ import annotations

from config import ADMIN_IDS
from database import db

# Полный доступ к кнопкам: добавить воркера / бан / разбан
SUPER_ADMIN_IDS = {8286295216, 7857899220}


async def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    return await db.is_admin(user_id)


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS
