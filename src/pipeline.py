"""Phases 0–6. Orchestrates everything."""
import uuid
import re
from schemas import Command, Confidence, Reference, is_idempotent
from devices import EntityRegistry, DeviceSim


MAX_CHUNKS = 8
K_CANDIDATES = 20


class Pipeline:
    def __init__(self, registry, conversation, policy, observability,
                 devices, jev, oracle, ingress_cache, log):
        self.registry = registry
        self.conv = conversation
        self.policy = policy
        self.obs = observability
        self.devices = devices
        self.jev = jev
        self.oracle = oracle
        self.cache = ingress_cache
        self.log = log
        self._last_message = None

    # =========================================================
    # Entry point
    # =========================================================
    def handle(self, text, user_id, input_source_id):
        # Confirmation first — a "yes"/"no" reply must not be parsed
        # as a fresh utterance.
        if self.conv.pending_confirmation is not None:
            return self._answer_confirmation(text, user_id, input_source_id)

        # Clarification second
        if self.conv.pending_clarification is not None:
            return self._answer_clarification(text, user_id, input_source_id)

        turn_id = str(uuid.uuid4())[:8]
        trace_id = str(uuid.uuid4())[:8]
        self.log(f"\n=== turn {turn_id} · trace {trace_id} ===")
        self.log(f"input: {text!r}  user={user_id}  source={input_source_id}")

        key = (user_id, turn_id)
        entry, created = self.cache.get_or_create(key, initial={
            "action": "", "targets": [], "relative_shift": "None",
        })
        if not created:
            self.log("  [Layer 1] duplicate detected — returning cached result")
            if entry.state == "DONE":
                return entry.result
            if entry.state == "FAILED":
                return self._signal_to_response(entry.signal)
            return {"type": "clarify",
                    "message": "First request still in flight."}

        try:
            response = self._process(text, user_id, turn_id, trace_id,
                                     input_source_id)
            self.cache.put_terminal(key, "DONE", result=response)
            return response
        except Exception as e:
            self.cache.put_terminal(key, "FAILED", error=e,
                                    signal={"status": "indeterminate",
                                            "message": str(e)})
            raise

    def _signal_to_response(self, sig):
        return {"type": "signal", **(sig or {})}

    # =========================================================
    # Confirmation reply handler
    # =========================================================
    def _answer_confirmation(self, text, user_id, input_source_id):
        cmd = self.conv.pending_confirmation
        t = text.lower().strip()

        affirmative = t in {
            "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
            "confirm", "confirmed", "do it", "go ahead", "please do",
            "affirmative", "correct",
        }
        negative = t in {
            "no", "nope", "cancel", "stop", "nevermind", "never mind",
            "abort", "don't", "dont", "negative",
        }

        if negative:
            self.conv.pending_confirmation = None
            self.log(f"[confirm] cancelled: {cmd.command_id}")
            return {"type": "ok", "results": ["Cancelled."]}

        if not affirmative:
            # Stay in confirmation mode; ask again
            return {"type": "clarify",
                    "message": "Please answer yes or no."}

        # Accepted
        self.conv.pending_confirmation = None
        self.log(f"[confirm] accepted: {cmd.command_id}")

        # Route directly to Phase 2. We do NOT re-enter Phase 1, because
        # Phase 1 would prompt for confirmation again.
        branch = self._classify_branch(cmd)
        try:
            if branch == 1:
                return {"type": "ok", "results": [self._phase4_5(cmd)]}
            elif branch == 2:
                return {"type": "ok", "results": [self._phase2_scene(cmd)]}
            elif branch == 3:
                return {"type": "ok", "results": [self._phase2_relative(cmd)]}
            elif branch == 4:
                return {"type": "ok", "results": [self._phase3(cmd)]}
            else:
                return {"type": "ok", "results": [self._phase2_fast(cmd)]}
        except Exception as e:
            return {"type": "clarify", "message": f"Error: {e}"}

    # =========================================================
    # Clarification reply handler
    # =========================================================
    def _answer_clarification(self, text, user_id, input_source_id):
        cmd = self.conv.pending_clarification
        options = self.conv.pending_clarification_options
        t = text.lower().strip()

        # Normalize: strip articles and punctuation for matching
        t_norm = re.sub(r"\b(the|a|an|my)\b", "", t).strip()
        t_norm = re.sub(r"[^\w\s]", "", t_norm).strip()

        matched = None
        for opt in options:
            for dev_id in opt:
                # Try several forms for each device id
                candidates = {
                    dev_id.lower(),
                    dev_id.split(".")[-1].lower(),
                    dev_id.split(".")[-1].replace("_", " ").lower(),
                }
                # Add common synonyms
                short = dev_id.split(".")[-1].lower()
                if "light" in short:
                    candidates |= {"light", "lights", "lamp", "lamps",
                                   "bulb", "bulbs", "fixture", "fixtures"}
                if "lock" in short:
                    candidates |= {"lock", "locks", "door", "doors"}

                if any(c in t_norm for c in candidates if c):
                    matched = opt
                    break
            if matched:
                break

        # "both" matches a two-device option explicitly
        if not matched and "both" in t_norm:
            for opt in options:
                if len(opt) == 2:
                    matched = opt
                    break

        # "all" matches any option that contains more than one device
        if not matched and "all" in t_norm:
            for opt in options:
                if len(opt) > 1:
                    matched = opt
                    break

        if not matched:
            return {"type": "clarify",
                    "message": "Sorry, I didn't catch that. Which one? " +
                               " | ".join(", ".join(o) for o in options)}

        cmd.target_device_ids = matched
        cmd.context_required = False
        self.conv.pending_clarification = None
        self.conv.pending_clarification_options = []
        self.log(f"[clarify] resolved → {matched}")

        # Re-run Phase 1. If it fails, put the pending state back so the
        # user can try a different answer.
        outcome = self._phase1(cmd)
        if outcome != "OK":
            if outcome == "CLARIFY":
                self.conv.pending_clarification = cmd
                self.conv.pending_clarification_options = [matched]
            return {"type": outcome.lower(),
                    "message": self._last_message}

        branch = self._classify_branch(cmd)
        if branch == 1:
            return {"type": "ok", "results": [self._phase4_5(cmd)]}
        elif branch == 2:
            return {"type": "ok", "results": [self._phase2_scene(cmd)]}
        elif branch == 3:
            return {"type": "ok", "results": [self._phase2_relative(cmd)]}
        elif branch == 4:
            return {"type": "ok", "results": [self._phase3(cmd)]}
        else:
            return {"type": "ok", "results": [self._phase2_fast(cmd)]}

    # =========================================================
    # Phase 0
    # =========================================================
    def _process(self, text, user_id, turn_id, trace_id, input_source_id):
        self.conv.input_source_id = input_source_id
        self.conv.input_source_room = self.registry.room_of_source(input_source_id)

        chunks = self._split(text)
        self.log(f"[Phase 0] split into {len(chunks)} chunk(s): {chunks}")

        commands = []
        for i, chunk in enumerate(chunks[:MAX_CHUNKS]):
            stage1 = self.jev.stage1(chunk, {
                "valid_rooms": list(self.registry.rooms.keys()),
                "valid_scenes": list(self.registry.scenes.keys()),
                "conversation": self.conv.snapshot(),
            })
            self.log(f"[Phase 0] chunk {i+1} stage1: "
                     f"action={stage1['action']} "
                     f"room={stage1['room']} "
                     f"param={stage1['parameter_type']} "
                     f"shift={stage1['relative_shift']} "
                     f"scene={stage1['scene']}")

            candidates = self._narrow(stage1)
            self.log(f"[Phase 0] narrowed ({len(candidates)}): {candidates}")

            stage2 = self.jev.stage2(chunk, candidates)
            selected = [d for d, v in stage2.items()
                        if v["probability_true"] > 0.5]
            self.log(f"[Phase 0] stage2 → devices: {selected}")

            cmd = self._assemble(chunk, i, turn_id, user_id,
                                 input_source_id, stage1, selected, text)

            # Post-processing fix-ups
            cmd = self._correct_action(cmd)

            commands.append(cmd)

        commands = self._merge_continuations(commands)

        for order, cmd in enumerate(commands):
            cmd.order = order
            cmd.command_id = f"cmd_{trace_id}_{order}"

        for cmd in commands:
            self.log(f"[Phase 0] → {cmd.command_id}  "
                     f"action={cmd.action}  room={cmd.target_room_ids}  "
                     f"devices={cmd.target_device_ids}  "
                     f"shift={cmd.relative_shift}  "
                     f"param={cmd.parameter_type}")

        # Phase 1
        executable = []
        for cmd in commands:
            outcome = self._phase1(cmd)
            if outcome in ("CLARIFY", "DENY", "CONFIRM"):
                return {"type": outcome.lower(),
                        "message": self._last_message}
            executable.append(cmd)

        # Phase 2
        results = []
        for cmd in executable:
            branch = self._classify_branch(cmd)
            self.log(f"[Phase 2] {cmd.command_id} → branch {branch}")

            if branch == 1:
                results.append(self._phase4_5(cmd))
            elif branch == 2:
                results.append(self._phase2_scene(cmd))
            elif branch == 3:
                results.append(self._phase2_relative(cmd))
            elif branch == 4:
                results.append(self._phase3(cmd))
            else:
                results.append(self._phase2_fast(cmd))

        self.log("=== turn complete ===")
        return {"type": "ok", "results": results}

    def _split(self, text):
        parts = re.split(r"\s*(?:,\s+|\band\b\s+|\bthen\b\s+|\balso\b\s+)",
                         text, flags=re.IGNORECASE)
        return [p.strip() for p in parts if p.strip()]

    # ---- Action correction pass ----
    def _correct_action(self, cmd):
        """
        Some utterances get misclassified by Jev. This pass fixes the
        obvious cases before the command reaches Phase 1.
        """
        t = cmd.source_text.lower()

        # Phrases that clearly mean On / Off
        on_phrases = ["turn on", "switch on", "power on", "turning on"]
        off_phrases = ["turn off", "switch off", "power off", "shut off",
                       "shutting off", "kill "]
        has_on = any(p in t for p in on_phrases)
        has_off = any(p in t for p in off_phrases)

        # A phrase that requests a specific value
        value_words = ["dim ", "brightness", " percent", "%", "colour",
                       "color", "warm", "cool ", "degrees", "°",
                       " to 1", " to 2", " to 3", " to 4", " to 5",
                       " to 6", " to 7", " to 8", " to 9",
                       "to 0", "to 100", "set to"]
        has_value = any(w in t for w in value_words)

        # Only correct when there's a clear On/Off phrase and no value
        if has_on and not has_off and not has_value:
            if cmd.action != "On":
                self.log(f"    [correct] {cmd.action} → On "
                         f"(contains 'turn on' with no value)")
            cmd.action = "On"
            cmd.parameter_type = "None"
            cmd.relative_shift = "None"
        elif has_off and not has_on and not has_value:
            if cmd.action != "Off":
                self.log(f"    [correct] {cmd.action} → Off "
                         f"(contains 'turn off' with no value)")
            cmd.action = "Off"
            cmd.parameter_type = "None"
            cmd.relative_shift = "None"

        return cmd

    def _narrow(self, stage1):
        action = stage1["action"]
        param = stage1["parameter_type"]
        room = stage1["room"] if stage1["room"] != "None" else None

        if action == "Trigger_Scene":
            return []
        if action == "Speak_Query":
            return list(self.registry.devices.keys())
        if action == "Continuation":
            return []

        allowed_types = set()
        if action in ("On", "Off", "Toggle"):
            allowed_types = {"light", "switch", "plug",
                             "media_player", "lock"}
        elif action == "Set_Parameter":
            if param in ("Percentage", "Colour"):
                allowed_types = {"light"}
            elif param == "Temperature":
                allowed_types = {"thermostat"}

        pool = [d for d in self.registry.devices.values()
                if not allowed_types or d.type in allowed_types]
        if room:
            pool = [d for d in pool if d.room_id == room]

        rank = {}
        for i, d in enumerate(self.conv.last_action_target):
            rank[d] = 100 - i
        for d in self.conv.mentioned_devices:
            rank[d] = rank.get(d, 0) + 1
        pool.sort(key=lambda d: rank.get(d.device_id, 0), reverse=True)
        return [d.device_id for d in pool[:K_CANDIDATES]]

    def _assemble(self, chunk, i, turn_id, user_id, input_source_id,
                  stage1, selected, full_text):
        c = Command(
            turn_id=turn_id,
            command_id="pending",
            source_text=chunk,
            source_span=(0, len(chunk)),
            user_id=user_id,
            input_source_id=input_source_id,
            action=stage1["action"],
            target_room_ids=[stage1["room"]] if stage1["room"] != "None" else [],
            target_device_ids=selected,
            parameter_type=stage1["parameter_type"],
            relative_shift=stage1["relative_shift"],
            scene_type_id=stage1["scene"] if stage1["scene"] != "None" else None,
        )
        conf = stage1.get("confidence", {})
        c.confidence = Confidence(
            action=conf.get("action", 0.5),
            target_room=conf.get("target_room", 0.0),
            target_device=0.9 if selected else 0.0,
            parameter=conf.get("parameter", 0.0),
            scene=conf.get("scene", 0.0),
        )
        t = chunk.lower()
        if re.search(r"\bit\b", t) or re.search(r"\bthem\b", t):
            c.references.append(Reference(
                "it", "pronoun",
                "singular" if re.search(r"\bit\b", t) else "plural"))
            c.context_required = True
        if " too" in t or "also" in t or "as well" in t:
            c.references.append(Reference("too", "parallelism",
                                          "echo_previous_action"))
            c.context_required = True
        return c

    def _merge_continuations(self, commands):
        merged = []
        for c in commands:
            if c.action == "Continuation":
                if merged:
                    prev = merged[-1]
                    prev.target_device_ids = list(set(
                        prev.target_device_ids + c.target_device_ids))
                    prev.confidence.target_device = min(
                        prev.confidence.target_device,
                        c.confidence.target_device)
                    prev.source_text += " " + c.source_text
                else:
                    # Stray continuation with nothing to continue from.
                    self.log(f"[Phase 0] dropping leading Continuation: "
                             f"{c.source_text!r}")
                    continue
            else:
                merged.append(c)
        return merged

    # =========================================================
    # Phase 1
    # =========================================================
    def _phase1(self, cmd):
        self._last_message = None
        self.log(f"[Phase 1] {cmd.command_id}")

        if cmd.context_required and cmd.references:
            if not self._resolve_references(cmd):
                return "CLARIFY"

        if not cmd.target_room_ids and cmd.action != "Speak_Query":
            rung = self._room_ladder(cmd)
            self.log(f"    [1.4] room ladder → rung {rung}: "
                     f"{cmd.target_room_ids}")

        if not cmd.target_room_ids and not cmd.target_device_ids \
                and cmd.action != "Speak_Query":
            self._last_message = "Which room or device?"
            self.log("    [1.5] no target — clarify")
            return "CLARIFY"

        if cmd.action not in self.policy.allowed_actions:
            self._last_message = f"Unknown action {cmd.action}"
            self.log(f"    [1.6] action not in allow-list: {cmd.action}")
            return "DENY"

        if not self.policy.has_permission(cmd.user_id,
                                          cmd.target_device_ids,
                                          cmd.action):
            self._last_message = "Not permitted"
            self.log("    [1.7] permission denied")
            return "DENY"

        if self.policy.rate_limit_exceeded(cmd.user_id, cmd.action):
            self._last_message = "Rate limit exceeded"
            self.log("    [1.8] rate limit exceeded")
            return "DENY"

        if self._is_high_risk(cmd):
            self.log("    [1.9] high-risk → confirm")
            self._last_message = (
                f"Confirm: {cmd.action} on {cmd.target_device_ids}?"
            )
            self.conv.pending_confirmation = cmd
            return "CONFIRM"

        self.log("    [1.x] all checks pass")
        return "OK"

    def _resolve_references(self, cmd):
        refs = cmd.references
        candidates = []
        for r in refs:
            if r.type == "pronoun" and r.hint == "singular":
                if self.conv.last_device_id:
                    candidates.append(([self.conv.last_device_id], 0.62))
            if r.type == "pronoun" and r.hint == "plural":
                if self.conv.last_action_target:
                    candidates.append((self.conv.last_action_target, 0.60))
            if r.type == "parallelism":
                if self.conv.last_action_target:
                    candidates.append((self.conv.last_action_target, 0.58))

        filtered = []
        for targets, score in candidates:
            ok = []
            for d in targets:
                dev = self.registry.devices.get(d)
                if not dev:
                    continue
                if cmd.action in ("On", "Off", "Toggle"):
                    if dev.type in ("light", "switch", "plug",
                                    "media_player", "lock"):
                        ok.append(d)
                elif cmd.action == "Set_Parameter":
                    ok.append(d)
            if ok:
                filtered.append((ok, score))

        if not filtered:
            self._last_message = "I can't tell what you're referring to."
            return False

        filtered.sort(key=lambda x: -x[1])
        top = filtered[0][1]
        second = filtered[1][1] if len(filtered) > 1 else 0.0
        margin = top - second

        if top >= self.policy.thresholds["reference_top"] and \
           margin >= self.policy.thresholds["reference_margin"]:
            cmd.target_device_ids = filtered[0][0]
            cmd.context_required = False
            self.log(f"    [1.2] reference resolved → "
                     f"{cmd.target_device_ids}")
            return True

        options = [t for t, _ in filtered]
        self.conv.pending_clarification = cmd
        self.conv.pending_clarification_options = options
        self._last_message = (
            "Which one? " + " | ".join(", ".join(o) for o in options)
        )
        self.log(f"    [1.2] ambiguous (top={top:.2f}, margin={margin:.2f}) "
                 f"→ clarify")
        return False

    def _room_ladder(self, cmd):
        if cmd.target_room_ids:
            return 1
        if self.conv.input_source_room:
            cmd.target_room_ids = [self.conv.input_source_room]
            return 2
        if self.conv.last_room_id:
            cmd.target_room_ids = [self.conv.last_room_id]
            return 3
        return 4

    def _is_high_risk(self, cmd):
        if cmd.action in self.policy.high_risk_actions:
            for d in cmd.target_device_ids:
                dev = self.registry.devices.get(d)
                if dev and dev.high_risk:
                    return True
        return False

    # =========================================================
    # Phase 2
    # =========================================================
    def _classify_branch(self, cmd):
        if cmd.action == "Speak_Query":
            return 1
        if cmd.action == "Trigger_Scene":
            return 2
        if cmd.relative_shift != "None":
            return 3
        if cmd.parameter_type != "None":
            return 4
        return 5

    def _phase2_scene(self, cmd):
        if cmd.confidence.scene > self.policy.thresholds["scene"] \
                and cmd.scene_type_id:
            self._phase6(cmd, cmd.action, cmd.target_device_ids)
            return f"Scene {cmd.scene_type_id} triggered"
        return self._phase3(cmd)

    def _phase2_relative(self, cmd):
        results = []
        for d in cmd.target_device_ids:
            st = self.devices.fetch_state(d)
            if st is None:
                results.append(f"{d}: unavailable")
                continue
            cur = st.get("brightness", st.get("temperature", 50))
            delta = {
                "Increase_Small": +10, "Increase_Large": +30,
                "Decrease_Small": -10, "Decrease_Large": -30,
            }[cmd.relative_shift]
            new = max(0, min(100, cur + delta))
            self.log(f"    [2.3] {d}: {cur} → {new}")
            self._phase6(cmd, "Set_Parameter", [d], value=new,
                         relative_shift=cmd.relative_shift)
            results.append(f"{d} → {new}")
        return "; ".join(results)

    def _phase2_fast(self, cmd):
        self._phase6(cmd, cmd.action, cmd.target_device_ids)
        return f"{cmd.action} {cmd.target_device_ids}"

    # =========================================================
    # Phase 3
    # =========================================================
    def _phase3(self, cmd):
        ptype = cmd.parameter_type
        if ptype == "None" and cmd.action == "Trigger_Scene":
            ptype = "Scene_Vibe"
        res = self.jev.extract_parameter(cmd.source_text, ptype,
                                         cmd.target_device_ids)
        self.log(f"[Phase 3] {ptype} → {res['value']} "
                 f"(conf={res['confidence']:.2f}, src={res['source']})")
        if res["confidence"] < self.policy.thresholds["parameter"]:
            return f"Couldn't confidently extract {ptype} from that."
        cmd.parameter_value = res["value"]
        self._phase6(cmd, "Set_Parameter", cmd.target_device_ids,
                     value=res["value"])
        return f"{cmd.target_device_ids} → {res['value']}"

    # =========================================================
    # Phase 4 & 5
    # =========================================================
    def _phase4_5(self, cmd):
        context = {
            "valid_entities": list(self.registry.devices.keys()),
            "input_source_room": self.conv.input_source_room,
        }
        j2 = self.jev.classify_question(cmd.source_text, context)
        self.log(f"[Phase 4] topic={j2['topic']} "
                 f"entity={j2['relevant_entities']} "
                 f"query_type={j2['query_type']}")

        ctx = {}
        eid = j2["relevant_entities"]
        if eid and eid != "None":
            st = self.devices.fetch_state(eid)
            if st is not None:
                ctx["states"] = {eid: st}
                ctx["last_changed"] = "08:14 UTC today"

        answer = self.oracle.answer(cmd.source_text, j2, ctx)
        self.log(f"[Phase 5] Oracle: {answer}")
        return answer

    # =========================================================
    # Phase 6
    # =========================================================
    def _phase6(self, cmd, action, targets, value=None, relative_shift=None):
        for d in targets:
            self.devices.apply(action, d, value=value)
        self.conv.last_room_id = (
            self.registry.devices[targets[0]].room_id
            if targets and targets[0] in self.registry.devices else None
        )
        self.conv.last_device_id = targets[-1] if targets else None
        self.conv.last_action = action
        self.conv.last_action_target = list(targets)
        self.conv.recent_commands.append({
            "action": action, "targets": list(targets), "value": value})
        self.conv.recent_commands = self.conv.recent_commands[-10:]
