"""语音：/tts（一句话转语音）、/voice（麦克风音频进来，唤醒和识别结果出去）"""

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..deps import get_ears, get_voice, status
from ..security import LOCAL_ORIGIN
from ..voice import float_to_wav, pcm16_to_float

log = logging.getLogger("aila")
router = APIRouter()


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


@router.post("/tts")
async def tts(req: TtsRequest, voice: Any = Depends(get_voice)) -> Response:
    """一句话转语音。没有能念的内容（只有标点、网址）时返回 204"""
    if voice is None:
        raise HTTPException(status_code=503, detail=f"语音合成不可用：{status['tts']}")
    t0 = time.perf_counter()
    samples = await asyncio.to_thread(voice.synthesize, req.text, req.speed)
    if samples is None:
        return Response(status_code=204)
    ms = round((time.perf_counter() - t0) * 1000)
    return Response(float_to_wav(samples, voice.sample_rate), media_type="audio/wav", headers={"X-Synth-Ms": str(ms)})


@router.websocket("/voice")
async def voice_ws(ws: WebSocket, ears: Any = Depends(get_ears)) -> None:
    """二进制消息：16kHz 单声道 16 位 PCM。文本消息：{"type": "mode", "mode": "off|wake|listen"}

    浏览器里任何网页都能连 ws://127.0.0.1，所以只接受本机页面和桌面端（没有 Origin 或 file://）
    """
    origin = ws.headers.get("origin")
    if origin and not LOCAL_ORIGIN.match(origin):
        log.warning("拒绝了来自 %s 的语音连接", origin)
        await ws.close(code=1008)
        return
    await ws.accept()
    if ears is None:
        await ws.send_json({"type": "error", "message": f"语音识别不可用：{status['ears']}"})
        await ws.close()
        return

    listener = ears.new_listener()
    await ws.send_json({"type": "ready", "mode": listener.mode})
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                # 识别要一两百毫秒，放到线程里；同一条连接按顺序处理
                for event in await asyncio.to_thread(listener.feed, pcm16_to_float(msg["bytes"])):
                    await ws.send_json(event)
            elif msg.get("text"):
                cmd = _parse_command(msg["text"])
                if cmd.get("type") == "mode" and cmd.get("mode") in ("off", "wake", "listen"):
                    listener.set_mode(cmd["mode"])
                    await ws.send_json({"type": "mode", "mode": listener.mode})
    except WebSocketDisconnect:
        pass


def _parse_command(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
