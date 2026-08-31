# cekura-agent evaluation matrix

| target | status | framework | reason | entrypoints | tools | dynamic_variables | kb_files | integrate_probe |
|---|---|---|---|---|---|---|---|---|
| fixture:livekit_basic | OFFLINE_E2E_PASS | livekit |  |  |  |  |  |  |
| fixture:pipecat_single | OFFLINE_E2E_PASS | pipecat |  |  |  |  |  |  |
| fixture:pipecat_custom | OFFLINE_E2E_PASS | pipecat |  |  |  |  |  |  |
| fixture:readme_only (refusal) | NEEDS_HUMAN |  | NO_FRAMEWORK |  |  |  |  | exit 2 |
| quickvoice | NEEDS_HUMAN | livekit | AMBIGUOUS_ENTRYPOINT | 2 | 5 | 19 | 21 | exit 2 (AMBIGUOUS_ENTRYPOINT) |
| quickvoice/apps/ai | NEEDS_HUMAN | livekit | AMBIGUOUS_ENTRYPOINT | 2 | 5 | 19 | 0 | exit 2 (AMBIGUOUS_ENTRYPOINT) |
| aireceptionist | NEEDS_HUMAN | livekit | AMBIGUOUS_ENTRYPOINT | 6 | 11 | 16 | 0 | exit 2 (AMBIGUOUS_ENTRYPOINT) |
| aireceptionist/receptionist | NEEDS_HUMAN | livekit | AMBIGUOUS_ENTRYPOINT | 6 | 11 | 7 | 0 | exit 2 (AMBIGUOUS_ENTRYPOINT) |
| outbound-caller-python | SUPPORTED | livekit | OK | 1 | 5 | 2 | 0 | OFFLINE_E2E_PASS (checks 15 passed/0 failed, no-op re-apply=True, rollback=exact) |
| pipecat-examples | NEEDS_HUMAN | pipecat | AMBIGUOUS_ENTRYPOINT | 68 | 2 | 6 | 0 | exit 2 (AMBIGUOUS_ENTRYPOINT) |
| pipecat-examples/twilio-chatbot/inbound | NEEDS_HUMAN | pipecat | AMBIGUOUS_ENTRYPOINT | 2 | 0 | 0 | 0 | exit 2 (AMBIGUOUS_ENTRYPOINT) |
| pipecat-examples/twilio-chatbot/outbound | NEEDS_HUMAN | pipecat | PIPECAT_WORKER_API | 1 | 0 | 0 | 0 | exit 2 (PIPECAT_WORKER_API) |
| pipecat-examples/audio-recording-s3-multipart-upload | NEEDS_HUMAN | pipecat | PIPECAT_WORKER_API | 1 | 0 | 0 | 0 | exit 2 (PIPECAT_WORKER_API) |
| pipecat-examples/aws-agentcore | NEEDS_HUMAN | pipecat | PIPECAT_WORKER_API | 1 | 0 | 0 | 0 | exit 2 (PIPECAT_WORKER_API) |
| pipecat-examples/aws-strands | NEEDS_HUMAN | pipecat | AMBIGUOUS_ENTRYPOINT | 2 | 0 | 0 | 0 | exit 2 (AMBIGUOUS_ENTRYPOINT) |
| pipecat-examples/bot-ready-signalling/server | NEEDS_HUMAN | pipecat | MISSING_AGGREGATOR_PAIR | 1 | 0 | 0 | 0 | exit 2 (MISSING_AGGREGATOR_PAIR) |
| pipecat-examples/code-helper/server | NEEDS_HUMAN | pipecat | PIPECAT_WORKER_API | 1 | 0 | 0 | 0 | exit 2 (PIPECAT_WORKER_API) |
| nvidia-voice-agent-examples | NEEDS_HUMAN | pipecat | NO_ENTRYPOINT | 0 | 0 | 0 | 5 | exit 2 (NO_ENTRYPOINT) |
| nvidia-voice-agent-examples/examples/nat_agent | NEEDS_HUMAN | pipecat | AMBIGUOUS_ENTRYPOINT | 2 | 0 | 0 | 0 | exit 2 (AMBIGUOUS_ENTRYPOINT) |
| nvidia-voice-agent-examples/examples/voice_agent_webrtc | NEEDS_HUMAN | pipecat | MISSING_AGGREGATOR_PAIR | 1 | 0 | 0 | 0 | exit 2 (MISSING_AGGREGATOR_PAIR) |
| nvidia-voice-agent-examples/examples/voice_agent_websocket | NEEDS_HUMAN | pipecat | MISSING_AGGREGATOR_PAIR | 1 | 0 | 0 | 0 | exit 2 (MISSING_AGGREGATOR_PAIR) |
| telephony-server | SUPPORTED | pipecat | OK | 1 | 0 | 0 | 0 | OFFLINE_E2E_PASS (checks 15 passed/0 failed, no-op re-apply=True, rollback=exact) |
