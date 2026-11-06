"""Chat endpoints: POST /api/chat, GET /api/chat/history (PLAN §8, §10)."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.llm import service

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post("")
async def post_chat(body: ChatRequest):
    message = body.message.strip()
    if not message:
        return JSONResponse(
            {"error": "Message must not be empty", "code": "VALIDATION_ERROR"}, status_code=400
        )
    try:
        return await service.handle_chat(message)
    except service.ChatError as exc:
        return JSONResponse({"error": exc.message, "code": exc.code}, status_code=exc.status)


@router.get("/history")
async def get_chat_history():
    return {"messages": service.get_history(50)}
