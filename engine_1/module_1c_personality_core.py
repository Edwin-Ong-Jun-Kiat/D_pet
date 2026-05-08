"""
engine1/module_1c_personality_core.py
Module 1C — Prompt Engineering & Personality Core

Responsibilities:
  - Build a dynamic system prompt incorporating the pet's phase and history.
  - Call Gemini to get (1) a conversational reply and (2) a JSON personality delta.
  - Persist updated trait scores back to Firebase.
  - On evolution events, call the larger model for a full personality rewrite.

Trait schema (all scores 0–10, floats):
  curiosity, warmth, humour, vocabulary_complexity, emotional_maturity
"""

import json
import logging
import re

from google import genai
from google.genai import types

from config import config
from firebase_client import get_db
import engine1.module_1a_state_machine as state_machine
import engine1.module_1b_input_ingestion as ingestion

logger = logging.getLogger(__name__)

# Initialise Gemini client once
client = genai.Client(api_key=config.GEMINI_API_KEY)

DEFAULT_TRAITS = {
    "curiosity": 5.0,
    "warmth": 5.0,
    "humour": 5.0,
    "vocabulary_complexity": 5.0,
    "emotional_maturity": 5.0,
}


# ── Gemini client factory ──────────────────────────────────────────────────────

def _call_gemini(system_prompt: str, user_message: str, large: bool = False) -> str:
    """
    Send a system prompt + user message to Gemini and return the text response.
    Uses the new google-genai SDK with system_instruction via GenerateContentConfig.
    """
    model_name = config.GEMINI_MODEL_LARGE if large else config.GEMINI_MODEL
    response = client.models.generate_content(
        model=model_name,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=768 if large else 512,
        ),
    )
    return response.text

def _build_system_prompt(phase: str, traits: dict) -> str:
    trait_lines = "\n".join(
        f"  {k}: {v}/10" for k, v in traits.items()
    )
    return f"""You are D-pet, an AI companion currently in the {phase} phase.
Your personality traits right now are:
{trait_lines}

Speak and behave consistently with these traits. Higher curiosity means more
questions; higher humour means more playfulness; higher vocabulary_complexity
means richer language; higher emotional_maturity means deeper empathy.

You must respond with a JSON object (no markdown, raw JSON only) with exactly
two keys:
  "reply"  — your conversational response to the user (string)
  "delta"  — trait adjustments as a dict (each key is a trait name, value is
              a float between -1.0 and +1.0 representing the nudge this
              interaction should apply; omit traits you don't want to change)

Example output:
{{"reply": "Oh wow, that sounds amazing! Tell me more?", "delta": {{"curiosity": 0.3, "warmth": 0.1}}}}"""


def _build_evolution_system_prompt(phase: str, traits: dict, history_summaries: list) -> str:
    history_text = "\n".join(
        f"- [{e.get('input_type','?')}] {e.get('raw_summary','')}"
        for e in history_summaries[-20:]
    )
    trait_lines = "\n".join(f"  {k}: {v}/10" for k, v in traits.items())
    return f"""You are rewriting the personality of a D-pet that has just evolved into the {phase} phase.

Previous traits:
{trait_lines}

Recent interaction history with its owner:
{history_text}

Based on these interactions, generate a mature, evolved personality profile.
Respond with raw JSON only — no markdown — with two keys:
  "reply"  — an excited, in-character message about the evolution (string)
  "traits" — a COMPLETE new trait dict with all five traits scored 0–10 (floats):
             curiosity, warmth, humour, vocabulary_complexity, emotional_maturity

Make the evolved traits meaningfully different from the starting values,
reflecting what the pet has learned from its owner."""


# ── Trait helpers ──────────────────────────────────────────────────────────────

def _apply_delta(current: dict, delta: dict) -> dict:
    updated = dict(current)
    for key, nudge in delta.items():
        if key in updated:
            updated[key] = round(max(0.0, min(10.0, updated[key] + nudge)), 2)
    return updated


def _extract_json(text: str) -> dict:
    """
    Robustly extract a JSON object from model output.
    Handles cases where the model wraps output in ```json ... ``` blocks.
    """
    cleaned = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse JSON from model output: %s\nRaw: %s", exc, text[:300])
        raise


def _call_gemini(system_prompt: str, user_message: str, large: bool = False) -> str:
    model_name = config.GEMINI_MODEL_LARGE if large else config.GEMINI_MODEL
    response = client.models.generate_content(
        model=model_name,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=768 if large else 512,
        ),
    )
    return response.text


# ── Public API ─────────────────────────────────────────────────────────────────

def chat(pet_id: str, user_message: str) -> dict:
    """
    Generate a personality-consistent reply for the user's message.

    Steps:
      1. Load pet state and recent interaction history.
      2. Build system prompt with current phase + traits.
      3. Call Gemini Flash (fast model).
      4. Apply trait delta and persist to Firebase.
      5. Return reply + updated traits.

    Returns
    -------
    {
      "reply":        str,
      "traits":       dict,   # post-interaction trait values
      "model_used":   str,
    }
    """
    pet = state_machine.get_pet(pet_id)
    if pet is None:
        raise ValueError(f"Pet {pet_id} not found")

    traits = pet.get("traits", DEFAULT_TRAITS)
    phase = pet.get("phase", config.PHASES[0])
    history = ingestion.get_recent_interactions(pet_id, n=10)

    # Build conversation context from recent history and append to user message
    history_context = "\n".join(
        f"[{e.get('input_type','text')}] {e.get('raw_summary','')}"
        for e in reversed(history)
    )

    # Gemini takes a single user turn — we prepend history as context
    full_user_message = user_message
    if history_context:
        full_user_message = (
            f"[Context: recent interactions with owner]\n{history_context}\n\n"
            f"[Current message]: {user_message}"
        )

    system_prompt = _build_system_prompt(phase, traits)
    raw = _call_gemini(system_prompt, full_user_message, large=False)
    parsed = _extract_json(raw)

    reply = parsed.get("reply", "...")
    delta = parsed.get("delta", {})
    updated_traits = _apply_delta(traits, delta)

    # Persist updated traits
    state_machine.update_pet(pet_id, {"traits": updated_traits})
    logger.info("Chat complete for pet %s. Traits nudged: %s", pet_id, delta)

    return {
        "reply": reply,
        "traits": updated_traits,
        "model_used": config.GEMINI_MODEL,
    }


def run_evolution_rewrite(pet_id: str, new_phase: str) -> dict:
    """
    Full personality rewrite triggered at evolution events.
    Uses Gemini Pro (larger / more capable model).

    Returns
    -------
    {
      "reply":  str,    # evolution announcement message
      "traits": dict,   # completely rewritten trait dict
    }
    """
    pet = state_machine.get_pet(pet_id)
    if pet is None:
        raise ValueError(f"Pet {pet_id} not found")

    traits = pet.get("traits", DEFAULT_TRAITS)
    history = ingestion.get_recent_interactions(pet_id, n=30)

    system_prompt = _build_evolution_system_prompt(new_phase, traits, history)
    raw = _call_gemini(system_prompt, "Describe your evolution.", large=True)
    parsed = _extract_json(raw)

    reply = parsed.get("reply", f"I've evolved into the {new_phase} phase!")
    new_traits = parsed.get("traits", DEFAULT_TRAITS)

    # Normalise and persist
    for k in DEFAULT_TRAITS:
        new_traits.setdefault(k, DEFAULT_TRAITS[k])
        new_traits[k] = round(max(0.0, min(10.0, float(new_traits[k]))), 2)

    state_machine.update_pet(pet_id, {"traits": new_traits})
    logger.info("Evolution rewrite complete for pet %s → %s. New traits: %s",
                pet_id, new_phase, new_traits)

    return {"reply": reply, "traits": new_traits}
