"""
Jev integration.

- RealJev: uses the TypeSafe SDK (requires TYPESAFE_API_KEY).
- MockJev: heuristic fallback when the SDK or key is unavailable.

Both expose the same interface, so pipeline.py does not care which is used.
"""
import os as _os
import re
import threading

try:
    from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
    _SDK = True
except ImportError:
    _SDK = False

TYPESAFE_API_KEY = "" # <-- set this before running
if TYPESAFE_API_KEY:
    _os.environ["TYPESAFE_API_KEY"] = TYPESAFE_API_KEY
# ----------------------------------------------------------------------
# RealJev — TypeSafe API
# ----------------------------------------------------------------------
_thread_local = threading.local()


def _client():
    if not hasattr(_thread_local, "c"):
        _thread_local.c = TypeSafeClient()
    return _thread_local.c


class RealJev:
    """Jev via the TypeSafe API. One system_one call per logical step."""

    MODEL = "jev-latest"

    # ---------------------------------------------------------------
    # Low-level call
    # ---------------------------------------------------------------
    def _call(self, state, questions):
        resp = _client().system_one(
            state=state,
            questions=questions,
            model=self.MODEL,
        )
        return resp.answers

    # ---------------------------------------------------------------
    # Phase 0 stage 1 — semantic fields for one chunk
    # ---------------------------------------------------------------
    def stage1(self, chunk: str, context: dict) -> dict:
        rooms = context.get("valid_rooms", [])
        scenes = context.get("valid_scenes", [])
        conv = context.get("conversation", {})

        room_criteria = {r: f"Room identified as '{r}'" for r in rooms}
        room_criteria["None"] = "No room is named or implied"
        scene_criteria = {s: f"Named scene '{s}'" for s in scenes}
        scene_criteria["None"] = "No known scene is named"

        state = {
            "utterance": chunk,
            "valid_rooms": rooms,
            "valid_scenes": scenes,
            "available_actions": [
                "Toggle", "On", "Off", "Set_Parameter",
                "Trigger_Scene", "Speak_Query", "Continuation",
            ],
            "conversation": {
                "last_room": conv.get("last_room_id"),
                "last_device": conv.get("last_device_id"),
                "last_action": conv.get("last_action"),
                "input_source_room": conv.get("input_source_room"),
            },
        }

        questions = {
            "action": Choice(
                instructions=(
                    "What action does this utterance request? "
                    "Use 'Continuation' if the utterance is a noun-phrase "
                    "extension of the previous chunk rather than a new "
                    "command. Use 'Speak_Query' if it asks a question. "
                    "A lock: 'lock the door' is On, 'unlock' is Off."
                ),
                criteria={
                    "Toggle": "Flip a device on/off, or a lock locked/unlocked",
                    "On": "Turn on, or lock a door",
                    "Off": "Turn off, or unlock a door",
                    "Set_Parameter": "Set a specific value: brightness, colour, temperature",
                    "Trigger_Scene": "Activate a scene or vibe",
                    "Speak_Query": "Ask a question, no state change",
                    "Continuation": "Noun-phrase extension of the previous chunk",
                },
            ),
            "room": Choice(
                instructions="Which room does this utterance target?",
                criteria=room_criteria,
            ),
            "parameter_type": Choice(
                instructions="If Set_Parameter, what kind of value does it require?",
                criteria={
                    "Percentage": "A brightness or level percentage",
                    "Colour": "A colour or hex value",
                    "Temperature": "A temperature value",
                    "Scene_Vibe": "A mood or vibe, not a named scene",
                    "None": "No parameter needed",
                },
            ),
            "relative_shift": Choice(
                instructions=(
                    "Does the utterance request a relative shift "
                    "('a bit dimmer', 'much brighter')? "
                    "If so, which direction and magnitude?"
                ),
                criteria={
                    "Increase_Small": "Slightly increase: 'a bit brighter', 'a little'",
                    "Increase_Large": "Significantly increase: 'much brighter', 'a lot'",
                    "Decrease_Small": "Slightly decrease: 'a bit dimmer', 'slightly lower'",
                    "Decrease_Large": "Significantly decrease: 'much dimmer', 'way down'",
                    "None": "No relative shift requested",
                },
            ),
            "scene": Choice(
                instructions="Does the utterance name a known scene?",
                criteria=scene_criteria,
            ),
        }

        try:
            answers = self._call(state, questions)
        except Exception as e:
            return self._stage1_fallback(chunk, context, e)

        return {
            "action": answers["action"].choice,
            "room": answers["room"].choice,
            "parameter_type": answers["parameter_type"].choice,
            "relative_shift": answers["relative_shift"].choice,
            "scene": answers["scene"].choice,
            "confidence": {
                "action": getattr(answers["action"], "confidence", 0.0),
                "target_room": getattr(answers["room"], "confidence", 0.0),
                "parameter": getattr(answers["parameter_type"], "confidence", 0.0),
                "scene": getattr(answers["scene"], "confidence", 0.0),
            },
        }

    # ---------------------------------------------------------------
    # Phase 0 stage 2 — device Nouls for one chunk
    # ---------------------------------------------------------------
    def stage2(self, chunk: str, candidate_ids: list) -> dict:
        if not candidate_ids:
            return {}

        state = {
            "utterance": chunk,
            "candidate_devices": [
                {"id": d, "hint": d.replace(".", " ").replace("_", " ")}
                for d in candidate_ids
            ],
        }

        questions = {
            f"is_target_{i}": Noul(
                instructions=(
                    f"Is device '{did}' a target of this utterance? "
                    f"Answer yes only if the utterance clearly refers to this "
                    f"device, its room, or its type."
                ),
            )
            for i, did in enumerate(candidate_ids)
        }

        try:
            answers = self._call(state, questions)
        except Exception:
            return {d: {"probability_true": 0.0} for d in candidate_ids}

        out = {}
        for i, did in enumerate(candidate_ids):
            out[did] = {"probability_true": answers[f"is_target_{i}"].noul}
        return out

    # ---------------------------------------------------------------
    # Phase 3 — parameter extraction
    # ---------------------------------------------------------------
    def extract_parameter(self, prompt: str, ptype: str,
                          device_ids: list) -> dict:
        t = prompt.lower()

        if ptype == "Percentage":
            m = re.search(r"(\d+)\s*%", t)
            if m:
                return {"value": int(m.group(1)), "confidence": 0.99,
                        "source": "regex"}
            m = re.search(r"to\s+(\d+)", t)
            if m:
                return {"value": int(m.group(1)), "confidence": 0.95,
                        "source": "regex"}

            state = {"utterance": prompt}
            levels = [f"{p}%" for p in range(0, 101, 20)]
            questions = {
                "percent": Score(
                    instructions="What brightness percentage does this request?",
                    criteria=levels,
                ),
            }
            try:
                a = self._call(state, questions)["percent"]
                pct = round(a.score * 20)
                return {"value": pct,
                        "confidence": a.confidence,
                        "source": "jev.score"}
            except Exception:
                return {"value": 50, "confidence": 0.0, "source": "error"}

        if ptype == "Temperature":
            m = re.search(r"(\d+)\s*(?:°|degrees?|c\b)", t)
            if m:
                return {"value": int(m.group(1)), "confidence": 0.98,
                        "source": "regex"}

            state = {"utterance": prompt}
            levels = [f"{c}°C" for c in range(15, 31, 5)]
            questions = {
                "temp": Score(
                    instructions="What temperature in Celsius does this request?",
                    criteria=levels,
                ),
            }
            try:
                a = self._call(state, questions)["temp"]
                temp = 15 + (a.score / (len(levels) - 1)) * 15
                return {"value": round(temp),
                        "confidence": a.confidence,
                        "source": "jev.score"}
            except Exception:
                return {"value": 21, "confidence": 0.0, "source": "error"}

        if ptype == "Colour":
            m = re.search(r"#([0-9A-Fa-f]{6})", t)
            if m:
                return {"value": "#" + m.group(1).upper(),
                        "confidence": 0.99, "source": "regex.hex"}

            state = {"utterance": prompt}
            questions = {
                "colour": Choice(
                    instructions="Which colour does this request?",
                    criteria={
                        "#FF0000": "Red", "#00FF00": "Green",
                        "#0000FF": "Blue", "#FFFF00": "Yellow",
                        "#FF00FF": "Magenta or pink", "#00FFFF": "Cyan",
                        "#FFFFFF": "White", "#000000": "Black or dark",
                        "#FF9900": "Orange", "#FFE4B5": "Warm white",
                        "#E0FFFF": "Cool white", "#808080": "Grey",
                    },
                ),
            }
            try:
                a = self._call(state, questions)["colour"]
                return {"value": a.choice,
                        "confidence": a.confidence,
                        "source": "jev.choice"}
            except Exception:
                return {"value": None, "confidence": 0.0, "source": "error"}

        if ptype == "Scene_Vibe":
            state = {"utterance": prompt}
            questions = {
                "vibe": Choice(
                    instructions="Which vibe or mood does this request?",
                    criteria={
                        "cyberpunk": "Neon, magenta, high energy",
                        "sunset": "Warm orange, low, relaxing",
                        "forest": "Green, natural, calm",
                        "ocean": "Blue, cool, serene",
                        "candlelight": "Very warm, dim, intimate",
                        "arctic": "Cool white, bright, crisp",
                        "calm": "Soft, muted, peaceful",
                        "none": "No vibe identified",
                    },
                ),
                "brightness": Score(
                    instructions="What brightness level does this vibe imply?",
                    criteria=["0%", "20%", "40%", "60%", "80%", "100%"],
                ),
            }
            try:
                ans = self._call(state, questions)
                hexmap = {
                    "cyberpunk": "#FF00FF", "sunset": "#FF6600",
                    "forest": "#228B22", "ocean": "#1E90FF",
                    "candlelight": "#FFA500", "arctic": "#E0FFFF",
                    "calm": "#DDA0DD", "none": "#808080",
                }
                vib = ans["vibe"].choice
                bri = round(ans["brightness"].score * 20)
                return {"value": {"hex": hexmap[vib], "brightness": bri},
                        "confidence": ans["vibe"].confidence,
                        "source": "jev.choice"}
            except Exception:
                return {"value": {"hex": "#808080", "brightness": 50},
                        "confidence": 0.0, "source": "error"}

        return {"value": None, "confidence": 0.0, "source": "none"}

    # ---------------------------------------------------------------
    # Phase 4 — Jev_2 question classification
    # ---------------------------------------------------------------
    def classify_question(self, prompt: str, context: dict) -> dict:
        entities = context.get("valid_entities", [])
        src_room = context.get("input_source_room")

        entity_criteria = {e: e.replace(".", " ").replace("_", " ")
                           for e in entities}
        entity_criteria["None"] = "No specific entity"

        state = {
            "question": prompt,
            "input_source_room": src_room,
            "valid_entities": entities,
        }

        questions = {
            "topic": Choice(
                instructions="What topic is this question about?",
                criteria={
                    "General_Knowledge": "World facts, not about this home",
                    "Home_Query": "About devices or rooms in this home",
                    "External_API": "Weather, calendar, time, external data",
                },
            ),
            "relevant_entities": Choice(
                instructions="Which specific entity is this question about?",
                criteria=entity_criteria,
            ),
            "query_type": Choice(
                instructions="What kind of data does answering this require?",
                criteria={
                    "Current_State": "Current state of a device",
                    "History_Logs": "Past events or changes over time",
                    "Both": "Both current state and history",
                    "External_API": "Data from an external service",
                    "General": "General knowledge, not stored data",
                },
            ),
            "timeframe_needed": Noul(
                instructions="Does answering this require a specific timeframe?",
            ),
        }

        try:
            a = self._call(state, questions)
            return {
                "topic": a["topic"].choice,
                "relevant_rooms": src_room or "None",
                "relevant_entities": a["relevant_entities"].choice,
                "query_type": a["query_type"].choice,
                "timeframe_needed": a["timeframe_needed"].noul > 0.5,
            }
        except Exception:
            return {
                "topic": "Home_Query",
                "relevant_rooms": src_room or "None",
                "relevant_entities": "None",
                "query_type": "Current_State",
                "timeframe_needed": False,
            }

    def _stage1_fallback(self, chunk, context, err):
        print(f"[jev] stage1 call failed: {err}")
        return {
            "action": "None", "room": "None", "parameter_type": "None",
            "relative_shift": "None", "scene": "None",
            "confidence": {"action": 0.0, "target_room": 0.0,
                           "parameter": 0.0, "scene": 0.0},
        }


# ----------------------------------------------------------------------
# MockJev — offline heuristic fallback
# ----------------------------------------------------------------------
ROOM_HINTS = {"kitchen": "kitchen", "living room": "living_room",
              "lounge": "living_room", "bedroom": "bedroom",
              "entry": "entry", "hallway": "entry"}

SCENE_HINTS = {"movie night": "movie_night", "cinema mode": "movie_night",
               "good morning": "good_morning", "relax": "relax",
               "chill": "relax"}

QUERY_WORDS = ["is ", "are ", "was ", "were ", "what", "when", "where",
               "how", "who", "?"]

OFF_WORDS = ["turn off", "switch off", "kill ", "shut off"]
ON_WORDS = ["turn on", "switch on", "enable"]
TOGGLE_WORDS = ["toggle", "flip"]
DIM_WORDS = ["dim", "brighten", "darker", "brighter"]
SET_WORDS = ["set ", "make it"]

COLOUR_HINTS = {
    "red": "#FF0000", "green": "#00FF00", "blue": "#0000FF",
    "yellow": "#FFFF00", "purple": "#FF00FF", "magenta": "#FF00FF",
    "cyan": "#00FFFF", "white": "#FFFFFF", "black": "#000000",
    "warm": "#FFE4B5", "warm white": "#FFE4B5", "cool": "#E0FFFF",
    "orange": "#FF9900",
}

VIBE_HINTS = {
    "cyberpunk": {"hex": "#FF00FF", "brightness": 60},
    "sunset": {"hex": "#FF6600", "brightness": 40},
    "forest": {"hex": "#228B22", "brightness": 50},
    "ocean": {"hex": "#1E90FF", "brightness": 50},
    "candlelight": {"hex": "#FFA500", "brightness": 20},
    "arctic": {"hex": "#E0FFFF", "brightness": 80},
    "calm": {"hex": "#DDA0DD", "brightness": 30},
}

RELATIVE_HINTS = {
    "a bit": "Decrease_Small", "a little": "Decrease_Small",
    "slightly": "Decrease_Small",
    "a lot": "Decrease_Large", "much": "Decrease_Large",
    "way": "Decrease_Large",
}

UP_HINTS = {
    "a bit": "Increase_Small", "a little": "Increase_Small",
    "slightly": "Increase_Small",
    "a lot": "Increase_Large", "much": "Increase_Large",
    "way": "Increase_Large",
}


class MockJev:
    """Heuristic stand-in for Jev. Same interface as RealJev."""

    def stage1(self, chunk: str, context: dict) -> dict:
        t = chunk.lower()
        out = {"action": "None", "room": "None", "parameter_type": "None",
               "relative_shift": "None", "scene": "None",
               "confidence": {}}

        if any(w in t for w in QUERY_WORDS):
            out["action"] = "Speak_Query"
            out["confidence"]["action"] = 0.97
        elif any(w in t for w in OFF_WORDS):
            out["action"] = "Off"
            out["confidence"]["action"] = 0.97
        elif any(w in t for w in ON_WORDS):
            out["action"] = "On"
            out["confidence"]["action"] = 0.96
        elif any(w in t for w in TOGGLE_WORDS):
            out["action"] = "Toggle"
            out["confidence"]["action"] = 0.95
        elif any(w in t for w in DIM_WORDS):
            out["action"] = "Set_Parameter"
            out["parameter_type"] = "Percentage"
            out["confidence"]["action"] = 0.94
            out["confidence"]["parameter"] = 0.90
        elif any(w in t for w in SET_WORDS):
            if any(w in t for w in COLOUR_HINTS):
                out["action"] = "Set_Parameter"
                out["parameter_type"] = "Colour"
            elif "temperature" in t or "degree" in t or "warm" in t or "cool" in t:
                out["action"] = "Set_Parameter"
                out["parameter_type"] = "Temperature"
            elif any(w in t for w in ["in ", "to ", "mode"]):
                out["action"] = "Trigger_Scene"
            else:
                out["action"] = "Set_Parameter"
                out["parameter_type"] = "Percentage"
            out["confidence"]["action"] = 0.90
            out["confidence"]["parameter"] = 0.85
        else:
            out["confidence"]["action"] = 0.40

        for hint, rid in ROOM_HINTS.items():
            if hint in t:
                out["room"] = rid
                out["confidence"]["target_room"] = 0.98
                break
        else:
            out["confidence"]["target_room"] = 0.0

        if "dim" in t or "darker" in t:
            for h, shift in RELATIVE_HINTS.items():
                if h in t:
                    out["relative_shift"] = shift
                    break
            else:
                out["relative_shift"] = "Decrease_Small"
        elif "brighten" in t or "brighter" in t:
            for h, shift in UP_HINTS.items():
                if h in t:
                    out["relative_shift"] = shift
                    break
            else:
                out["relative_shift"] = "Increase_Small"

        for hint, sid in SCENE_HINTS.items():
            if hint in t:
                out["scene"] = sid
                out["confidence"]["scene"] = 0.95
                break
        else:
            if "in " in t and "mode" in t:
                out["confidence"]["scene"] = 0.40

        return out

    def stage2(self, chunk: str, candidate_ids: list) -> dict:
        t = chunk.lower()
        out = {}
        for did in candidate_ids:
            short = did.split(".")[-1].replace("_", " ")
            room = did.split(".")[1].split("_")[0] if "." in did else ""
            prob = 0.05
            type_hint = did.split(".")[0]
            if type_hint in t or type_hint + "s" in t:
                prob = max(prob, 0.85)
            if "light" in t and type_hint == "light":
                prob = max(prob, 0.90)
            if "lock" in t and type_hint == "lock":
                prob = max(prob, 0.90)
            if "door" in t and "lock" in did:
                prob = max(prob, 0.95)
            for rname in [room.replace("_", " "), room]:
                if rname and rname in t:
                    prob = max(prob, 0.92)
            if "both" in t or "all" in t:
                prob = max(prob, 0.80)
            out[did] = {"probability_true": prob}
        return out

    def extract_parameter(self, prompt: str, ptype: str,
                          device_ids: list) -> dict:
        t = prompt.lower()

        if ptype == "Percentage":
            m = re.search(r"(\d+)\s*%", t)
            if m:
                return {"value": int(m.group(1)), "confidence": 0.99,
                        "source": "regex"}
            m = re.search(r"to\s+(\d+)", t)
            if m:
                return {"value": int(m.group(1)), "confidence": 0.95,
                        "source": "regex"}
            if "half" in t:
                return {"value": 50, "confidence": 0.90, "source": "jev.score"}
            return {"value": 50, "confidence": 0.40, "source": "jev.score"}

        if ptype == "Temperature":
            m = re.search(r"(\d+)\s*(?:°|degrees?|c\b)", t)
            if m:
                return {"value": int(m.group(1)), "confidence": 0.98,
                        "source": "regex"}
            return {"value": 21, "confidence": 0.40, "source": "jev.score"}

        if ptype == "Colour":
            m = re.search(r"#([0-9A-Fa-f]{6})", t)
            if m:
                return {"value": "#" + m.group(1).upper(),
                        "confidence": 0.99, "source": "regex.hex"}
            for name, hexv in COLOUR_HINTS.items():
                if name in t:
                    return {"value": hexv, "confidence": 0.92,
                            "source": "jev.choice"}
            return {"value": None, "confidence": 0.20, "source": "jev.choice"}

        if ptype == "Scene_Vibe":
            for name, spec in VIBE_HINTS.items():
                if name in t:
                    return {"value": spec, "confidence": 0.91,
                            "source": "jev.choice"}
            return {"value": {"hex": "#808080", "brightness": 50},
                    "confidence": 0.30, "source": "jev.choice"}

        return {"value": None, "confidence": 0.0, "source": "none"}

    def classify_question(self, prompt: str, context: dict) -> dict:
        t = prompt.lower()
        out = {
            "topic": "Home_Query",
            "relevant_rooms": "None",
            "relevant_entities": "None",
            "query_type": "Current_State",
        }
        if "weather" in t or "calendar" in t or "time" in t:
            out["topic"] = "External_API"
            out["query_type"] = "External_API"
        elif "when" in t or "history" in t or "last change" in t:
            out["query_type"] = "History_Logs"
        for did in context.get("valid_entities", []):
            short = did.split(".")[-1].replace("_", " ")
            if short in t or did in t:
                out["relevant_entities"] = did
                break
        if out["relevant_entities"] == "None":
            src_room = context.get("input_source_room")
            if src_room:
                out["relevant_rooms"] = src_room
        return out


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------
def make_jev(force_mock=False):
    key = TYPESAFE_API_KEY or _os.environ.get("TYPESAFE_API_KEY")
    if force_mock:
        print("[jev] using MockJev (forced)")
        return MockJev()
    if not key:
        print("[jev] using MockJev (no API key found)")
        return MockJev()
    if not _SDK:
        print("[jev] typesafe-sdk not installed; pip install typesafe-sdk")
        return MockJev()
    try:
        c = TypeSafeClient()
        print(f"[jev] TypeSafeClient initialised; key length = {len(key)}")
        return RealJev()
    except Exception as e:
        print(f"[jev] client init failed: {type(e).__name__}: {e}")
        print("[jev] falling back to MockJev")
        return MockJev()
