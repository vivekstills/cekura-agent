# Compatibility matrix

## Supported (v1)
| Topology | Detection | Integration |
|---|---|---|
| LiveKit Python, one deployable entrypoint with `AgentSession` + `session.start(...)` (awaited, `asyncio.create_task`-wrapped, or plain) | `JobContext` annotation / `WorkerOptions(entrypoint_fnc=...)` / `@*.rtc_session`; helpers that take `JobContext` but do not call `session.start()` are ignored | module-scope `LiveKitTracer` + `track_session`/`observe_session` before start |
| Pipecat Python, one `PipelineTask(...)` with `LLMContextAggregatorPair` (or `create_context_aggregator` + `.user()/.assistant()`) in the pipeline | `Pipeline([...])` + `PipelineTask(...)` construction | single-step `track/observe_and_create_task`, or multi-step wrap preserving custom kwargs |
| Already-integrated repos | `cekura` imports / tracer calls (incl. `*_and_create_task`) | no-op plan, zero diff |

## Refused with stable reason codes (exit 2)
| Code | Meaning |
|---|---|
| `NO_FRAMEWORK` | no livekit/pipecat imports in code (README text is ignored) |
| `MULTI_FRAMEWORK` | both frameworks present; target must be specified |
| `NO_ENTRYPOINT` / `AMBIGUOUS_ENTRYPOINT` | zero or multiple deployable entrypoints (tests/examples/scripts/client paths are auto-rejected candidates) |
| `NO_SESSION_START` | LiveKit entrypoint without a reachable `session.start()` |
| `NO_PIPELINE_TASK` | pipecat imported but no task construction |
| `PIPECAT_WORKER_API` | newer `PipelineWorker`/`WorkerRunner` API; documented SDK contract targets `PipelineTask` |
| `MISSING_AGGREGATOR_PAIR` | SDK would silently disable without the aggregator pair |
| `DIRECT_OBSERVE_PRESENT` | repo already POSTs `/observability/v1/observe/` directly; SDK + direct API duplicates records — migration decision required |

## Blockers (implemented, waiting on access; exit 0 with status)
| Code | Meaning |
|---|---|
| `CEKURA_KEY_MISSING` / `CEKURA_UNAUTHORIZED` | staging platform calls need a valid `CEKURA_API_KEY` |
| `PROVIDER_CONNECTION_REQUIRED` | platform requires provider creds (e.g. `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`) or a connection method to create the agent |
| `PROJECT_REQUIRED` | multiple projects visible; pass `--project-id` |
| `OPENROUTER_KEY_MISSING` / `OPENROUTER_UNAUTHORIZED` | live planning needs `OPENROUTER_API_KEY` |

## CEK-8066 top picks — existence, execution, test outcome (2026-08-31; full table in eval/matrix.md)
| Top pick | Executed | Outcome |
|---|---|---|
| QuickVoice | full offline E2E on a copy | SUPPORTED — 15/15 checks, no-op re-apply, exact rollback; tracer inserted before `session.start()` in `apps/ai/main.py`; 5 mock tools, 19 dynamic variables, 21 KB candidates detected |
| AIReceptionist | full offline E2E on a copy | SUPPORTED — 15/15 checks, no-op re-apply, exact rollback; tracer inserted before `session.start()` in `receptionist/agent.py`; 11 mock tools, 16 dynamic variables detected |
| outbound-caller-python | full offline E2E on a copy **+ live real-Kimi apply** | SUPPORTED — 15/15 checks, no-op re-apply, exact rollback; tracer before `asyncio.create_task(session.start(...))`; 5 mock tools + vars prepared (platform blocked on LiveKit creds) |
| telephony-server | full offline E2E on a copy | SUPPORTED — 15/15 checks on the older `OpenAILLMContext`/`create_context_aggregator` API via the multi-step adapter; no-op re-apply; exact rollback |
| pipecat-examples (Twilio phone bot pinned) | integrate executed on root + `twilio-chatbot/inbound` + `twilio-chatbot/outbound` | outbound → exit 2 `PIPECAT_WORKER_API` (new `PipelineWorker` runner API — documented SDK contract targets `PipelineTask`); inbound → exit 2 `AMBIGUOUS_ENTRYPOINT` (server `bot.py` + `client/python/client.py`); other subprojects: `PIPECAT_WORKER_API` / `MISSING_AGGREGATOR_PAIR` |
| NVIDIA voice-agent-examples | integrate executed on root + 3 subprojects | exit 2 `NO_ENTRYPOINT` root; subprojects `MISSING_AGGREGATOR_PAIR` (SDK would silently disable) / `AMBIGUOUS_ENTRYPOINT` (`bot.py` + `bot_websocket.py` in `nat_agent`) |

Every refusal is a structured exit-2 with a stable reason code — executed and tested, never skipped.

Out of scope v1: LiveKit JS/TS (`@cekura/livekit` exists; Python-only per trial scope),
`PipelineWorker`/`WorkerRunner` Pipecat runner API, scenario-run E2E automation (statuses stay `NOT_RUN`, never faked).
