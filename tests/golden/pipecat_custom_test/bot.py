import os

from cekura.pipecat import PipecatTracer
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService

PROMPT = "You are a survey caller. Confirm the respondent's zip code {{zip_code}} before starting."


async def run_bot(transport, runner_args):
    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"))
    tts = ElevenLabsTTSService(api_key=os.getenv("ELEVENLABS_API_KEY"))

    context = LLMContext(messages=[{"role": "system", "content": PROMPT}])
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
        agent_id=99,
    )
    pipeline = cekura_tracer.track_pipeline(
        pipeline, context, runner_args=runner_args,
    )
    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),
        idle_timeout_secs=30,
        enable_tracing=True,
        enable_turn_tracking=True,
    )
    task = cekura_tracer.register_task_handlers(task, transport=transport)

    runner = PipelineRunner()
    await runner.run(task)
