from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.auth.dependencies import get_current_agent
from app.models.agent import Agent
from app.schemas.run import RunRequest, RunResponse
from app.services.execution_service import run_agent

router = APIRouter(
    prefix="/runs",
    tags=["Runs"],
)


@router.post(
    "",
    response_model=RunResponse,
    summary="Execute an agent run",
)
async def execute_run(
    payload: RunRequest,
    current_agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    return await run_agent(
        agent_orm=current_agent,
        payload=payload,
        db=db,
    )