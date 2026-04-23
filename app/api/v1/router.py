from fastapi import APIRouter
from app.api.v1.endpoints.sync import router as sync_router
from app.api.v1.endpoints.data import router as data_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(sync_router)
api_router.include_router(data_router)
