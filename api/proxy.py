from fastapi import APIRouter, Depends

from auth import get_user_id
from adapters import get_adapter
from api.deps import check_agent, relay
from schemas.chat import CreateSessionRequest, RenameSessionRequest, FeedbackRequest

router = APIRouter(prefix="/agents/{agent_id}", tags=["proxy"])


@router.post("/sessions")
async def create_session(
    agent_id: str, payload: CreateSessionRequest,
    user_id: str = Depends(get_user_id)
):
    check_agent(agent_id)
    return relay(await get_adapter(agent_id).create_session(user_id, payload.title))


@router.get("/sessions")
async def list_sessions(
        agent_id: str,
        user_id: str = Depends(get_user_id)
):
    check_agent(agent_id)
    return relay(await get_adapter(agent_id).list_sessions(user_id))


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    agent_id: str, session_id: str,
    user_id: str = Depends(get_user_id)
):
    check_agent(agent_id)
    return relay(await get_adapter(agent_id).get_messages(user_id, session_id))


@router.patch("/sessions/{session_id}")
async def rename_session(
    agent_id: str, session_id: str, payload: RenameSessionRequest,
    user_id: str = Depends(get_user_id),
):
    check_agent(agent_id)
    return relay(await get_adapter(agent_id).rename_session(user_id, session_id, payload.title))


@router.delete("/sessions/{session_id}")
async def delete_session(
    agent_id: str, session_id: str,
    user_id: str = Depends(get_user_id)
):
    check_agent(agent_id)
    return relay(await get_adapter(agent_id).delete_session(user_id, session_id))


@router.post("/messages/{message_id}/feedback")
async def set_feedback(
    agent_id: str, message_id: str, payload: FeedbackRequest,
    user_id: str = Depends(get_user_id),
):
    check_agent(agent_id)
    return relay(await get_adapter(agent_id).set_feedback(user_id, message_id, payload.model_dump()))


@router.get("/messages/{message_id}/feedback")
async def get_feedback(
    agent_id: str, message_id: str,
    user_id: str = Depends(get_user_id)
):
    check_agent(agent_id)
    return relay(await get_adapter(agent_id).get_feedback(user_id, message_id))


@router.delete("/messages/{message_id}/feedback")
async def delete_feedback(
    agent_id: str, message_id: str,
    user_id: str = Depends(get_user_id)
):
    check_agent(agent_id)
    return relay(await get_adapter(agent_id).delete_feedback(user_id, message_id))
