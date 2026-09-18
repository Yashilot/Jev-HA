"""Simulated device layer. Stands in for Home Assistant."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Device:
    device_id: str
    room_id: str
    type: str                       # light, lock, thermostat, switch, ...
    capabilities: dict = field(default_factory=dict)
    high_risk: bool = False
    state: dict = field(default_factory=dict)


@dataclass
class Room:
    room_id: str
    name: str
    aliases: list = field(default_factory=list)


@dataclass
class Scene:
    scene_id: str
    name: str
    aliases: list = field(default_factory=list)


@dataclass
class InputSource:
    input_source_id: str
    room_id: Optional[str]
    type: str = "wall_panel"


class EntityRegistry:
    """The source of truth. Read by every phase; written only by the admin UI."""

    def __init__(self):
        self.rooms: dict[str, Room] = {}
        self.devices: dict[str, Device] = {}
        self.scenes: dict[str, Scene] = {}
        self.input_sources: dict[str, InputSource] = {}

    # --- rooms ---
    def add_room(self, room_id, name, aliases=None):
        self.rooms[room_id] = Room(room_id, name, aliases or [])

    def find_room(self, text: str) -> Optional[str]:
        t = text.lower()
        for r in self.rooms.values():
            if r.room_id.replace("_", " ") in t or r.name.lower() in t:
                return r.room_id
            for a in r.aliases:
                if a.lower() in t:
                    return r.room_id
        return None

    # --- devices ---
    def add_device(self, device_id, room_id, type_, capabilities=None,
                   high_risk=False, initial_state=None):
        self.devices[device_id] = Device(
            device_id, room_id, type_,
            capabilities or {}, high_risk, initial_state or {}
        )

    def devices_in_room(self, room_id):
        return [d for d in self.devices.values() if d.room_id == room_id]

    # --- scenes ---
    def add_scene(self, scene_id, name, aliases=None):
        self.scenes[scene_id] = Scene(scene_id, name, aliases or [])

    def find_scene(self, text: str) -> Optional[str]:
        t = text.lower()
        for s in self.scenes.values():
            if s.scene_id.replace("_", " ") in t or s.name.lower() in t:
                return s.scene_id
            for a in s.aliases:
                if a.lower() in t:
                    return s.scene_id
        return None

    # --- input sources ---
    def add_input_source(self, sid, room_id, type_="wall_panel"):
        self.input_sources[sid] = InputSource(sid, room_id, type_)

    def room_of_source(self, sid) -> Optional[str]:
        s = self.input_sources.get(sid)
        return s.room_id if s else None


def demo_home() -> EntityRegistry:
    """The standard demo home from the worked example."""
    r = EntityRegistry()
    r.add_room("kitchen", "Kitchen", ["cookhouse"])
    r.add_room("living_room", "Living Room", ["lounge", "front room"])
    r.add_room("bedroom", "Bedroom", ["master bedroom"])
    r.add_room("entry", "Entry", ["hallway", "front hall"])

    r.add_device("light.kitchen_main", "kitchen", "light",
                 {"brightness": {"min": 0, "max": 100}, "colour": False},
                 initial_state={"on": False, "brightness": 0})
    r.add_device("light.living_room_main", "living_room", "light",
                 {"brightness": {"min": 0, "max": 100}, "colour": True},
                 initial_state={"on": True, "brightness": 80})
    r.add_device("light.living_room_accent", "living_room", "light",
                 {"brightness": {"min": 0, "max": 100}, "colour": True},
                 initial_state={"on": True, "brightness": 45})
    r.add_device("light.bedroom_main", "bedroom", "light",
                 {"brightness": {"min": 0, "max": 100}, "colour": True},
                 initial_state={"on": False, "brightness": 100})
    r.add_device("lock.front_door", "entry", "lock",
                 {"lock": True}, high_risk=True,
                 initial_state={"locked": True})
    r.add_device("thermostat.hall", "entry", "thermostat",
                 {"temperature": {"min": 15, "max": 30, "unit": "C"}},
                 initial_state={"temperature": 21})

    r.add_scene("movie_night", "Movie Night", ["cinema mode"])
    r.add_scene("good_morning", "Good Morning", ["wake up"])
    r.add_scene("relax", "Relax", ["chill", "wind down"])

    r.add_input_source("panel.kitchen_wall", "kitchen")
    r.add_input_source("speaker.living_room_echo", "living_room")
    r.add_input_source("speaker.bedroom_echo", "bedroom")
    r.add_input_source("app.alice_phone", None, "phone")
    r.add_input_source("watch.alice", None, "watch")
    return r


class DeviceSim:
    """Stand-in for Home Assistant's service call layer."""

    def __init__(self, registry: EntityRegistry, log):
        self.registry = registry
        self.log = log

    def fetch_state(self, device_id):
        d = self.registry.devices.get(device_id)
        return dict(d.state) if d else None

    def apply(self, action, device_id, value=None, relative_shift=None):
        d = self.registry.devices.get(device_id)
        if not d:
            return {"error": "unknown device"}
        s = d.state
        if action == "On":
            if d.type == "lock":
                s["locked"] = True
            else:
                s["on"] = True
        elif action == "Off":
            if d.type == "lock":
                s["locked"] = False
            else:
                s["on"] = False
        elif action == "Toggle":
            if d.type == "lock":
                s["locked"] = not s.get("locked", False)
            else:
                s["on"] = not s.get("on", False)
        elif action == "Set_Parameter":
            if isinstance(value, dict):
                if "brightness" in value:
                    s["brightness"] = value["brightness"]
                    s["on"] = value["brightness"] > 0
                if "hex" in value:
                    s["colour"] = value["hex"]
                    s["on"] = True
                if "temperature" in value:
                    s["temperature"] = value["temperature"]
            elif isinstance(value, (int, float)):
                if d.type == "thermostat":
                    s["temperature"] = value
                else:
                    s["brightness"] = value
                    s["on"] = value > 0
        self.log(f"    [device] {device_id} → {s}")
        return {"ok": True, "state": dict(s)}
