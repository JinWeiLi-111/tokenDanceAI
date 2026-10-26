from fastapi import APIRouter

router = APIRouter()


@router.post("")
async def agent_run():
    pass


@router.get("/sessions")
async def sessions() -> list:
    pass
