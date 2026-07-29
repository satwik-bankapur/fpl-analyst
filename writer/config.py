"""Layer 4 tunables (§2, §4, §7). No magic numbers in the module bodies."""
from __future__ import annotations

import os

from graph.sections import SECTION_TITLES

# --- payload / model budget (§2) -------------------------------------------
PAYLOAD_MAX_TOKENS = 3500          # hard input budget; enforced by drop order
OUTPUT_MAX_TOKENS = 1200           # generation cap
# Spec §2 asks for temperature 0.7, but the current Sonnet-class model rejects
# sampling params (400). We omit it by default (model self-manages sampling; voice
# variety is steered by the prompt) and only send it for models that accept it.
# See the Layer 4 §8 drift note.
TEMPERATURE = 0.7
SEND_TEMPERATURE = False           # flip True only for a model that accepts sampling params
CHARS_PER_TOKEN = 4                # cheap offline token estimate (~4 chars/token)

# Per-model $/1M tokens (input, output) for the harness cost line. Sonnet 5 intro rate.
MODEL_PRICING = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# --- validation gates (§7) -------------------------------------------------
LENGTH_MIN_WORDS = 250
LENGTH_MAX_WORDS = 700
MAX_RETRIES = 1                    # one evidence-appended retry, then stub fallback

# --- output structure (§3) -------------------------------------------------
# Derived from graph.sections — the ONE section vocabulary (writer/ may import
# graph/, never the reverse). Never hard-code a header string here again.
REQUIRED_HEADERS = [
    f"## {SECTION_TITLES['verdict']}",
    f"## {SECTION_TITLES['captaincy']}",
]
# The transfer header varies by action; verdict + captaincy are always required and
# ordered. Paid-only sections (bench_plan/chips/watchlist) are generated but gated
# at render time, so they are not *required* by the validator.
TRANSFER_HEADERS = (f"## {SECTION_TITLES['transfer']}", "## Roll the transfer")

# --- generic preview (§6) --------------------------------------------------
PREVIEW_TOP_EP = 8
PREVIEW_DIFFERENTIALS = 3
PREVIEW_MIN_WORDS = 400
PREVIEW_MAX_WORDS = 600

# --- model / auth (from env, NEVER hard-coded) -----------------------------
# Sonnet-class mid-tier target (§2). Overridable for the harness / future tuning.
DEFAULT_MODEL = "claude-sonnet-5"


def model_name() -> str:
    return os.environ.get("FPL_WRITER_MODEL", DEFAULT_MODEL)


def api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")
