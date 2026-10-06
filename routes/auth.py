from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from datetime import datetime, timedelta
from datetime import timezone
from pathlib import Path
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import os
import shutil
import uuid
import models
import schemas
from database import get_db
from routes.badges import get_badge_summaries_by_user_ids

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set before starting the API")


@router.post("/register", response_model=schemas.UserResponse, status_code=201)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_password = pwd_context.hash(user.password)
    new_user = models.User(
        name=user.name,
        email=user.email,
        password_hash=hashed_password,
        headline=user.headline,
        avatar_url=user.avatar_url,
        skills_teach=user.skills_teach,
        skills_learn=user.skills_learn,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


def create_access_token(data: dict):
    # Sign the token with the same key and algorithm used by get_current_user.
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not pwd_context.verify(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Invalid or expired authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    print(f"[auth] raw token received: {token!r}")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        print(f"[auth] decoded JWT payload: {payload!r}")
        email = payload.get("sub")
        if not isinstance(email, str) or not email:
            print(f"[auth] invalid sub claim: {email!r}")
            raise credentials_exception
    except JWTError as exc:
        print(f"[auth] JWT decode exception: {exc!r} ({str(exc)})")
        raise credentials_exception

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        print(f"[auth] no database user found for sub/email: {email!r}")
        raise credentials_exception
    print(f"[auth] authenticated database user: id={user.id}, email={user.email!r}")
    return user


@router.get("/me", response_model=schemas.UserResponse)
def read_current_user(current_user: models.User = Depends(get_current_user)):
    return current_user


@router.put("/profile", response_model=schemas.ProfileResponse)
def update_profile(
    profile: schemas.ProfileUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.headline = profile.headline
    # Persist the request's string arrays directly into the JSON columns.
    current_user.skills_teach = list(profile.skills_teach)
    current_user.skills_learn = list(profile.skills_learn)
    print(
        f"[profile] saving skills_teach={current_user.skills_teach!r}, "
        f"skills_learn={current_user.skills_learn!r}"
    )
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/profile/me", response_model=schemas.ProfileMeResponse)
def read_profile(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verification_rows = (
        db.query(models.SkillVerification)
        .filter(
            models.SkillVerification.user_id == current_user.id,
        )
        .order_by(models.SkillVerification.attempted_at.desc())
        .all()
    )
    verification_by_skill = {}
    for verification in verification_rows:
        verification_by_skill.setdefault(verification.skill.casefold(), verification)

    skills_teach = current_user.skills_teach or []
    skill_verifications = []
    verified_skill_keys = set()
    for skill in skills_teach:
        if not isinstance(skill, str):
            continue
        verification = verification_by_skill.get(skill.casefold())
        score = verification.score if verification is not None else None
        is_verified = bool(
            verification is not None
            and verification.is_verified
            and score is not None
            and score >= 4
        )
        proficiency_label = (
            "Expert" if is_verified and score == 5
            else "Proficient" if is_verified and score == 4
            else "Not verified"
        )
        skill_verifications.append(
            {
                "skill": skill,
                "is_verified": is_verified,
                "score": score,
                "total_questions": 5,
                "proficiency_label": proficiency_label,
                "attempted_at": (
                    verification.attempted_at if verification is not None else None
                ),
            }
        )
        if is_verified:
            verified_skill_keys.add(skill.casefold())

    badges = get_badge_summaries_by_user_ids(db, [current_user.id]).get(
        current_user.id,
        [],
    )
    return {
        "headline": current_user.headline,
        "avatar_url": current_user.avatar_url,
        "skills_teach": skills_teach,
        "skills_learn": current_user.skills_learn or [],
        "rating": current_user.rating,
        "swaps_completed": current_user.swaps_completed,
        "badges": badges,
        "skill_verifications": skill_verifications,
        "verified_skills": [
            skill
            for skill in skills_teach
            if isinstance(skill, str) and skill.casefold() in verified_skill_keys
        ],
    }


@router.post("/upload-avatar", response_model=schemas.AvatarResponse)
def upload_avatar(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    extension = Path(file.filename or "").suffix.lower()
    filename = f"{uuid.uuid4().hex}{extension}"
    destination = UPLOAD_DIR / filename

    with destination.open("wb") as saved_file:
        shutil.copyfileobj(file.file, saved_file)

    avatar_url = f"/uploads/{filename}"
    current_user.avatar_url = avatar_url
    db.commit()
    db.refresh(current_user)
    return {"avatar_url": avatar_url}