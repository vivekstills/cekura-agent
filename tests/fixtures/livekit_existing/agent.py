import os

from cekura.livekit import LiveKitTracer
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli

cekura_tracer = LiveKitTracer(api_key=os.getenv("CEKURA_API_KEY"), agent_id=42)


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="You are a helpful agent.")


async def entrypoint(ctx: JobContext):
    await ctx.connect()
    assistant = Assistant()
    session = AgentSession()
    await cekura_tracer.track_session(ctx, session, assistant)
    await session.start(room=ctx.room, agent=assistant)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="existing"))
