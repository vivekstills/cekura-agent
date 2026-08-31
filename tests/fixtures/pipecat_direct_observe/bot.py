import httpx
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair

OBSERVE_URL = "https://api.cekura.ai/observability/v1/observe/"


async def run_bot(transport, runner_args):
    context = LLMContext(messages=[])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)
    pipeline = Pipeline(
        [transport.input(), user_aggregator, transport.output(), assistant_aggregator]
    )
    task = PipelineTask(pipeline)


async def push_call_log(payload):
    async with httpx.AsyncClient() as client:
        await client.post(OBSERVE_URL, json=payload)
