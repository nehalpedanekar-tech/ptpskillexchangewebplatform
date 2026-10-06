from sqlalchemy.orm import Session

import models


def get_badge_summaries_by_user_ids(db: Session, user_ids: list[int]):
    if not user_ids:
        return {}

    badge_rows = (
        db.query(
            models.UserBadge.user_id,
            models.Badge.code,
            models.Badge.name,
            models.Badge.icon,
        )
        .join(models.Badge, models.Badge.id == models.UserBadge.badge_id)
        .filter(models.UserBadge.user_id.in_(user_ids))
        .order_by(models.UserBadge.earned_at.desc(), models.Badge.id.asc())
        .all()
    )
    badges_by_user = {}
    for user_id, code, name, icon in badge_rows:
        badges_by_user.setdefault(user_id, []).append(
            {"code": code, "name": name, "icon": icon}
        )
    return badges_by_user