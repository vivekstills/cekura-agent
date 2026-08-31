from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask


async def run_bot(transport, runner_args):
    pipeline = Pipeline([transport.input(), transport.output()])
    task = PipelineTask(pipeline)
