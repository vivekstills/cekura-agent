import os

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from cekura.pipecat import PipecatTracer

SYSTEM_PROMPT = (
    "You are the front-desk assistant for Riverline Dental. "
    "The caller is {{caller_name}} and their patient id is {{patient_id}}. "
    "Use the pricing guide document when asked about costs."
)

order_lookup = FunctionSchema(
    name="order_lookup",
    description="Look up a patient's open invoice by patient id.",
    properties={"patient_id": {"type": "string", "description": "Patient identifier"}},
    required=["patient_id"],
)


async def handle_order_lookup(params):
    await params.result_callback({"invoice": "INV-1009", "amount_due": 120.5})


async def run_bot(transport, runner_args):
    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"))
    tts = CartesiaTTSService(api_key=os.getenv("CARTESIA_API_KEY"))
    llm.register_function("order_lookup", handle_order_lookup)

    context = LLMContext(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}],
        tools=ToolsSchema(standard_tools=[order_lookup]),
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    # Cekura tracing (added by cekura-agent) — per-call tracer: not thread-safe to share
    cekura_tracer = PipecatTracer(
        api_key=os.getenv("CEKURA_API_KEY"),
        agent_id=88,
    )
    task = cekura_tracer.track_and_create_task(
        pipeline, context, runner_args=runner_args, transport=transport,
    )

    runner = PipelineRunner()
    await runner.run(task)
