"""Tkinter GUI: Chat, Rooms, Devices, State tabs."""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox


class App:
    def __init__(self, root, pipeline, registry, conversation, policy,
                 ingress_cache, log_fn):
        self.root = root
        self.pipeline = pipeline
        self.registry = registry
        self.conv = conversation
        self.policy = policy
        self.cache = ingress_cache
        self._log = log_fn

        root.title("Smart Home — Interactive Demo")
        root.geometry("1200x800")

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)

        self._build_chat_tab(nb)
        self._build_rooms_tab(nb)
        self._build_devices_tab(nb)
        self._build_state_tab(nb)

        self.policy.set_permissions("alice")

    # =========================================================
    # Chat tab
    # =========================================================
    def _build_chat_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Chat")

        pane = ttk.PanedWindow(tab, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=1)
        pane.add(right, weight=1)

        ttk.Label(left, text="Conversation").pack(anchor="w", padx=6, pady=(6, 0))
        self.chat = scrolledtext.ScrolledText(left, wrap="word", height=20)
        self.chat.pack(fill="both", expand=True, padx=6, pady=6)
        self.chat.tag_config("user", foreground="#1e6fbf",
                             font=("TkDefaultFont", 10, "bold"))
        self.chat.tag_config("system", foreground="#666")
        self.chat.tag_config("clarify", foreground="#b36b00",
                             font=("TkDefaultFont", 10, "italic"))

        ttk.Label(right, text="Pipeline trace").pack(anchor="w", padx=6, pady=(6, 0))
        self.trace = scrolledtext.ScrolledText(right, wrap="word", height=20,
                                               font=("Courier", 9))
        self.trace.pack(fill="both", expand=True, padx=6, pady=6)

        bottom = ttk.Frame(tab)
        bottom.pack(fill="x", padx=6, pady=(0, 6))

        ttk.Label(bottom, text="From:").pack(side="left")
        self.source_var = tk.StringVar(value="panel.kitchen_wall")
        self.source_combo = ttk.Combobox(
            bottom, textvariable=self.source_var,
            values=list(self.registry.input_sources.keys()),
            width=24, state="readonly")
        self.source_combo.pack(side="left", padx=6)

        self.entry = ttk.Entry(bottom)
        self.entry.pack(side="left", fill="x", expand=True, padx=6)
        self.entry.bind("<Return>", lambda e: self._send())
        ttk.Button(bottom, text="Send", command=self._send).pack(side="left")
        ttk.Button(bottom, text="Clear",
                   command=self._clear).pack(side="left", padx=(6, 0))

        self._chat_line("system", "Type a command or question. Try:")
        for ex in [
            "Dim the living room lights a bit, put the bedroom in cyberpunk mode, and is the front door locked?",
            "turn on the kitchen light",
            "is the front door locked?",
            "turn it off too",
        ]:
            self._chat_line("system", "  • " + ex)

    def _send(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._chat_line("user", "You: " + text)
        try:
            response = self.pipeline.handle(
                text, user_id="alice",
                input_source_id=self.source_var.get())
        except Exception as e:
            self._chat_line("clarify", f"[error] {e}")
            return
        self._render(response)
        self._refresh_state()
        self._refresh_devices()
        self._refresh_rooms()

    def _render(self, response):
        rtype = response.get("type")
        if rtype == "ok":
            for line in response.get("results", []):
                self._chat_line("system", "  ✔ " + str(line))
        elif rtype in ("clarify", "confirm", "deny"):
            self._chat_line("clarify",
                            f"[{rtype}] " + response.get("message", ""))
        elif rtype == "signal":
            self._chat_line("clarify", f"[signal] {response}")
        else:
            self._chat_line("system", str(response))

    def _chat_line(self, tag, text):
        self.chat.insert("end", text + "\n", tag)
        self.chat.see("end")

    def _clear(self):
        self.chat.delete("1.0", "end")
        self.trace.delete("1.0", "end")

    # =========================================================
    # Rooms tab  (NEW)
    # =========================================================
    def _build_rooms_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Rooms")

        top = ttk.Frame(tab)
        top.pack(fill="both", expand=True, padx=6, pady=6)

        cols = ("id", "name", "aliases", "devices")
        self.rooms_tree = ttk.Treeview(top, columns=cols, show="headings",
                                       height=12)
        for c, w in zip(cols, [180, 200, 320, 80]):
            self.rooms_tree.heading(c, text=c)
            self.rooms_tree.column(c, width=w)
        self.rooms_tree.pack(fill="both", expand=True)

        # Right-click menu
        self._rooms_menu = tk.Menu(self.rooms_tree, tearoff=0)
        self._rooms_menu.add_command(label="Delete room",
                                     command=self._delete_selected_room)
        self.rooms_tree.bind("<Button-3>", self._show_room_menu)
        self.rooms_tree.bind("<Button-2>", self._show_room_menu)
        self.rooms_tree.bind("<Delete>", lambda e: self._delete_selected_room())

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Button(btn_row, text="Delete selected",
                   command=self._delete_selected_room).pack(side="left")
        ttk.Label(btn_row,
                  text="(right-click a row or press Delete)",
                  foreground="#888").pack(side="left", padx=8)

        # Add form
        form = ttk.LabelFrame(tab, text="Add room")
        form.pack(fill="x", padx=6, pady=6)

        self.r_id = tk.StringVar()
        self.r_name = tk.StringVar()
        self.r_aliases = tk.StringVar()

        ttk.Label(form, text="room_id").grid(row=0, column=0, padx=4, pady=4)
        ttk.Entry(form, textvariable=self.r_id, width=18).grid(row=0, column=1)
        ttk.Label(form, text="name").grid(row=0, column=2)
        ttk.Entry(form, textvariable=self.r_name, width=20).grid(row=0, column=3)
        ttk.Label(form, text="aliases (comma-separated)").grid(row=0, column=4)
        ttk.Entry(form, textvariable=self.r_aliases,
                  width=30).grid(row=0, column=5, padx=4)
        ttk.Button(form, text="Add",
                   command=self._add_room).grid(row=0, column=6, padx=6)

        self._refresh_rooms()

    def _show_room_menu(self, event):
        row = self.rooms_tree.identify_row(event.y)
        if row:
            self.rooms_tree.selection_set(row)
            self._rooms_menu.tk_popup(event.x_root, event.y_root)
            self._rooms_menu.grab_release()

    def _add_room(self):
        rid = self.r_id.get().strip()
        name = self.r_name.get().strip() or rid
        aliases = [a.strip() for a in self.r_aliases.get().split(",")
                   if a.strip()]
        if not rid:
            messagebox.showerror("Missing", "room_id is required")
            return
        if rid in self.registry.rooms:
            messagebox.showerror("Duplicate", f"Room '{rid}' already exists.")
            return
        self.registry.add_room(rid, name, aliases)
        self._log(f"[registry] added room {rid} (aliases={aliases})")
        self.r_id.set("")
        self.r_name.set("")
        self.r_aliases.set("")
        self._refresh_rooms()
        self._refresh_device_room_options()

    def _delete_selected_room(self):
        sel = self.rooms_tree.selection()
        if not sel:
            messagebox.showinfo("Delete room", "Select a room first.")
            return

        room_ids = [self.rooms_tree.item(i, "values")[0] for i in sel]
        if not room_ids:
            return

        # Refuse if any room still has devices
        blocked = {}
        for rid in room_ids:
            devices = [d.device_id
                       for d in self.registry.devices.values()
                       if d.room_id == rid]
            if devices:
                blocked[rid] = devices

        if blocked:
            lines = []
            for rid, devs in blocked.items():
                lines.append(f"  • {rid} → {len(devs)} device(s): "
                             f"{', '.join(devs[:4])}"
                             f"{' …' if len(devs) > 4 else ''}")
            messagebox.showerror(
                "Cannot delete room",
                "These rooms still contain devices:\n\n"
                + "\n".join(lines)
                + "\n\nMove or delete the devices first.")
            return

        if not messagebox.askyesno(
                "Delete room",
                f"Delete the following room(s)?\n\n"
                f"{', '.join(room_ids)}\n\n"
                f"This does not affect devices (none are assigned)."):
            return

        for rid in room_ids:
            self._delete_room(rid)
        self._refresh_rooms()
        self._refresh_device_room_options()
        self._refresh_state()

    def _delete_room(self, room_id):
        if room_id not in self.registry.rooms:
            return
        del self.registry.rooms[room_id]

        # Clean up ConversationState references to this room
        if self.conv.last_room_id == room_id:
            self.conv.last_room_id = None
        self.conv.mentioned_rooms = [
            r for r in self.conv.mentioned_rooms if r != room_id
        ]
        if self.conv.input_source_room == room_id:
            # Only clear if the source itself points here (it shouldn't,
            # since sources are separate — but be safe)
            self.conv.input_source_room = None

        self._log(f"[registry] deleted room {room_id}")

    def _refresh_rooms(self):
        for row in self.rooms_tree.get_children():
            self.rooms_tree.delete(row)
        for r in self.registry.rooms.values():
            dev_count = sum(1 for d in self.registry.devices.values()
                            if d.room_id == r.room_id)
            self.rooms_tree.insert("", "end", values=(
                r.room_id, r.name, ", ".join(r.aliases) or "—",
                dev_count))

    # =========================================================
    # Devices tab
    # =========================================================
    def _build_devices_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Devices")

        top = ttk.Frame(tab)
        top.pack(fill="both", expand=True, padx=6, pady=6)

        cols = ("id", "room", "type", "state", "high_risk")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=12)
        for c, w in zip(cols, [220, 120, 100, 240, 80]):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True)

        self._menu = tk.Menu(self.tree, tearoff=0)
        self._menu.add_command(label="Delete device",
                               command=self._delete_selected_device)
        self.tree.bind("<Button-3>", self._show_device_menu)
        self.tree.bind("<Button-2>", self._show_device_menu)
        self.tree.bind("<Delete>", lambda e: self._delete_selected_device())

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Button(btn_row, text="Delete selected",
                   command=self._delete_selected_device).pack(side="left")
        ttk.Label(btn_row,
                  text="(right-click a row or press Delete)",
                  foreground="#888").pack(side="left", padx=8)

        form = ttk.LabelFrame(tab, text="Add device")
        form.pack(fill="x", padx=6, pady=6)

        self.d_id = tk.StringVar()
        self.d_room = tk.StringVar()
        self.d_type = tk.StringVar(value="light")
        self.d_hr = tk.BooleanVar(value=False)

        ttk.Label(form, text="device_id").grid(row=0, column=0, padx=4, pady=4)
        ttk.Entry(form, textvariable=self.d_id, width=24).grid(row=0, column=1)
        ttk.Label(form, text="room").grid(row=0, column=2)
        self.device_room_combo = ttk.Combobox(
            form, textvariable=self.d_room,
            values=list(self.registry.rooms.keys()),
            width=16, state="readonly")
        self.device_room_combo.grid(row=0, column=3)
        ttk.Label(form, text="type").grid(row=0, column=4)
        ttk.Combobox(form, textvariable=self.d_type,
                     values=["light", "switch", "lock", "thermostat",
                             "media_player", "plug"],
                     width=14, state="readonly").grid(row=0, column=5)
        ttk.Checkbutton(form, text="high_risk", variable=self.d_hr) \
            .grid(row=0, column=6, padx=6)
        ttk.Button(form, text="Add", command=self._add_device) \
            .grid(row=0, column=7)

        self._refresh_devices()

    def _refresh_device_room_options(self):
        """Keep the Devices tab's room dropdown in sync with the room list."""
        self.device_room_combo["values"] = list(self.registry.rooms.keys())

    def _show_device_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self._menu.tk_popup(event.x_root, event.y_root)
            self._menu.grab_release()

    def _delete_selected_device(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Delete device", "Select a device first.")
            return
        device_ids = [self.tree.item(i, "values")[0] for i in sel]
        if not device_ids:
            return

        names = ", ".join(device_ids)
        if not messagebox.askyesno(
                "Delete device",
                f"Delete the following device(s)?\n\n{names}\n\n"
                f"This also clears any references in conversation state."):
            return

        for did in device_ids:
            self._delete_device(did)
        self._refresh_devices()
        self._refresh_rooms()
        self._refresh_state()

    def _delete_device(self, device_id):
        if device_id not in self.registry.devices:
            return
        device = self.registry.devices[device_id]
        del self.registry.devices[device_id]

        if self.conv.last_device_id == device_id:
            self.conv.last_device_id = None
        self.conv.last_action_target = [
            d for d in self.conv.last_action_target if d != device_id
        ]
        self.conv.mentioned_devices = [
            d for d in self.conv.mentioned_devices if d != device_id
        ]
        self.conv.recent_commands = [
            {**cmd, "targets": [t for t in cmd.get("targets", [])
                                if t != device_id]}
            for cmd in self.conv.recent_commands
        ]
        if self.conv.pending_confirmation is not None:
            pc = self.conv.pending_confirmation
            if device_id in getattr(pc, "target_device_ids", []):
                self.conv.pending_confirmation = None
        if self.conv.pending_clarification is not None:
            pcl = self.conv.pending_clarification
            if device_id in getattr(pcl, "target_device_ids", []):
                self.conv.pending_clarification = None
                self.conv.pending_clarification_options = []

        self._log(f"[registry] deleted device {device_id} "
                  f"(was in {device.room_id})")

    def _add_device(self):
        did = self.d_id.get().strip()
        room = self.d_room.get().strip()
        dtype = self.d_type.get().strip()
        if not did or not room:
            messagebox.showerror("Missing", "device_id and room are required")
            return
        if room not in self.registry.rooms:
            messagebox.showerror("Unknown room",
                                 f"Room '{room}' does not exist.")
            return
        if did in self.registry.devices:
            messagebox.showerror("Duplicate", f"{did} already exists.")
            return
        caps = {"brightness": {"min": 0, "max": 100}, "colour": True} \
            if dtype == "light" else \
            {"lock": True} if dtype == "lock" else \
            {"temperature": {"min": 15, "max": 30, "unit": "C"}} \
            if dtype == "thermostat" else {}
        initial = {"on": False}
        if dtype == "lock":
            initial = {"locked": True}
        if dtype == "thermostat":
            initial = {"temperature": 21}
        self.registry.add_device(did, room, dtype, caps,
                                 high_risk=self.d_hr.get(),
                                 initial_state=initial)
        self._refresh_devices()
        self._refresh_rooms()
        self._refresh_state()
        self._log(f"[registry] added device {did}")

    def _refresh_devices(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for d in self.registry.devices.values():
            self.tree.insert("", "end", values=(
                d.device_id, d.room_id, d.type,
                str(d.state), "yes" if d.high_risk else "no"))

    # =========================================================
    # State tab
    # =========================================================
    def _build_state_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="State")

        top = ttk.Frame(tab)
        top.pack(fill="both", expand=True)

        ttk.Label(top, text="ConversationState").pack(anchor="w", padx=6,
                                                      pady=(6, 0))
        self.state_box = scrolledtext.ScrolledText(top, wrap="word", height=14,
                                                   font=("Courier", 9))
        self.state_box.pack(fill="both", expand=True, padx=6)

        ttk.Label(top, text="Ingress cache").pack(anchor="w", padx=6,
                                                  pady=(6, 0))
        self.cache_box = scrolledtext.ScrolledText(top, wrap="word", height=8,
                                                   font=("Courier", 9))
        self.cache_box.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        ttk.Button(tab, text="Refresh",
                   command=self._refresh_state).pack(pady=4)
        self._refresh_state()

    def _refresh_state(self):
        self.state_box.delete("1.0", "end")
        s = self.conv.snapshot()
        for k, v in s.items():
            self.state_box.insert("end", f"{k}: {v}\n")

        self.cache_box.delete("1.0", "end")
        for k, e in list(self.cache._entries.items()):
            self.cache_box.insert(
                "end",
                f"{k}  state={e.state}  action={e.action}  "
                f"targets={e.targets}  shift={e.relative_shift}\n"
            )

    # =========================================================
    # Log
    # =========================================================
    def log(self, msg):
        self.trace.insert("end", msg + "\n")
        self.trace.see("end")
