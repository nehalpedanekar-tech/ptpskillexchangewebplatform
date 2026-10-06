from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
import threading

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sentence_transformers import SentenceTransformer
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import SessionLocal, get_db, initialize_database
import models
import schemas
from routes import auth
from routes import peers
from routes import messages
from routes.auth import get_current_user
from quiz import generate_quiz

logger = logging.getLogger(__name__)

load_dotenv()

_BADGE_SEEDS = (
    ("first_swap", "First Swap!", "Completed your first skill swap", "🎉"),
    ("five_swaps", "Super Swapper", "Completed five skill swaps", "🔥"),
    ("ten_swaps", "Swap Master", "Completed ten skill swaps", "🏆"),
    ("first_verified_skill", "Verified Expert", "Verified your first skill", "✅"),
    ("three_verified_skills", "Triple Threat", "Verified three skills", "🌟"),
    (
        "five_star_rating",
        "Top Rated",
        "Received an average rating of 4.5+ from at least 3 ratings",
        "⭐",
    ),
)


def _seed_badges():
    db = SessionLocal()
    try:
        badge_codes = [code for code, _, _, _ in _BADGE_SEEDS]
        existing_codes = {
            code
            for (code,) in db.query(models.Badge.code)
            .filter(models.Badge.code.in_(badge_codes))
            .all()
        }
        missing_badges = [
            models.Badge(code=code, name=name, description=description, icon=icon)
            for code, name, description, icon in _BADGE_SEEDS
            if code not in existing_codes
        ]
        if missing_badges:
            db.add_all(missing_badges)
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


initialize_database()
_seed_badges()


def check_and_award_badges(user_id: int, db: Session):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        return

    verified_skill_count = (
        db.query(func.count(models.SkillVerification.id))
        .filter(
            models.SkillVerification.user_id == user_id,
            models.SkillVerification.is_verified.is_(True),
        )
        .scalar()
        or 0
    )
    average_rating, rating_count = (
        db.query(func.avg(models.Rating.score), func.count(models.Rating.id))
        .filter(models.Rating.to_user_id == user_id)
        .one()
    )

    earned_codes = set()
    swaps_completed = user.swaps_completed or 0
    if swaps_completed >= 1:
        earned_codes.add("first_swap")
    if swaps_completed >= 5:
        earned_codes.add("five_swaps")
    if swaps_completed >= 10:
        earned_codes.add("ten_swaps")
    if verified_skill_count >= 1:
        earned_codes.add("first_verified_skill")
    if verified_skill_count >= 3:
        earned_codes.add("three_verified_skills")
    if rating_count >= 3 and average_rating is not None and average_rating >= 4.5:
        earned_codes.add("five_star_rating")
    if not earned_codes:
        return

    eligible_badges = (
        db.query(models.Badge)
        .filter(models.Badge.code.in_(earned_codes))
        .all()
    )
    badge_ids = [badge.id for badge in eligible_badges]
    if not badge_ids:
        return

    existing_badge_ids = {
        badge_id
        for (badge_id,) in db.query(models.UserBadge.badge_id)
        .filter(
            models.UserBadge.user_id == user_id,
            models.UserBadge.badge_id.in_(badge_ids),
        )
        .all()
    }
    new_user_badges = [
        models.UserBadge(user_id=user_id, badge_id=badge_id)
        for badge_id in badge_ids
        if badge_id not in existing_badge_ids
    ]
    if new_user_badges:
        db.add_all(new_user_badges)
        db.commit()


def _check_and_award_badges_safely(user_id: int, db: Session):
    try:
        check_and_award_badges(user_id, db)
    except Exception:
        try:
            db.rollback()
        except Exception:
            logger.exception("Could not roll back after badge award failure for user %s", user_id)
        logger.exception("Could not check or award badges for user %s", user_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/uploads", StaticFiles(directory=auth.UPLOAD_DIR, check_dir=False), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(peers.router)
app.include_router(messages.router)

@app.get("/")
def read_root():
    return {"message": "SkillExchange backend running!"}


def _get_badges_for_user(user_id: int, db: Session):
    earned_badges = (
        db.query(
            models.Badge.code,
            models.Badge.name,
            models.Badge.description,
            models.Badge.icon,
            models.UserBadge.earned_at,
        )
        .join(models.UserBadge, models.UserBadge.badge_id == models.Badge.id)
        .filter(models.UserBadge.user_id == user_id)
        .order_by(models.UserBadge.earned_at.desc(), models.Badge.id.asc())
        .all()
    )
    return [
        {
            "code": code,
            "name": name,
            "description": description,
            "icon": icon,
            "earned_at": earned_at,
        }
        for code, name, description, icon, earned_at in earned_badges
    ]


@app.get("/my-badges", response_model=list[schemas.EarnedBadgeResponse])
def get_my_badges(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _get_badges_for_user(current_user.id, db)


@app.get("/badges/{user_id}", response_model=list[schemas.EarnedBadgeResponse])
def get_user_badges(
    user_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_exists = db.query(models.User.id).filter(models.User.id == user_id).first()
    if user_exists is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _get_badges_for_user(user_id, db)


@app.get("/leaderboard", response_model=schemas.LeaderboardResponse)
def get_leaderboard(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rating_averages = (
        db.query(
            models.Rating.to_user_id.label("user_id"),
            func.avg(models.Rating.score).label("rating"),
        )
        .group_by(models.Rating.to_user_id)
        .subquery()
    )
    verified_skill_counts = (
        db.query(
            models.SkillVerification.user_id.label("user_id"),
            func.count(models.SkillVerification.id).label("verified_skills_count"),
        )
        .filter(
            models.SkillVerification.is_verified.is_(True),
            models.SkillVerification.score.in_((4, 5)),
        )
        .group_by(models.SkillVerification.user_id)
        .subquery()
    )
    badge_counts = (
        db.query(
            models.UserBadge.user_id.label("user_id"),
            func.count(models.UserBadge.id).label("badges_count"),
        )
        .group_by(models.UserBadge.user_id)
        .subquery()
    )
    swaps_completed = func.coalesce(models.User.swaps_completed, 0)
    ranked_users = (
        db.query(
            models.User.id.label("id"),
            models.User.name.label("name"),
            models.User.headline.label("headline"),
            rating_averages.c.rating.label("rating"),
            swaps_completed.label("swaps_completed"),
            func.coalesce(
                verified_skill_counts.c.verified_skills_count, 0
            ).label("verified_skills_count"),
            func.coalesce(badge_counts.c.badges_count, 0).label("badges_count"),
            func.row_number()
            .over(
                order_by=(
                    rating_averages.c.rating.desc(),
                    swaps_completed.desc(),
                    models.User.id.asc(),
                )
            )
            .label("user_rank"),
        )
        .join(rating_averages, rating_averages.c.user_id == models.User.id)
        .outerjoin(
            verified_skill_counts,
            verified_skill_counts.c.user_id == models.User.id,
        )
        .outerjoin(badge_counts, badge_counts.c.user_id == models.User.id)
        .subquery()
    )
    rows = (
        db.query(ranked_users)
        .filter(
            or_(
                ranked_users.c.user_rank <= 20,
                ranked_users.c.id == current_user.id,
            )
        )
        .order_by(ranked_users.c.user_rank)
        .all()
    )

    leaderboard = []
    my_rank = None
    for row in rows:
        user_rank = row.user_rank
        if row.id == current_user.id:
            my_rank = user_rank
        if user_rank <= 20:
            leaderboard.append(
                {
                    "rank": user_rank,
                    "id": row.id,
                    "name": row.name,
                    "headline": row.headline,
                    "rating": float(row.rating),
                    "swaps_completed": row.swaps_completed,
                    "verified_skills_count": row.verified_skills_count,
                    "badges_count": row.badges_count,
                    "badge_icons": [],
                }
            )

    leaderboard_user_ids = [entry["id"] for entry in leaderboard]
    if leaderboard_user_ids:
        badge_icon_rows = (
            db.query(models.UserBadge.user_id, models.Badge.icon)
            .join(models.Badge, models.Badge.id == models.UserBadge.badge_id)
            .filter(models.UserBadge.user_id.in_(leaderboard_user_ids))
            .order_by(models.UserBadge.earned_at.asc(), models.Badge.id.asc())
            .all()
        )
        badge_icons_by_user = {}
        for user_id, icon in badge_icon_rows:
            badge_icons_by_user.setdefault(user_id, []).append(icon)
        for entry in leaderboard:
            entry["badge_icons"] = badge_icons_by_user.get(entry["id"], [])

    return {"leaderboard": leaderboard, "my_rank": my_rank}


@app.get(
    "/verified-skills/{user_id}",
    response_model=list[schemas.VerifiedSkillResponse],
)
def get_user_verified_skills(
    user_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    verifications = (
        db.query(models.SkillVerification)
        .filter(
            models.SkillVerification.user_id == user_id,
            models.SkillVerification.is_verified.is_(True),
            models.SkillVerification.score.in_((4, 5)),
        )
        .order_by(models.SkillVerification.attempted_at.desc())
        .all()
    )
    return [
        {
            "skill": verification.skill,
            "is_verified": True,
            "score": verification.score,
            "total_questions": 5,
            "proficiency_label": "Expert" if verification.score == 5 else "Proficient",
            "attempted_at": verification.attempted_at,
        }
        for verification in verifications
    ]


# Keep answer keys server-side and expire unfinished quizzes after fifteen minutes.
_QUIZ_SESSION_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class _PendingSkillQuiz:
    answers: tuple[str, ...]
    options: tuple[tuple[str, ...], ...]
    expires_at: float


_pending_skill_quizzes: dict[tuple[int, str], _PendingSkillQuiz] = {}
_pending_skill_quizzes_lock = threading.Lock()


def _skill_quiz_key(user_id: int, skill: str) -> tuple[int, str]:
    return user_id, skill.strip().casefold()


def _require_taught_skill(current_user: models.User, skill: str) -> str:
    for taught_skill in (current_user.skills_teach or []):
        if isinstance(taught_skill, str) and taught_skill.casefold() == skill.casefold():
            return taught_skill
    raise HTTPException(
        status_code=400,
        detail="You can only verify a skill listed in skills_teach",
    )


def _normalize_quiz_answer(answer: str, options: tuple[str, ...]) -> str:
    labels = ("A", "B", "C", "D")
    if not isinstance(answer, str):
        raise HTTPException(status_code=422, detail="Each answer must be a string")

    selected = answer.strip()
    if selected.upper() in labels:
        return selected.upper()
    for label, option in zip(labels, options):
        if selected == option:
            return label
    raise HTTPException(
        status_code=422,
        detail="Answers must be A, B, C, D, or the exact option text",
    )


@app.get("/verify-skill/{skill}", response_model=schemas.SkillQuizResponse)
def get_skill_quiz(
    skill: str,
    current_user: models.User = Depends(get_current_user),
):
    normalized_skill = skill.strip()
    if not normalized_skill:
        raise HTTPException(status_code=400, detail="Skill cannot be empty")
    normalized_skill = _require_taught_skill(current_user, normalized_skill)

    try:
        generated_questions = generate_quiz(normalized_skill)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    key = _skill_quiz_key(current_user.id, normalized_skill)
    pending_quiz = _PendingSkillQuiz(
        answers=tuple(question["correct_answer"] for question in generated_questions),
        options=tuple(tuple(question["options"]) for question in generated_questions),
        expires_at=time.monotonic() + _QUIZ_SESSION_TTL_SECONDS,
    )
    with _pending_skill_quizzes_lock:
        now = time.monotonic()
        expired_keys = [
            quiz_key
            for quiz_key, quiz_session in _pending_skill_quizzes.items()
            if quiz_session.expires_at <= now
        ]
        for quiz_key in expired_keys:
            del _pending_skill_quizzes[quiz_key]
        _pending_skill_quizzes[key] = pending_quiz

    # Do not include answer keys in the public quiz response.
    return {
        "skill": normalized_skill,
        "questions": [
            {"question": question["question"], "options": question["options"]}
            for question in generated_questions
        ],
    }


@app.post(
    "/verify-skill/{skill}/submit",
    response_model=schemas.SkillVerificationResult,
)
def submit_skill_quiz(
    skill: str,
    request: schemas.SkillVerificationSubmit,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    normalized_skill = skill.strip()
    if not normalized_skill:
        raise HTTPException(status_code=400, detail="Skill cannot be empty")
    normalized_skill = _require_taught_skill(current_user, normalized_skill)
    if len(request.answers) != 5:
        raise HTTPException(status_code=422, detail="Exactly five answers are required")

    key = _skill_quiz_key(current_user.id, normalized_skill)
    with _pending_skill_quizzes_lock:
        pending_quiz = _pending_skill_quizzes.get(key)
        if pending_quiz is None:
            raise HTTPException(
                status_code=404,
                detail="No active quiz found; request a new quiz before submitting",
            )
        if pending_quiz.expires_at <= time.monotonic():
            del _pending_skill_quizzes[key]
            raise HTTPException(status_code=410, detail="The quiz has expired")

        selected_answers = tuple(
            _normalize_quiz_answer(answer, options)
            for answer, options in zip(request.answers, pending_quiz.options)
        )
        del _pending_skill_quizzes[key]

    score = sum(
        selected == correct
        for selected, correct in zip(selected_answers, pending_quiz.answers)
    )
    is_verified = score >= 4
    # Store only the latest score; the model's unique constraint enforces one row per skill.
    verification = (
        db.query(models.SkillVerification)
        .filter(
            models.SkillVerification.user_id == current_user.id,
            func.lower(models.SkillVerification.skill) == normalized_skill.casefold(),
        )
        .with_for_update()
        .first()
    )
    if verification is None:
        verification = models.SkillVerification(
            user_id=current_user.id,
            skill=normalized_skill,
            is_verified=is_verified,
            score=score,
            total_questions=len(pending_quiz.answers),
            attempted_at=datetime.now(timezone.utc),
        )
        db.add(verification)
    else:
        verification.skill = normalized_skill
        verification.is_verified = is_verified
        verification.score = score
        verification.total_questions = len(pending_quiz.answers)
        verification.attempted_at = datetime.now(timezone.utc)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A verification attempt for this skill was saved concurrently; request a new quiz",
        ) from exc

    if is_verified:
        _check_and_award_badges_safely(current_user.id, db)

    return {
        "skill": normalized_skill,
        "score": score,
        "total_questions": len(pending_quiz.answers),
        "is_verified": is_verified,
    }


def _serialize_swap_request(db: Session, swap_request: models.SwapRequest):
    from_user = db.query(models.User).filter(models.User.id == swap_request.from_user_id).first()
    to_user = db.query(models.User).filter(models.User.id == swap_request.to_user_id).first()
    if from_user is None or to_user is None:
        raise HTTPException(status_code=404, detail="A user associated with this swap request was not found")
    return {
        "id": swap_request.id,
        "from_user_name": from_user.name,
        "to_user_name": to_user.name,
        "skill": swap_request.skill,
        "status": swap_request.status,
        "created_at": swap_request.created_at,
    }


@app.post(
    "/swap-request",
    response_model=schemas.SwapRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_swap_request(
    request: schemas.SwapRequestCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.to_user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot send a swap request to yourself")

    to_user = db.query(models.User).filter(models.User.id == request.to_user_id).first()
    if to_user is None:
        raise HTTPException(status_code=404, detail="Recipient user not found")

    skill = request.skill.strip()
    if not skill:
        raise HTTPException(status_code=400, detail="Skill cannot be empty")

    swap_request = models.SwapRequest(
        from_user_id=current_user.id,
        to_user_id=to_user.id,
        skill=skill,
        status="pending",
    )
    db.add(swap_request)
    db.commit()
    db.refresh(swap_request)
    return _serialize_swap_request(db, swap_request)


@app.get("/swap-requests/received", response_model=list[schemas.SwapRequestResponse])
def get_received_swap_requests(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    swap_requests = (
        db.query(models.SwapRequest)
        .filter(
            models.SwapRequest.to_user_id == current_user.id,
            models.SwapRequest.status == "pending",
        )
        .order_by(models.SwapRequest.created_at.desc())
        .all()
    )
    return [_serialize_swap_request(db, item) for item in swap_requests]


@app.get("/notifications/summary", response_model=schemas.NotificationSummaryResponse)
def get_notifications_summary(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pending_swap_requests = (
        db.query(func.count(models.SwapRequest.id))
        .filter(
            models.SwapRequest.to_user_id == current_user.id,
            models.SwapRequest.status == "pending",
        )
        .scalar()
        or 0
    )
    accepted_peer_ids = (
        db.query(
            models.SwapRequest.from_user_id.label("peer_id")
        )
        .filter(
            models.SwapRequest.status == "accepted",
            models.SwapRequest.to_user_id == current_user.id,
        )
        .union(
            db.query(models.SwapRequest.to_user_id.label("peer_id")).filter(
                models.SwapRequest.status == "accepted",
                models.SwapRequest.from_user_id == current_user.id,
            )
        )
        .subquery()
    )
    unread_messages = (
        db.query(func.count(models.Message.id))
        .filter(
            models.Message.receiver_id == current_user.id,
            models.Message.is_read.is_(False),
            models.Message.sender_id.in_(db.query(accepted_peer_ids.c.peer_id)),
        )
        .scalar()
        or 0
    )
    return {
        "pending_swap_requests": pending_swap_requests,
        "unread_messages": unread_messages,
        "total": pending_swap_requests + unread_messages,
    }


@app.get("/swap-requests/sent", response_model=list[schemas.SwapRequestResponse])
def get_sent_swap_requests(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    swap_requests = (
        db.query(models.SwapRequest)
        .filter(models.SwapRequest.from_user_id == current_user.id)
        .order_by(models.SwapRequest.created_at.desc())
        .all()
    )
    return [_serialize_swap_request(db, item) for item in swap_requests]


@app.get(
    "/swap-requests/accepted",
    response_model=list[schemas.AcceptedSwapRequestResponse],
)
def get_accepted_swap_requests(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    swap_requests = (
        db.query(models.SwapRequest)
        .filter(
            models.SwapRequest.status == "accepted",
            (models.SwapRequest.from_user_id == current_user.id)
            | (models.SwapRequest.to_user_id == current_user.id),
        )
        .order_by(models.SwapRequest.created_at.desc())
        .all()
    )
    request_ids = [swap_request.id for swap_request in swap_requests]
    rated_request_ids = set()
    if request_ids:
        rated_request_ids = {
            request_id
            for (request_id,) in (
                db.query(models.Rating.swap_request_id)
                .filter(
                    models.Rating.from_user_id == current_user.id,
                    models.Rating.swap_request_id.in_(request_ids),
                )
                .all()
            )
        }

    accepted_swaps = []
    for swap_request in swap_requests:
        peer_id = (
            swap_request.to_user_id
            if swap_request.from_user_id == current_user.id
            else swap_request.from_user_id
        )
        peer = db.query(models.User).filter(models.User.id == peer_id).first()
        if peer is None:
            raise HTTPException(status_code=404, detail="A user associated with this swap was not found")
        accepted_swaps.append(
            {
                "id": swap_request.id,
                "peer_user_id": peer.id,
                "peer_name": peer.name,
                "skill": swap_request.skill,
                "status": swap_request.status,
                "created_at": swap_request.created_at,
                "rated_by_current_user": swap_request.id in rated_request_ids,
            }
        )
    return accepted_swaps


def _get_pending_request_for_recipient(
    request_id: int,
    current_user: models.User,
    db: Session,
):
    swap_request = (
        db.query(models.SwapRequest)
        .filter(models.SwapRequest.id == request_id)
        .first()
    )
    if swap_request is None:
        raise HTTPException(status_code=404, detail="Swap request not found")
    if swap_request.to_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the recipient can respond to this request")
    if swap_request.status != "pending":
        raise HTTPException(status_code=409, detail="Swap request is no longer pending")
    return swap_request


@app.put(
    "/swap-request/{request_id}/accept",
    response_model=schemas.SwapRequestResponse,
)
def accept_swap_request(
    request_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    swap_request = _get_pending_request_for_recipient(request_id, current_user, db)
    from_user = db.query(models.User).filter(models.User.id == swap_request.from_user_id).first()
    if from_user is None:
        raise HTTPException(status_code=404, detail="Sender user not found")

    swap_request.status = "accepted"
    current_user.swaps_completed = (current_user.swaps_completed or 0) + 1
    from_user.swaps_completed = (from_user.swaps_completed or 0) + 1
    db.commit()
    db.refresh(swap_request)
    _check_and_award_badges_safely(current_user.id, db)
    _check_and_award_badges_safely(from_user.id, db)
    return _serialize_swap_request(db, swap_request)


@app.put(
    "/swap-request/{request_id}/reject",
    response_model=schemas.SwapRequestResponse,
)
def reject_swap_request(
    request_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    swap_request = _get_pending_request_for_recipient(request_id, current_user, db)
    swap_request.status = "rejected"
    db.commit()
    db.refresh(swap_request)
    return _serialize_swap_request(db, swap_request)


def _serialize_rating(rating: models.Rating, rater_name: str):
    return {
        "id": rating.id,
        "swap_request_id": rating.swap_request_id,
        "score": rating.score,
        "comment": rating.comment,
        "rater_name": rater_name,
        "created_at": rating.created_at,
    }


@app.post(
    "/rate",
    response_model=schemas.RatingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_rating(
    request: schemas.RatingCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    swap_request = (
        db.query(models.SwapRequest)
        .filter(models.SwapRequest.id == request.swap_request_id)
        .first()
    )
    if swap_request is None:
        raise HTTPException(status_code=404, detail="Swap request not found")
    if swap_request.status != "accepted":
        raise HTTPException(status_code=409, detail="Only accepted swap requests can be rated")

    if current_user.id == swap_request.from_user_id:
        rated_user_id = swap_request.to_user_id
    elif current_user.id == swap_request.to_user_id:
        rated_user_id = swap_request.from_user_id
    else:
        raise HTTPException(status_code=403, detail="You were not part of this swap request")

    existing_rating = (
        db.query(models.Rating)
        .filter(
            models.Rating.swap_request_id == swap_request.id,
            models.Rating.from_user_id == current_user.id,
        )
        .first()
    )
    if existing_rating is not None:
        raise HTTPException(status_code=409, detail="You have already rated this swap")

    rated_user = (
        db.query(models.User)
        .filter(models.User.id == rated_user_id)
        .with_for_update()
        .first()
    )
    if rated_user is None:
        raise HTTPException(status_code=404, detail="User being rated was not found")

    rating = models.Rating(
        swap_request_id=swap_request.id,
        from_user_id=current_user.id,
        to_user_id=rated_user.id,
        score=request.score,
        comment=request.comment,
    )
    db.add(rating)
    try:
        db.flush()
        average_score = (
            db.query(func.avg(models.Rating.score))
            .filter(models.Rating.to_user_id == rated_user.id)
            .scalar()
        )
        rated_user.rating = float(average_score or 0.0)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="You have already rated this swap") from exc

    db.refresh(rating)
    _check_and_award_badges_safely(rated_user.id, db)
    return _serialize_rating(rating, current_user.name)


@app.get("/ratings/{user_id}", response_model=list[schemas.RatingResponse])
def get_user_ratings(
    user_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rated_user = db.query(models.User).filter(models.User.id == user_id).first()
    if rated_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    ratings = (
        db.query(models.Rating, models.User.name)
        .join(models.User, models.Rating.from_user_id == models.User.id)
        .filter(models.Rating.to_user_id == user_id)
        .order_by(models.Rating.created_at.desc())
        .all()
    )
    return [_serialize_rating(rating, rater_name) for rating, rater_name in ratings]