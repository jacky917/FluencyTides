from fastapi import APIRouter

router = APIRouter()

@router.get("/health", summary="Health Check")
async def health_check() -> dict[str, str]:
    """
    Check if the API is running correctly.
    """
    return {"status": "ok"}
