from fastapi import APIRouter, Depends, HTTPException, Request
from sentence_transformers import util
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from routes.badges import get_badge_summaries_by_user_ids
from routes.auth import get_current_user

router = APIRouter(prefix="/peers", tags=["peers"])
SIMILARITY_THRESHOLD = 0.5


def _clean_skills(skills):
    if not isinstance(skills, list):
        return []
    return [skill.strip() for skill in skills if isinstance(skill, str) and skill.strip()]


@router.get("/suggestions", response_model=list[schemas.PeerSuggestionResponse])
def get_peer_suggestions(
    request: Request,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    embedding_model = getattr(request.app.state, "embedding_model", None)
    if embedding_model is None:
        raise HTTPException(status_code=503, detail="Skill matching model is not ready")

    current_skills_learn = _clean_skills(current_user.skills_learn)

    # Query every user except the logged-in user.
    candidates = (
        db.query(models.User)
        .filter(models.User.id != current_user.id)
        .all()
    )

    candidate_skills = [
        (candidate, _clean_skills(candidate.skills_teach))
        for candidate in candidates
    ]
    candidate_ids = [candidate.id for candidate, _ in candidate_skills]
    badges_by_user = get_badge_summaries_by_user_ids(db, candidate_ids)
    verified_skills_by_user = {}
    proficiency_by_user = {}
    verification_by_user = {}
    if candidate_ids:
        verified_skill_rows = (
            db.query(
                models.SkillVerification.user_id,
                models.SkillVerification.skill,
                models.SkillVerification.score,
                models.SkillVerification.is_verified,
            )
            .filter(
                models.SkillVerification.user_id.in_(candidate_ids),
            )
            .order_by(models.SkillVerification.attempted_at.desc())
            .all()
        )
        for user_id, skill, score, stored_is_verified in verified_skill_rows:
            normalized_skill = skill.casefold()
            verification = verification_by_user.setdefault(user_id, {}).setdefault(
                normalized_skill,
                {
                    "score": score,
                    "is_verified": bool(stored_is_verified and score is not None and score >= 4),
                },
            )
            if not verification["is_verified"]:
                continue
            verified_skills_by_user.setdefault(user_id, set()).add(normalized_skill)
            proficiency_by_user.setdefault(user_id, {})[normalized_skill] = {
                "score": verification["score"],
                "proficiency_level": "Expert" if verification["score"] == 5 else "Proficient",
            }

    skill_texts = {}
    for skill in current_skills_learn:
        skill_texts.setdefault(skill.casefold(), skill)
    for _, taught_skills in candidate_skills:
        for skill in taught_skills:
            skill_texts.setdefault(skill.casefold(), skill)

    embedding_cache = {}
    if skill_texts:
        skill_keys = list(skill_texts)
        skill_embeddings = embedding_model.encode(
            [skill_texts[key] for key in skill_keys],
            convert_to_tensor=True,
        )
        embedding_cache = dict(zip(skill_keys, skill_embeddings))

    suggestions = []
    for candidate, candidate_skills_teach in candidate_skills:
        matched_skills = []
        highest_score = 0.0

        for my_skill in current_skills_learn:
            normalized_my_skill = my_skill.casefold()
            for their_skill in candidate_skills_teach:
                normalized_their_skill = their_skill.casefold()
                if normalized_my_skill == normalized_their_skill:
                    score = 1.0
                else:
                    score = float(
                        util.cos_sim(
                            embedding_cache[normalized_my_skill],
                            embedding_cache[normalized_their_skill],
                        ).item()
                    )

                print(
                    f"[peer suggestions] {my_skill!r} vs {their_skill!r}: "
                    f"cosine similarity={score:.4f}"
                )

                if score > SIMILARITY_THRESHOLD:
                    verification = verification_by_user.get(candidate.id, {}).get(
                        normalized_their_skill
                    )
                    verification_score = verification["score"] if verification else None
                    is_verified = bool(verification and verification["is_verified"])
                    proficiency_label = (
                        "Expert" if is_verified and verification_score == 5
                        else "Proficient" if is_verified and verification_score == 4
                        else "Not verified" if verification is not None
                        else "Not verified yet"
                    )
                    matched_skills.append(
                        {
                            "my_skill": my_skill,
                            "their_skill": their_skill,
                            "score": round(score, 4),
                            "verification": {
                                "is_verified": is_verified,
                                "score": verification_score,
                                "proficiency_label": proficiency_label,
                            },
                        }
                    )
                    highest_score = max(highest_score, score)

        if not matched_skills:
            continue

        suggestions.append(
            {
                "id": candidate.id,
                "name": candidate.name,
                "badges": badges_by_user.get(candidate.id, []),
                "verified_skills": [
                    skill
                    for skill in candidate_skills_teach
                    if skill.casefold() in verified_skills_by_user.get(candidate.id, set())
                ],
                "skill_proficiency": [
                    {
                        "skill": skill,
                        **proficiency_by_user[candidate.id][skill.casefold()],
                    }
                    for skill in candidate_skills_teach
                    if skill.casefold() in proficiency_by_user.get(candidate.id, {})
                ],
                "matching_skills": sorted(
                    matched_skills,
                    key=lambda match: match["score"],
                    reverse=True,
                ),
                "_highest_score": highest_score,
            }
        )

    suggestions.sort(key=lambda suggestion: (-suggestion["_highest_score"], suggestion["id"]))
    for suggestion in suggestions:
        del suggestion["_highest_score"]
    return suggestions