"""
engine1/module_1a_state_machine.py
Module 1A — State Machine & Trigger System

Responsibilities:
  - Stores and retrieves per-pet state from Firebase Realtime DB.
  - Checks evolution thresholds on every interaction.
  - Sets evolution_pending = True when thresholds are met.
  - Advances the phase when evolution is confirmed (called by Module 2C).

Firebase schema for each pet (stored at /pets/{pet_id}):
  {
    "pet_id":             str,
    "owner_wallet":       str,
    "phase":              str   (Egg | Infant | Growth | Adult),
    "interaction_count":  int,
    "evolution_count":    int,
    "evolution_pending":  bool,
    "created_at":         ISO-8601 str,
    "last_interaction_at": ISO-8601 str,
    "last_checkin_at":    ISO-8601 str | null,
    "traits":             dict  (populated by Module 1C),
    "mint_address":       str | null,
    "nfc_tag_id":         str | null,
  }
"""

import logging
import uuid
from datetime import datetime, timezone

from config import config
from firebase_client import get_db

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_since(iso_str: str) -> float:
    """Return fractional hours elapsed since an ISO-8601 timestamp."""
    then = datetime.fromisoformat(iso_str)
    delta = datetime.now(timezone.utc) - then
    return delta.total_seconds() / 3600


# ── Pet lifecycle ──────────────────────────────────────────────────────────────

def create_pet(owner_wallet: str, nfc_tag_id: str | None = None) -> dict:
    """
    Create a brand-new pet in the Egg phase and persist it to Firebase.
    Returns the full pet dict.
    """
    pet_id = str(uuid.uuid4())
    now = _now_iso()

    pet = {
        "pet_id": pet_id,
        "owner_wallet": owner_wallet,
        "phase": config.PHASES[0],          # "Egg"
        "interaction_count": 0,
        "evolution_count": 0,
        "evolution_pending": False,
        "created_at": now,
        "last_interaction_at": now,
        "last_checkin_at": None,
        "traits": {
            "curiosity": 5,
            "warmth": 5,
            "humour": 5,
            "vocabulary_complexity": 5,
            "emotional_maturity": 5,
        },
        "mint_address": None,
        "nfc_tag_id": nfc_tag_id,
    }

    db = get_db()
    db.reference(f"pets/{pet_id}").set(pet)
    logger.info("Created pet %s for wallet %s", pet_id, owner_wallet)
    return pet


def get_pet(pet_id: str) -> dict | None:
    """Fetch a pet from Firebase. Returns None if not found."""
    db = get_db()
    data = db.reference(f"pets/{pet_id}").get()
    return data


def update_pet(pet_id: str, updates: dict) -> None:
    """Partial update of pet fields."""
    db = get_db()
    db.reference(f"pets/{pet_id}").update(updates)


# ── Threshold logic ────────────────────────────────────────────────────────────

def check_evolution_thresholds(pet_id: str) -> bool:
    """
    Evaluate whether the pet should evolve.

    Conditions (both must be true):
      - interaction_count >= EVOLUTION_INTERACTION_THRESHOLD
      - hours since created_at >= EVOLUTION_AGE_HOURS
      - pet is not already pending and has a next phase available

    Returns True if evolution_pending was just set to True.
    """
    pet = get_pet(pet_id)
    if pet is None:
        logger.warning("check_evolution_thresholds: pet %s not found", pet_id)
        return False

    # Already waiting — don't double-trigger
    if pet.get("evolution_pending"):
        return False

    current_phase = pet.get("phase", config.PHASES[0])
    phase_index = config.PHASES.index(current_phase) if current_phase in config.PHASES else 0

    # No further evolution possible
    if phase_index >= len(config.PHASES) - 1:
        logger.info("Pet %s is already at max phase (%s).", pet_id, current_phase)
        return False

    interactions_ok = pet.get("interaction_count", 0) >= config.EVOLUTION_INTERACTION_THRESHOLD
    age_ok = _hours_since(pet["created_at"]) >= config.EVOLUTION_AGE_HOURS

    if interactions_ok and age_ok:
        update_pet(pet_id, {"evolution_pending": True})
        logger.info(
            "Evolution threshold met for pet %s (phase=%s). evolution_pending=True.",
            pet_id, current_phase,
        )
        return True

    logger.debug(
        "Pet %s not ready to evolve — interactions=%d/%d, age_hours=%.2f/%.2f",
        pet_id,
        pet.get("interaction_count", 0), config.EVOLUTION_INTERACTION_THRESHOLD,
        _hours_since(pet["created_at"]), config.EVOLUTION_AGE_HOURS,
    )
    return False


def record_interaction(pet_id: str) -> None:
    """
    Increment interaction_count and update last_interaction_at.
    Called by Module 1B after every successful input ingestion.
    """
    pet = get_pet(pet_id)
    if pet is None:
        raise ValueError(f"Pet {pet_id} not found")

    updates = {
        "interaction_count": pet.get("interaction_count", 0) + 1,
        "last_interaction_at": _now_iso(),
    }
    update_pet(pet_id, updates)
    logger.debug("Recorded interaction for pet %s. Count=%d", pet_id, updates["interaction_count"])


def advance_phase(pet_id: str) -> str:
    """
    Move the pet to the next phase and clear evolution_pending.
    Called by Module 2C after a successful on-chain burn-and-mint.
    Returns the new phase string.
    """
    pet = get_pet(pet_id)
    if pet is None:
        raise ValueError(f"Pet {pet_id} not found")

    current = pet.get("phase", config.PHASES[0])
    idx = config.PHASES.index(current) if current in config.PHASES else 0
    next_phase = config.PHASES[min(idx + 1, len(config.PHASES) - 1)]

    update_pet(pet_id, {
        "phase": next_phase,
        "evolution_pending": False,
        "evolution_count": pet.get("evolution_count", 0) + 1,
    })
    logger.info("Pet %s advanced from %s → %s", pet_id, current, next_phase)
    return next_phase
