import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .ws_handler import flush_all_audio, router as ws_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # 배포(force-new-deployment)로 태스크가 SIGTERM 을 받으면 아직 안 올린 녹음을 S3 에 올리고 끝낸다
    await flush_all_audio()


app = FastAPI(title="transmeet-ws", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"service": "transmeet-ws"}


app.include_router(ws_router)
