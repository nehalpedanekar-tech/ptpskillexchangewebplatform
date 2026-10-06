from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from routes.auth import get_current_user

router = APIRouter(tags=["messages"])
_NO_ACCEPTED_SWAP = "You can only message peers you have an accepted swap with."


def _has_accepted_swap(db: Session, first_user_id: int, second_user_id: int) -> bool:
    accepted_swap = (
        db.query(models.SwapRequest.id)
        .filter(
            models.SwapRequest.status == "accepted",
            or_(
                and_(
                    models.SwapRequest.from_user_id == first_user_id,
                    models.SwapRequest.to_user_id == second_user_id,
                ),
                and_(
                    models.SwapRequest.from_user_id == second_user_id,
                    models.SwapRequest.to_user_id == first_user_id,
                ),
            ),
        )
        .first()
    )
    return accepted_swap is not None


def _require_accepted_swap(db: Session, first_user_id: int, second_user_id: int) -> None:
    if not _has_accepted_swap(db, first_user_id, second_user_id):
        raise HTTPException(status_code=403, detail=_NO_ACCEPTED_SWAP)


def _other_user_id_filter(first_user_id: int, second_user_id: int):
    return or_(
        and_(
            models.Message.sender_id == first_user_id,
            models.Message.receiver_id == second_user_id,
        ),
        and_(
            models.Message.sender_id == second_user_id,
            models.Message.receiver_id == first_user_id,
        ),
    )


@router.post(
    "/messages",
    response_model=schemas.MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def send_message(
    request: schemas.MessageCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Message content cannot be empty")
    if request.receiver_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot message yourself")

    receiver = db.query(models.User).filter(models.User.id == request.receiver_id).first()
    if receiver is None:
        raise HTTPException(status_code=404, detail="Message recipient not found")
    _require_accepted_swap(db, current_user.id, receiver.id)

    message = models.Message(
        sender_id=current_user.id,
        receiver_id=receiver.id,
        content=content,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return {
        "id": message.id,
        "sender_id": message.sender_id,
        "sender_name": current_user.name,
        "receiver_id": message.receiver_id,
        "content": message.content,
        "created_at": message.created_at,
        "is_read": message.is_read,
    }


@router.get(
    "/messages/{other_user_id}",
    response_model=list[schemas.MessageResponse],
)
def get_message_conversation(
    other_user_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if other_user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot open a conversation with yourself")
    other_user = db.query(models.User).filter(models.User.id == other_user_id).first()
    if other_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    _require_accepted_swap(db, current_user.id, other_user_id)

    sender = db.query(models.User).subquery()
    messages = (
        db.query(models.Message, sender.c.name.label("sender_name"))
        .join(sender, sender.c.id == models.Message.sender_id)
        .filter(_other_user_id_filter(current_user.id, other_user_id))
        .order_by(models.Message.created_at.asc(), models.Message.id.asc())
        .all()
    )
    has_unread_messages = False
    response = []
    for message, sender_name in messages:
        if message.receiver_id == current_user.id and not message.is_read:
            message.is_read = True
            has_unread_messages = True
        response.append(
            {
                "id": message.id,
                "sender_id": message.sender_id,
                "sender_name": sender_name,
                "receiver_id": message.receiver_id,
                "content": message.content,
                "created_at": message.created_at,
                "is_read": message.is_read,
            }
        )
    if has_unread_messages:
        db.commit()
    return response


@router.get("/conversations", response_model=list[schemas.ConversationResponse])
def get_conversations(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    accepted_swaps = (
        db.query(models.SwapRequest.from_user_id, models.SwapRequest.to_user_id)
        .filter(
            models.SwapRequest.status == "accepted",
            or_(
                models.SwapRequest.from_user_id == current_user.id,
                models.SwapRequest.to_user_id == current_user.id,
            ),
        )
        .all()
    )
    accepted_peer_ids = {
        from_user_id if to_user_id == current_user.id else to_user_id
        for from_user_id, to_user_id in accepted_swaps
    }
    if not accepted_peer_ids:
        return []

    other_user_id = case(
        (models.Message.sender_id == current_user.id, models.Message.receiver_id),
        else_=models.Message.sender_id,
    )
    conversation_filter = or_(
        models.Message.sender_id == current_user.id,
        models.Message.receiver_id == current_user.id,
    )
    ranked_messages = (
        db.query(
            models.Message.id.label("message_id"),
            other_user_id.label("other_user_id"),
            func.row_number()
            .over(
                partition_by=other_user_id,
                order_by=(models.Message.created_at.desc(), models.Message.id.desc()),
            )
            .label("message_rank"),
        )
        .filter(conversation_filter, other_user_id.in_(accepted_peer_ids))
        .subquery()
    )
    latest_messages = (
        db.query(
            models.Message,
            models.User.id.label("other_user_id"),
            models.User.name.label("other_user_name"),
        )
        .join(ranked_messages, ranked_messages.c.message_id == models.Message.id)
        .join(models.User, models.User.id == ranked_messages.c.other_user_id)
        .filter(ranked_messages.c.message_rank == 1)
        .all()
    )
    unread_counts = dict(
        db.query(models.Message.sender_id, func.count(models.Message.id))
        .filter(
            models.Message.receiver_id == current_user.id,
            models.Message.sender_id.in_(accepted_peer_ids),
            models.Message.is_read.is_(False),
        )
        .group_by(models.Message.sender_id)
        .all()
    )

    conversations = [
        {
            "other_user_id": other_id,
            "other_user_name": other_name,
            "last_message_content": message.content,
            "last_message_at": message.created_at,
            "unread_count": unread_counts.get(other_id, 0),
        }
        for message, other_id, other_name in latest_messages
    ]
    return sorted(conversations, key=lambda conversation: conversation["last_message_at"], reverse=True)