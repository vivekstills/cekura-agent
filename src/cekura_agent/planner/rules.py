"""Shared planning rules: allowed actions per framework/mode and SDK pins."""

from __future__ import annotations

from ..models import ActionType, Framework, Mode

# Verified live on PyPI 2026-08-31: cekura 1.6.4+ ships both extras.
SDK_VERSION_SPEC = ">=1.6.4"


def sdk_package(framework: Framework) -> str:
    return f"cekura[{framework.value}]{SDK_VERSION_SPEC}"


def allowed_actions(framework: Framework, mode: Mode, already_integrated: bool) -> set[ActionType]:
    if already_integrated:
        return {ActionType.ALREADY_INTEGRATED_NOOP}
    common = {ActionType.ADD_DEPENDENCY, ActionType.ADD_ENV_PLACEHOLDER}
    if framework == Framework.LIVEKIT:
        tracer = (
            ActionType.INSERT_TRACK_SESSION if mode == Mode.TEST else ActionType.INSERT_OBSERVE_SESSION
        )
        return common | {ActionType.INSERT_TRACER_INIT, tracer}
    if framework == Framework.PIPECAT:
        return common | {ActionType.PIPECAT_SINGLE_STEP, ActionType.PIPECAT_MULTI_STEP}
    return set()
