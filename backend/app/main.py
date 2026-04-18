import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .ws_handler import router as ws_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="transmeet-ws")

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
