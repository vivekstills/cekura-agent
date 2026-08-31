import json
import logging

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import deepgram, openai, silero
import os
from cekura.livekit import LiveKitTracer

# Cekura tracing (added by cekura-agent)
cekura_tracer = LiveKitTracer(
    api_key=os.getenv("CEKURA_API_KEY"),
    agent_id=77,
)

logger = logging.getLogger("acme-scheduler")

INSTRUCTIONS = """You are a scheduling assistant for Acme Health.
You are speaking with {{customer_name}} (account {{account_id}}).
Help them manage their upcoming appointment on {{appointment_date}}.
Answer insurance questions using the clinic FAQ document.
"""


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    @function_tool()
    async def lookup_availability(self, ctx: RunContext, date: str):
        """Look up free appointment slots on a date.

        Args:
            date: ISO date to check, e.g. 2026-09-01
        """
        return {"slots": ["10:00", "14:30"]}

    @function_tool()
    async def confirm_appointment(self, ctx: RunContext, date: str, time: str, notes: str = ""):
        """Confirm an appointment slot for the caller."""
        return {"status": "confirmed", "date": date, "time": time}


def load_faq() -> str:
    with open("docs/faq.md") as fh:
        return fh.read()


async def entrypoint(ctx: JobContext):
    await ctx.connect()
    dial_info = json.loads(ctx.job.metadata or "{}")
    phone_number = dial_info.get("phone_number")
    logger.info("dialing %s", phone_number)

    assistant = Assistant()
    session = AgentSession(
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(voice="alloy"),
        vad=silero.VAD.load(),
    )

    # Cekura: must be called before session.start()
    await cekura_tracer.track_session(ctx, session, assistant)

    await session.start(room=ctx.room, agent=assistant)


if __name__ == "__main__":
    load_dotenv()
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="acme-scheduler"))
