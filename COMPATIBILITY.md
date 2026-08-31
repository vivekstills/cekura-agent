# Compatibility matrix

## Supported (v1)
| Topology | Detection | Integration |
|---|---|---|
| LiveKit Python, one deployable entrypoint with `AgentSession` + `session.start(...)` (awaited, `asyncio.create_task`-wrapped, or plain) | `JobContext` annotation / `WorkerOptions(entrypoint_fnc=...)` / `@*.rtc_session` | module-scope `LiveKitTracer` + `track_session`/`observe_session` before start |
| Pipecat Python, one `PipelineTask(...)` with `LLMContextAggregatorPair` (or `create_context_aggregator` + `.user()/.assistant()`) in the pipeline | `Pipeline([...])` + `PipelineTask(...)` construction | single-step `track/observe_and_create_task`, or multi-step wrap preserving custom kwargs |
| Already-integrated repos | `cekura` imports / tracer calls (incl. `*_and_create_task`) | no-op plan, zero diff |

## Refused with stable reason codes (exit 2)
| Code | Meaning |
|---|---|
| `NO_FRAMEWORK` | no livekit/pipecat imports in code (README text is ignored) |
| `MULTI_FRAMEWORK` | both frameworks present; target must be specified |
| `NO_ENTRYPOINT` / `AMBIGUOUS_ENTRYPOINT` | zero or multiple deployable entrypoints (tests/examples/scripts paths are auto-rejected candidates) |
| `NO_SESSION_START` | LiveKit entrypoint without a reachable `session.start` |
| `NO_PIPELINE_TASK` | pipecat imported but no task construction |
| `PIPECAT_WORKER_API` | newer `PipelineWorker`/`WorkerRunner` API; documented SDK contract targets `PipelineTask` |
| `MISSING_AGGREGATOR_PAIR` | SDK would silently disable without the aggregator pair |
| `DIRECT_OBSERVE_PRESENT` | repo already POSTs `/observability/v1/observe/`; SDK + direct API duplicates records — migration decision required |

## Blockers (implemented, waiting on access; exit 0 with status)
| Code | Meaning |
|---|---|
| `CEKURA_KEY_MISSING` / `CEKURA_UNAUTHORIZED` | staging platform calls need a valid `CEKURA_API_KEY` |
| `PROVIDER_CONNECTION_REQUIRED` | platform requires provider creds (e.g. `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`) or a connection method to create the agent |
| `PROJECT_REQUIRED` | multiple projects visible; pass `--project-id` |
| `OPENROUTER_KEY_MISSING` / `OPENROUTER_UNAUTHORIZED` | live planning needs `OPENROUTER_API_KEY` |
| `PIPECAT_MOCK_ROUTING_UNCONFIGURED` | pipecat mock routing requested without an explicit endpoint contract |

## Real-repo classification (2026-08-31, read-only; full table in eval/matrix.md)
- outbound-caller-python → SUPPORTED (5 tools, create_task-wrapped start)
- telephony-server → SUPPORTED (single pipecat pipeline bridge)
- QuickVoice, AIReceptionist → AMBIGUOUS_ENTRYPOINT (multiple worker entrypoints)
- pipecat-examples subprojects → PIPECAT_WORKER_API / MISSING_AGGREGATOR_PAIR / AMBIGUOUS
- NVIDIA voice-agent-examples subprojects → MISSING_AGGREGATOR_PAIR / AMBIGUOUS

Out of scope v1: LiveKit JS/TS (`@cekura/livekit` exists; Python-only per trial scope),
scenario-run E2E automation (statuses stay `NOT_RUN`, never faked).
