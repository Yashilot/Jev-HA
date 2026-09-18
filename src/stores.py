"""ConversationState, PolicyStore, ObservabilityStore, IngressCache."""
import time
import threading
import uuid
from dataclasses import dataclass, field


class ConversationState:
    def __init__(self):
        self.input_source_id = None
        self.input_source_room = None
        self.last_room_id = None
        self.last_device_id = None
        self.last_action = None
        self.last_action_target = []
        self.mentioned_rooms = []
        self.mentioned_devices = []
        self.recent_commands = []
        self.pending_confirmation = None
        self.pending_clarification = None
        self.pending_clarification_options = []

    def snapshot(self):
        return {
            "input_source_id": self.input_source_id,
            "input_source_room": self.input_source_room,
            "last_room_id": self.last_room_id,
            "last_device_id": self.last_device_id,
            "last_action": self.last_action,
            "last_action_target": list(self.last_action_target),
            "mentioned_rooms": list(self.mentioned_rooms),
            "mentioned_devices": list(self.mentioned_devices),
            "recent_commands": list(self.recent_commands),
            "pending_confirmation": self.pending_confirmation,
            "pending_clarification": self.pending_clarification,
        }


class PolicyStore:
    def __init__(self):
        self.permissions = {}
        self.high_risk_actions = {"On", "Off", "Toggle"}
        self.rate_limits = {}
        self._rate_window = {}
        self.thresholds = {
            "action": 0.85,
            "target_room": 0.85,
            "target_device": 0.85,
            "parameter": 0.80,
            "scene": 0.85,
            "reference_top": 0.85,
            "reference_margin": 0.15,
            "jev_floor": 0.30,
        }
        self.allowed_actions = [
            "Toggle", "On", "Off", "Set_Parameter",
            "Trigger_Scene", "Speak_Query", "Clarify"
        ]

    def set_permissions(self, user_id, allowed_rooms=None, allowed_devices=None,
                        allowed_actions=None):
        self.permissions[user_id] = {
            "allowed_rooms": allowed_rooms or "ALL",
            "allowed_devices": allowed_devices or "ALL",
            "allowed_actions": allowed_actions or "ALL",
        }

    def has_permission(self, user_id, device_ids, action):
        p = self.permissions.get(user_id)
        if not p:
            return False
        if p["allowed_actions"] != "ALL" and action not in p["allowed_actions"]:
            return False
        for d in device_ids:
            if p["allowed_devices"] != "ALL" and d not in p["allowed_devices"]:
                return False
        return True

    def rate_limit_exceeded(self, user_id, action):
        key = (user_id, action)
        limit = self.rate_limits.get(key, {}).get("max_per_minute")
        if not limit:
            return False
        now = time.time()
        window = self._rate_window.setdefault(key, [])
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= limit:
            return True
        window.append(now)
        return False


class ObservabilityStore:
    def __init__(self):
        self.records = []

    def record(self, trace_id, turn_id, command_id, **fields):
        self.records.append({
            "trace_id": trace_id,
            "turn_id": turn_id,
            "command_id": command_id,
            "timestamp": time.time(),
            **fields,
        })


@dataclass
class CacheEntry:
    state: str
    action: str = ""
    relative_shift: str = "None"
    targets: list = field(default_factory=list)
    processing_deadline: float = 0.0
    expires_at: float = 0.0
    result: object = None
    error: object = None
    signal: object = None


class IngressCache:
    """
    Layer 1: cross-request deduplication, keyed on (user_id, turn_id).
    Uses a threading lock to emulate atomic Cache_Get_Or_Create_If_Absent.
    """
    DUPLICATE_WAIT = 5.0
    PROCESSING_DEADLINE = 15.0
    TERMINAL_LIFETIME = 60.0

    def __init__(self, log):
        self._lock = threading.Lock()
        self._entries: dict[tuple, CacheEntry] = {}
        self.log = log
        # Start reaper thread
        t = threading.Thread(target=self._reaper_loop, daemon=True)
        t.start()

    def get_or_create(self, key, initial):
        """Atomic. Returns (entry, created_bool)."""
        with self._lock:
            e = self._entries.get(key)
            if e is not None:
                return e, False
            entry = CacheEntry(
                state="IN_FLIGHT",
                action=initial.get("action", ""),
                relative_shift=initial.get("relative_shift", "None"),
                targets=initial.get("targets", []),
                processing_deadline=time.time() + self.PROCESSING_DEADLINE,
            )
            self._entries[key] = entry
            return entry, True

    def put_terminal(self, key, state, result=None, error=None, signal=None):
        with self._lock:
            e = self._entries.get(key)
            if e is None:
                return
            if e.state != "IN_FLIGHT":
                self.log(f"    [cache anomaly] terminal write to {e.state} entry {key}")
                return
            e.state = state
            e.result = result
            e.error = error
            e.signal = signal
            e.expires_at = time.time() + self.TERMINAL_LIFETIME

    def _reaper_loop(self):
        while True:
            time.sleep(1.0)
            now = time.time()
            with self._lock:
                for key, e in list(self._entries.items()):
                    if e.state == "IN_FLIGHT" and now > e.processing_deadline:
                        e.state = "FAILED"
                        e.signal = {
                            "status": "indeterminate",
                            "retry_safe": False,
                            "targets": e.targets,
                            "action": e.action,
                            "relative_shift": e.relative_shift,
                            "verify_hint": "Verify device state before retrying.",
                        }
                        e.expires_at = now + self.TERMINAL_LIFETIME
                        self.log(f"    [reaper] force-failed {key}")
                    elif e.state in ("DONE", "FAILED") and now > e.expires_at:
                        del self._entries[key]
