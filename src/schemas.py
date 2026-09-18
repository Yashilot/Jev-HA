"""Dataclasses mirroring the Command schema, Confidence, Reference, Signal."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Confidence:
    action: float = 0.0
    target_room: float = 0.0
    target_device: float = 0.0
    parameter: float = 0.0
    scene: float = 0.0


@dataclass
class Reference:
    token: str
    type: str          # "pronoun" | "parallelism" | "ellipsis"
    hint: str = ""


@dataclass
class Command:
    turn_id: str
    command_id: str
    source_text: str
    source_span: tuple
    user_id: str
    input_source_id: str
    action: str = "None"
    target_room_ids: list = field(default_factory=list)
    target_device_ids: list = field(default_factory=list)
    parameter_type: str = "None"
    relative_shift: str = "None"
    scene_type_id: Optional[str] = None
    confidence: Confidence = field(default_factory=Confidence)
    depends_on: list = field(default_factory=list)
    order: int = 0
    requires_confirmation: bool = False
    context_required: bool = False
    references: list = field(default_factory=list)
    parameter_value: object = None   # filled in by Phase 3


@dataclass
class Signal:
    status: str          # "succeeded" | "failed" | "indeterminate"
    retry_safe: bool
    targets: list = field(default_factory=list)
    action: str = ""
    relative_shift: str = "None"
    verify_hint: str = ""
    message: str = ""


def is_idempotent(action: str, relative_shift: str) -> bool:
    if action == "Set_Parameter" and relative_shift != "None":
        return False
    if action == "Toggle":
        return False
    return True
