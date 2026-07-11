"""Layer 5 — delivery surface (FastAPI routes, rate limit, Resend mail).

Stage A only: draft-path brief, preview, players, signup, health, admin.
Checkout / webhooks / weekly sends / paywall are Stage B (see docs/layer5).
The deterministic/LLM boundary still holds — this layer calls the graph via its
contract (`graph.cache.get_or_generate`), never the engine formula directly.
"""
