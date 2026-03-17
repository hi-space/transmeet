from typing import Literal, Optional
from pydantic import BaseModel


class SubtitleStreamMessage(BaseModel):
    type: Literal["subtitle_stream"] = "subtitle_stream"
    messageId: str
    phase: Literal["stt", "translating", "done"]
    speaker: Optional[str] = None
    timestamp: str
    originalText: Optional[str] = None
    partialTranslation: Optional[str] = None
    translatedText: Optional[str] = None
    detectedLanguage: Optional[Literal["ko", "en"]] = None


class PongMessage(BaseModel):
    type: Literal["pong"] = "pong"
    timestamp: str


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str
    timestamp: str
