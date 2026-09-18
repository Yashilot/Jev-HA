# Jev-HA

A layered assistant pipeline for smart homes. Jev handles
every structured decision (routing, extraction, classification);
a read-only LLM handles questions. Most commands never touch a
large model.

## Why

Most systems use one LLM to handle all assistant tool calls and all queries; this has issues such as wasting tokens, long waiting times, and hallucinations.
Some other systems that do use Jev don't have the capabilities of LLMs when natural language output is ideal.
Jev-HA aims to solve these issues by using Jev for fast, cheap, and accurate home controls, while using LLMs when replying to natural queries like "What's the weather like?"

## Architecture

See ARCHITECTURE.md for the full spec.

- Phase 0: Jev extraction (per chunk, uniform schema)
- Phase 1: deterministic resolution + policy
- Phase 2: routing (5 branches)
- Phase 3: parameter extraction (regex first, Jev fallback)
- Phase 4: Jev question classification
- Phase 5: read-only Oracle
- Phase 6: execution + audit + state update

## Running the demo

pip install -r requirements.txt
ollama pull llama3.1:8b
export TYPESAFE_API_KEY="..."     # optional; falls back to MockJev
python main.py

## What's novel

- Reference resolution with a margin gate
- `Is_Idempotent(action, relative_shift)` for retry safety
- Two-layer dedup: turn_id + command_id
- Indeterminate/failed signal split
- Post-execution state reads for high-risk targets

## Status

Working demo. Not production-ready. Not affiliated with TypeSafe.
