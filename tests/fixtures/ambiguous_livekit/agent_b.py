from livekit.agents import AgentSession, JobContext


async def entrypoint_b(ctx: JobContext):
    session = AgentSession()
    await session.start(room=ctx.room)
