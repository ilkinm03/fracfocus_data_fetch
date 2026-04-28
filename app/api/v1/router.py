from fastapi import APIRouter
from app.api.v1.endpoints.fracfocus_sync import router as sync_router
from app.api.v1.endpoints.fracfocus import router as fracfocus_router
from app.api.v1.endpoints.seismic import router as seismic_router
from app.api.v1.endpoints.iris import router as iris_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(sync_router)
api_router.include_router(fracfocus_router)
api_router.include_router(seismic_router)
api_router.include_router(iris_router)
