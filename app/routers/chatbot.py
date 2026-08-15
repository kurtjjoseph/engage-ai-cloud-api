"""Website chatbot replies for the Engage AI plugin's Chatbot module.

One endpoint. The site retrieves from its own Site Brain, posts the grounding,
and gets the assistant's next turn back - so a customer never needs an AI
account of their own, and the answer protocol stays server-side."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import User
from app.routers.organizations import get_owned_org
from app.schemas import ChatbotReplyIn, ChatbotReplyOut
from app.services.chatbot import ChatbotService

router = APIRouter(prefix="/organizations/{org_id}/chatbot", tags=["chatbot"])
chatbot = ChatbotService()


@router.post("/reply", response_model=ChatbotReplyOut)
def chatbot_reply(
    org_id: int,
    payload: ChatbotReplyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_owned_org(org_id, db, user)
    reply = chatbot.reply(payload.grounding, payload.messages, payload.language)
    return {"reply": reply}
