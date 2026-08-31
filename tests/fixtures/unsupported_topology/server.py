"""Telephony bridge: forwards provider websockets to worker processes. No pipeline here."""
import pipecat  # noqa: F401
from fastapi import FastAPI, WebSocket

app = FastAPI()


@app.websocket("/media")
async def media(ws: WebSocket):
    await ws.accept()
    while True:
        await ws.receive_bytes()
