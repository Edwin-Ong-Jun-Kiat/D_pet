"""
engine1/module_1b_input_ingestion.py
Module 1B — Input Ingestion Pipeline

Responsibilities:
  - Accept text, audio, and image payloads.
  - Transcribe audio (stub — wire up Whisper for production).
  - Describe images via Gemini's vision capability (gemini-1.5-flash is multimodal).
  - Append every parsed input to the pet's interaction log in Firebase.
  - Increment the interaction counter and trigger Module 1A's threshold check.

Interaction log stored at: /pets/{pet_id}/interaction_log (list of dicts)
Each entry:
  {
    "timestamp":    ISO-8601,
    "input_type":   "text" | "audio" | "image",
    "raw_summary":  str,   # transcribed / described content
  }
"""

import base64
import logging
from datetime import datetime, timezone
from typing import Literal

from google import genai
from google.genai import types

from config import config
from firebase_client import get_db
import engine1.module_1a_state_machine as state_machine

logger = logging.getLogger(__name__)

# Initialise Gemini client once
client = genai.Client(api_key=config.GEMINI_API_KEY)

InputType = Literal["text", "audio", "image"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Per-type parsers ───────────────────────────────────────────────────────────

def _parse_text(content: str) -> str:
    """Text inputs need no transformation."""
    return content.strip()


def _parse_audio(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """
    Transcribe audio — stub for now.
    Wire up Whisper or Google Speech-to-Text for production.
    """
    logger.warning(
        "Audio transcription is a placeholder. "
        "Wire up Whisper or Google Speech-to-Text for production."
    )
    return "[audio input received — transcription pending Whisper integration]"


def _parse_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """
    Describe an image using Gemini's multimodal vision capability.
    Uses the new google-genai SDK with inline image bytes.
    """
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            "Describe what you see in this image in 2-3 sentences. "
            "Focus on objects, emotions, and context relevant to a "
            "digital companion pet that is learning about its owner.",
        ],
    )
    description = response.text.strip()
    logger.debug("Image described: %s", description[:80])
    return description


# ── Public API ─────────────────────────────────────────────────────────────────

def ingest(
    pet_id: str,
    input_type: InputType,
    content: str | bytes,
    mime_type: str | None = None,
) -> dict:
    """
    Main entry point. Parses the input, logs it to Firebase, and
    triggers the evolution threshold check.

    Parameters
    ----------
    pet_id      : Target pet's UUID.
    input_type  : "text", "audio", or "image".
    content     : str for text; bytes for audio/image.
    mime_type   : Required for audio and image (e.g. "image/jpeg").

    Returns
    -------
    dict with keys: pet_id, input_type, raw_summary, timestamp
    """
    # 1. Parse
    if input_type == "text":
        if not isinstance(content, str):
            raise TypeError("Text input must be a str.")
        raw_summary = _parse_text(content)

    elif input_type == "audio":
        if not isinstance(content, bytes):
            raise TypeError("Audio input must be bytes.")
        raw_summary = _parse_audio(content, mime_type or "audio/wav")

    elif input_type == "image":
        if not isinstance(content, bytes):
            raise TypeError("Image input must be bytes.")
        raw_summary = _parse_image(content, mime_type or "image/jpeg")

    else:
        raise ValueError(f"Unknown input_type: {input_type!r}")

    timestamp = _now_iso()

    # 2. Append to interaction log in Firebase
    log_entry = {
        "timestamp": timestamp,
        "input_type": input_type,
        "raw_summary": raw_summary,
    }
    db = get_db()
    db.reference(f"pets/{pet_id}/interaction_log").push(log_entry)
    logger.info("Logged %s input for pet %s.", input_type, pet_id)

    # 3. Increment counter in state machine
    state_machine.record_interaction(pet_id)

    # 4. Check evolution thresholds
    evolved = state_machine.check_evolution_thresholds(pet_id)
    if evolved:
        logger.info("Pet %s has hit evolution thresholds — evolution_pending set.", pet_id)

    return {
        "pet_id": pet_id,
        "input_type": input_type,
        "raw_summary": raw_summary,
        "timestamp": timestamp,
        "evolution_pending": evolved,
    }


def get_recent_interactions(pet_id: str, n: int = 10) -> list[dict]:
    """
    Fetch the n most recent interaction log entries for use as LLM context.
    Firebase push() keys are lexicographically ordered by time, so we sort
    by timestamp descending and take the first n.
    """
    db = get_db()
    raw = db.reference(f"pets/{pet_id}/interaction_log").get()
    if not raw:
        return []

    entries = list(raw.values())
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries[:n]
