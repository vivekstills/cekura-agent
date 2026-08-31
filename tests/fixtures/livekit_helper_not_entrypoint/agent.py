from livekit.agents import AgentSession, JobContext, WorkerOptions, cli


async def load_business_config(ctx: JobContext):
    """Helper that uses JobContext but is not the entrypoint."""
    return {"config": ctx.job.metadata}


async def entrypoint(ctx: JobContext):
    session = AgentSession()
    await session.start(room=ctx.room, agent=None)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
