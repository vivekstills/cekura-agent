from livekit.agents import AgentSession, JobContext, WorkerOptions, cli


async def entrypoint(ctx: JobContext):
    session = AgentSession()
    billing_reporter = BillingReporter()
    await billing_reporter.start()
    await session.start(room=ctx.room, agent=None)


class BillingReporter:
    async def start(self):
        pass


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
