from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from auth import require_admin
from registry_service import (AgentNotFound, RegistryConflict,
                              RegistryValidationError, agent_registry)
from schemas.agents import (AgentCreate, AgentMutationRead, AgentRead,
                            AgentUpdate, AppliedRead, RegistryApplyRead,
                            RegistryStatusRead)

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])


def _applied(applied) -> AppliedRead:
    return AppliedRead(**vars(applied))


def _mutation(agent, applied) -> AgentMutationRead:
    return AgentMutationRead(agent=AgentRead.from_info(agent),
                             applied=_applied(applied))


@router.get("/agents", response_model=list[AgentRead])
async def list_agents():
    """Отдаёт весь реестр как есть, включая выключенных агентов."""
    return [AgentRead.from_info(a) for a in agent_registry.all()]


@router.get("/agents/{agent_id}", response_model=AgentRead)
async def get_agent(agent_id: str):
    try:
        return AgentRead.from_info(agent_registry.get(agent_id))
    except AgentNotFound:
        raise HTTPException(404, f"Агент {agent_id} не найден")


@router.post("/agents", response_model=AgentMutationRead, status_code=201)
async def create_agent(payload: AgentCreate):
    try:
        agent, applied = await agent_registry.create(payload)
    except RegistryConflict as e:
        raise HTTPException(409, str(e))
    except RegistryValidationError as e:
        raise HTTPException(400, str(e))
    return _mutation(agent, applied)


@router.patch("/agents/{agent_id}", response_model=AgentMutationRead)
async def update_agent(agent_id: str, payload: AgentUpdate):
    try:
        agent, applied = await agent_registry.update(agent_id, payload)
    except AgentNotFound:
        raise HTTPException(404, f"Агент {agent_id} не найден")
    except RegistryConflict as e:
        raise HTTPException(409, str(e))
    except RegistryValidationError as e:
        raise HTTPException(400, str(e))
    return _mutation(agent, applied)


@router.delete("/agents/{agent_id}", response_model=RegistryApplyRead)
async def delete_agent(agent_id: str):
    """Возвращает 200 с телом `applied`, а не 204: без него не видно, что
    именно применилось — вектор снят с индекса, адаптер выброшен из кэша."""
    try:
        applied = await agent_registry.delete(agent_id)
    except AgentNotFound:
        raise HTTPException(404, f"Агент {agent_id} не найден")
    except RegistryConflict as e:
        raise HTTPException(409, str(e))
    return RegistryApplyRead(applied=_applied(applied))


@router.post("/registry/reload", response_model=RegistryApplyRead)
async def reload_registry():
    try:
        applied = await agent_registry.reload()
    except RegistryValidationError as e:
        raise HTTPException(400, f"Файл реестра не принят: {e}")
    except RegistryConflict as e:
        raise HTTPException(409, str(e))
    return RegistryApplyRead(applied=_applied(applied))


@router.get("/registry", response_model=RegistryStatusRead)
async def registry_status():
    return RegistryStatusRead(**agent_registry.status())
