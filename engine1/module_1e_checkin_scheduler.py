"""
engine1/module_1e_checkin_scheduler.py
Module 1E — Proactive Check-up Scheduler

Responsibilities:
  - Scan all active pets in Firebase.
  - If a pet has been inactive for > CHECKIN_INACTIVITY_HOURS, generate a
    check-in message using Claude and store it in Firebase.
  - Throttle to max 1 check-in per CHECKIN_THROTTLE_HOURS per pet.
  - Log the check-in message (production would push via FCM/Expo).

Check-in messages are stored at: /pets/{pet_id}/pending_checkins (push list)
Each entry: { "message": str, "generated_at": ISO-8601, "delivered": bool }

Usage:
  Run as a standalone cron job:
    python -m engine1.module_1e_checkin_scheduler

  Or import and call run_scheduler() from a Vercel/Cloud Scheduler handler.
"""

import logging
from datetime import datetime, timezone

from google import genai
from google.genai import types

from config import config
from firebase_client import get_db
import engine1.module_1a_state_machine as state_machine

logger = logging.getLogger(__name__)

# Initialise Gemini client once
client = genai.Client(api_key=config.GEMINI_API_KEY)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_since(iso_str: str | None) -> float:
    if not iso_str:
        return float("inf")
    then = datetime.fromisoformat(iso_str)
    delta = datetime.now(timezone.utc) - then
    return delta.total_seconds() / 3600


# ── Message generation ─────────────────────────────────────────────────────────

def generate_checkin_message(pet_id: str) -> str | None:
    """
    Generate a proactive check-in message for a single pet.

    Returns the message string, or None if the pet is not eligible.
    """
    pet = state_machine.get_pet(pet_id)
    if pet is None:
        logger.warning("generate_checkin_message: pet %s not found", pet_id)
        return None

    last_interaction = pet.get("last_interaction_at")
    last_checkin     = pet.get("last_checkin_at")

    inactive_hours = _hours_since(last_interaction)
    since_last_checkin = _hours_since(last_checkin)

    # Eligibility checks
    if inactive_hours < config.CHECKIN_INACTIVITY_HOURS:
        logger.debug("Pet %s active recently (%.1fh ago). Skipping.", pet_id, inactive_hours)
        return None

    if since_last_checkin < config.CHECKIN_THROTTLE_HOURS:
        logger.debug(
            "Pet %s checked in %.1fh ago (throttle=%dh). Skipping.",
            pet_id, since_last_checkin, config.CHECKIN_THROTTLE_HOURS,
        )
        return None

    traits = pet.get("traits", {})
    phase  = pet.get("phase", config.PHASES[0])
    traits_str = ", ".join(f"{k}={v}" for k, v in traits.items())

    prompt = (
        f"You are D-pet, a digital companion in the {phase} phase. "
        f"Your personality traits: {traits_str}. "
        f"Your owner hasn't spoken to you in {inactive_hours:.0f} hours. "
        "Write a friendly, in-character 2-sentence check-in message that shows "
        "you genuinely miss them. Stay consistent with your personality traits."
    )

    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction="You are D-pet. Write warmly and stay in character.",
            max_output_tokens=128,
        ),
    )
    message = response.text.strip()
    logger.info("Check-in generated for pet %s: %s", pet_id, message[:80])
    return message


def deliver_checkin(pet_id: str, message: str) -> None:
    """
    Store the check-in in Firebase and log it.
    In production this would also call Expo Push / FCM.
    """
    now = _now_iso()
    db = get_db()

    entry = {"message": message, "generated_at": now, "delivered": False}
    db.reference(f"pets/{pet_id}/pending_checkins").push(entry)

    # Update last_checkin_at so the throttle works correctly
    state_machine.update_pet(pet_id, {"last_checkin_at": now})

    # ── Production TODO ────────────────────────────────────────────────────
    # push_token = pet.get("push_token")
    # if push_token:
    #     expo_push(push_token, message)
    # ──────────────────────────────────────────────────────────────────────

    logger.info(
        "[CHECK-IN LOGGED] pet=%s | message: %s",
        pet_id, message,
    )


# ── Scheduler loop ─────────────────────────────────────────────────────────────

def run_scheduler() -> dict:
    """
    Scan every pet in Firebase and send check-ins where eligible.

    Returns a summary dict: { "checked": int, "sent": int, "pet_ids_sent": list }
    """
    db = get_db()
    pets_raw = db.reference("pets").get()

    if not pets_raw:
        logger.info("No pets found in database.")
        return {"checked": 0, "sent": 0, "pet_ids_sent": []}

    checked = 0
    sent = 0
    sent_ids = []

    for pet_id, pet_data in pets_raw.items():
        if not isinstance(pet_data, dict):
            continue
        checked += 1
        message = generate_checkin_message(pet_id)
        if message:
            deliver_checkin(pet_id, message)
            sent += 1
            sent_ids.append(pet_id)

    summary = {"checked": checked, "sent": sent, "pet_ids_sent": sent_ids}
    logger.info("Scheduler run complete: %s", summary)
    return summary


# ── CLI entry-point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )
    result = run_scheduler()
    print(f"\nScheduler result: {result}")
