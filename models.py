from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    headline = Column(String, nullable=False, default="")
    avatar_url = Column(String, nullable=True)
    skills_teach = Column(JSON, nullable=False, default=list, server_default="[]")
    skills_learn = Column(JSON, nullable=False, default=list, server_default="[]")
    rating = Column(Float, nullable=False, default=0.0)
    swaps_completed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SwapRequest(Base):
    __tablename__ = "swap_requests"

    id = Column(Integer, primary_key=True, index=True)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    skill = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending", server_default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("score >= 1 AND score <= 5", name="ck_ratings_score_range"),
        UniqueConstraint(
            "swap_request_id",
            "from_user_id",
            name="uq_ratings_swap_request_from_user",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    swap_request_id = Column(Integer, ForeignKey("swap_requests.id"), nullable=False)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    score = Column(Integer, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    is_read = Column(Boolean, nullable=False, default=False, server_default="false")


# Keep one latest verification result per user and skill; retries update this row.
class SkillVerification(Base):
    __tablename__ = "skill_verifications"
    __table_args__ = (
        UniqueConstraint("user_id", "skill", name="uq_skill_verifications_user_skill"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    skill = Column(String, nullable=False)
    is_verified = Column(Boolean, nullable=False, default=False, server_default="false")
    score = Column(Integer, nullable=False)
    total_questions = Column(Integer, nullable=False)
    attempted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Badge(Base):
    __tablename__ = "badges"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    icon = Column(String, nullable=False)


class UserBadge(Base):
    __tablename__ = "user_badges"
    __table_args__ = (
        UniqueConstraint("user_id", "badge_id", name="uq_user_badges_user_badge"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    badge_id = Column(Integer, ForeignKey("badges.id"), nullable=False)
    earned_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())