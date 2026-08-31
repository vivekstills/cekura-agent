from livekit.agents import AgentSession, JobContext


async def entrypoint(ctx: JobContext):
    session = AgentSession()
    await session.start(room=ctx.room)
