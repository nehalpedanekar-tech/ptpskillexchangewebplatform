from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    headline: str = ""
    avatar_url: str | None = None
    skills_teach: list[str] = Field(default_factory=list)
    skills_learn: list[str] = Field(default_factory=list)

class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    headline: str
    avatar_url: str | None = None
    skills_teach: list[str] = Field(default_factory=list)
    skills_learn: list[str] = Field(default_factory=list)
    rating: float
    swaps_completed: int

    class Config:
        from_attributes = True

class ProfileUpdate(BaseModel):
    headline: str
    # JSON skill columns are represented by arrays of individual skill names.
    skills_teach: list[str] = Field(default_factory=list)
    skills_learn: list[str] = Field(default_factory=list)

class ProfileResponse(BaseModel):
    headline: str
    avatar_url: str | None = None
    skills_teach: list[str] = Field(default_factory=list)
    skills_learn: list[str] = Field(default_factory=list)
    rating: float
    swaps_completed: int

    class Config:
        from_attributes = True


class ProfileSkillVerification(BaseModel):
    skill: str
    is_verified: bool
    score: int | None = None
    total_questions: int = 5
    proficiency_label: Literal["Expert", "Proficient", "Not verified"]
    attempted_at: datetime | None = None


class VerifiedSkillResponse(BaseModel):
    skill: str
    is_verified: bool
    score: int = Field(ge=4, le=5)
    total_questions: int = 5
    proficiency_label: Literal["Proficient", "Expert"]
    attempted_at: datetime | None = None


class BadgeSummary(BaseModel):
    code: str
    name: str
    icon: str


class EarnedBadgeResponse(BaseModel):
    code: str
    name: str
    description: str
    icon: str
    earned_at: datetime


class LeaderboardEntry(BaseModel):
    rank: int
    id: int
    name: str
    headline: str
    rating: float
    swaps_completed: int
    verified_skills_count: int
    badges_count: int
    badge_icons: list[str] = Field(default_factory=list)


class LeaderboardResponse(BaseModel):
    leaderboard: list[LeaderboardEntry]
    my_rank: int | None


class ProfileMeResponse(ProfileResponse):
    verified_skills: list[str] = Field(default_factory=list)
    skill_verifications: list[ProfileSkillVerification] = Field(default_factory=list)
    badges: list[BadgeSummary] = Field(default_factory=list)

class AvatarResponse(BaseModel):
    avatar_url: str


class PeerSkillVerification(BaseModel):
    is_verified: bool
    score: int | None = Field(default=None, ge=0, le=5)
    proficiency_label: Literal["Expert", "Proficient", "Not verified", "Not verified yet"]


class PeerSkillMatch(BaseModel):
    my_skill: str
    their_skill: str
    score: float
    verification: PeerSkillVerification


class SkillProficiency(BaseModel):
    skill: str
    score: int = Field(ge=4, le=5)
    proficiency_level: Literal["Proficient", "Expert"]


class PeerSuggestionResponse(BaseModel):
    id: int
    name: str
    badges: list[BadgeSummary] = Field(default_factory=list)
    matching_skills: list[PeerSkillMatch] = Field(default_factory=list)
    verified_skills: list[str] = Field(default_factory=list)
    skill_proficiency: list[SkillProficiency] = Field(default_factory=list)


class SwapRequestCreate(BaseModel):
    to_user_id: int
    skill: str


class SwapRequestResponse(BaseModel):
    id: int
    from_user_name: str
    to_user_name: str
    skill: str
    status: str
    created_at: datetime


class AcceptedSwapRequestResponse(BaseModel):
    id: int
    peer_user_id: int
    peer_name: str
    skill: str
    status: str
    created_at: datetime
    rated_by_current_user: bool


class RatingCreate(BaseModel):
    swap_request_id: int
    score: int = Field(ge=1, le=5)
    comment: str | None = None


class RatingResponse(BaseModel):
    id: int
    swap_request_id: int
    score: int
    comment: str | None = None
    rater_name: str
    created_at: datetime


class MessageCreate(BaseModel):
    receiver_id: int
    content: str = Field(min_length=1)


class MessageResponse(BaseModel):
    id: int
    sender_id: int
    sender_name: str
    receiver_id: int
    content: str
    created_at: datetime
    is_read: bool


class ConversationResponse(BaseModel):
    other_user_id: int
    other_user_name: str
    last_message_content: str
    last_message_at: datetime
    unread_count: int


class SkillQuizQuestion(BaseModel):
    question: str
    options: list[str]


class SkillQuizResponse(BaseModel):
    skill: str
    questions: list[SkillQuizQuestion]


class SkillVerificationSubmit(BaseModel):
    answers: list[str]


class SkillVerificationChatMessage(BaseModel):
    message: str = Field(min_length=1)


class SkillVerificationResult(BaseModel):
    skill: str
    score: int
    total_questions: int
    is_verified: bool
    
class NotificationSummaryResponse(BaseModel):
    pending_swap_requests: int
    unread_messages: int
    total: int
