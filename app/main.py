from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()  # fails fast if SECRET_KEY is missing
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Small Cloud", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
