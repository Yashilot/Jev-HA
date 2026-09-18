"""Entry point."""
import tkinter as tk

from devices import demo_home, DeviceSim
from stores import ConversationState, PolicyStore, ObservabilityStore, IngressCache
from jev import make_jev
from oracle import make_oracle
from pipeline import Pipeline
from app import App


def main():
    registry = demo_home()
    conversation = ConversationState()
    policy = PolicyStore()
    observability = ObservabilityStore()
    jev = make_jev()
    oracle = make_oracle(model="llama3.1:8b")

    # GUI first so we have a log sink
    root = tk.Tk()

    holder = {}

    def log(msg):
        if "app" in holder:
            holder["app"].log(msg)
        else:
            print(msg)

    cache = IngressCache(log=log)
    devices = DeviceSim(registry, log=log)
    pipeline = Pipeline(
        registry=registry,
        conversation=conversation,
        policy=policy,
        observability=observability,
        devices=devices,
        jev=jev,
        oracle=oracle,
        ingress_cache=cache,
        log=log,
    )

    app = App(root, pipeline, registry, conversation, policy, cache, log)
    holder["app"] = app

    root.mainloop()


if __name__ == "__main__":
    main()
