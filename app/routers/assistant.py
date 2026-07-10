from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import User
from app.routers.campaigns import org_to_memory
from app.routers.organizations import get_owned_org
from app.schemas import AssistantAskIn, AssistantAskOut
from app.services.assistant import AssistantService

router = APIRouter(prefix="/organizations/{org_id}/assistant", tags=["assistant"])
assistant = AssistantService()


@router.post("/ask", response_model=AssistantAskOut)
def ask_assistant(
    org_id: int,
    payload: AssistantAskIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org = get_owned_org(org_id, db, user)
    answer = assistant.ask(org_to_memory(org), payload.question)
    return {"question": payload.question, "answer": answer}
