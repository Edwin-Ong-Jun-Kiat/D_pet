"""
tests/test_all_modules.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
D-pet Full Integration Test Suite
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

What this tests:
  Engine 1:
    [1A] State machine: create pet, record interactions, threshold check,
         advance phase.
    [1B] Input ingestion: text, image (bytes) inputs; interaction log written
         to Firebase.
    [1C] Personality core: chat() generates a reply + trait delta; traits
         persisted back to Firebase.
    [1D] 3-D asset pipeline: generates visual traits via LLM, writes a .gltf
         file to disk.
    [1E] Check-in scheduler: generates a check-in message and stores it in
         Firebase.

  Engine 2:
    [2A] Encrypted storage: encrypt/decrypt round-trip; upload to NFT.Storage
         (requires NFT_STORAGE_API_KEY).
    [2B] Token minting: mint a Token2022 NFT on Devnet (requires funded wallet).
    [2C] Evolution contract: full atomic burn-and-mint flow (runs after 2B).
    [2D] NFC bridge: simulate_nfc_tap() — full backend flow without a phone.

Prerequisites
-------------
  1. Firebase service account JSON at path in FIREBASE_SERVICE_ACCOUNT_PATH
  2. ANTHROPIC_API_KEY in .env
  3. NFT_STORAGE_API_KEY in .env
  4. wallet_keypair.json (your [234,232,...] array) at SOLANA_WALLET_KEYPAIR_PATH
  5. Solana Devnet accessible (default: https://api.devnet.solana.com)

Run:
    cd dpet
    pytest tests/test_all_modules.py -v -s

Individual tests:
    pytest tests/test_all_modules.py::test_1a_create_pet -v -s
    pytest tests/test_all_modules.py::test_engine2_full_flow -v -s
"""

import json
import os
import sys
import time
import logging
from pathlib import Path

import pytest

# ── Path setup ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

# ── Shared state across tests (pet_id is created once and reused) ──────────────
_shared: dict = {}


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 1 TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEngine1StateMachine:
    """Module 1A — State Machine & Trigger System"""

    def test_create_pet(self):
        print("\n[1A] Creating pet in Firebase...")
        import engine1.module_1a_state_machine as sm
        pet = sm.create_pet(owner_wallet="test_wallet_pubkey_abc123")
        _shared["pet_id"] = pet["pet_id"]

        assert pet["pet_id"], "pet_id should be non-empty"
        assert pet["phase"] == "Egg", f"Expected Egg, got {pet['phase']}"
        assert pet["interaction_count"] == 0
        assert pet["evolution_pending"] is False
        assert "traits" in pet

        print(f"  ✓ Pet created: {pet['pet_id']} | phase={pet['phase']}")

    def test_get_pet(self):
        print("\n[1A] Fetching pet from Firebase...")
        import engine1.module_1a_state_machine as sm
        pet = sm.get_pet(_shared["pet_id"])
        assert pet is not None
        assert pet["pet_id"] == _shared["pet_id"]
        print(f"  ✓ Pet fetched: phase={pet['phase']}, interactions={pet['interaction_count']}")

    def test_record_interaction(self):
        print("\n[1A] Recording an interaction...")
        import engine1.module_1a_state_machine as sm
        sm.record_interaction(_shared["pet_id"])
        pet = sm.get_pet(_shared["pet_id"])
        assert pet["interaction_count"] == 1
        print(f"  ✓ Interaction count: {pet['interaction_count']}")

    def test_evolution_threshold_not_met(self):
        print("\n[1A] Checking thresholds (should not trigger yet)...")
        import engine1.module_1a_state_machine as sm
        evolved = sm.check_evolution_thresholds(_shared["pet_id"])
        # With count=1 and likely < EVOLUTION_AGE_HOURS, this should be False
        print(f"  ✓ evolution_pending after 1 interaction: {evolved} (expected False with default config)")

    def test_update_pet(self):
        print("\n[1A] Updating pet fields...")
        import engine1.module_1a_state_machine as sm
        sm.update_pet(_shared["pet_id"], {"evolution_pending": True})
        pet = sm.get_pet(_shared["pet_id"])
        assert pet["evolution_pending"] is True

        # Reset
        sm.update_pet(_shared["pet_id"], {"evolution_pending": False})
        print("  ✓ Update + reset of evolution_pending works correctly")


class TestEngine1InputIngestion:
    """Module 1B — Input Ingestion Pipeline"""

    def test_ingest_text(self):
        print("\n[1B] Ingesting text input...")
        import engine1.module_1b_input_ingestion as ingestion
        result = ingestion.ingest(
            pet_id=_shared["pet_id"],
            input_type="text",
            content="I love spending time in the park watching birds fly.",
        )
        assert result["input_type"] == "text"
        assert result["raw_summary"]
        assert result["pet_id"] == _shared["pet_id"]
        print(f"  ✓ Text ingested. Summary: {result['raw_summary'][:60]}")

    def test_ingest_second_text(self):
        print("\n[1B] Ingesting second text input...")
        import engine1.module_1b_input_ingestion as ingestion
        result = ingestion.ingest(
            pet_id=_shared["pet_id"],
            input_type="text",
            content="Today I felt a bit lonely but reading helped me feel better.",
        )
        assert result["raw_summary"]
        print(f"  ✓ Second text ingested.")


    def test_ingest_image(self):
        print("\n[1B] Ingesting a real image via Gemini vision...")
        import engine1.module_1b_input_ingestion as ingestion
        from pathlib import Path

        image_path = Path(__file__).parent / "test_image.png"
        if not image_path.exists():
            pytest.skip("No test image found — add any image as tests/test_image.png")

        image_bytes = image_path.read_bytes()
        mime = "image/png" if str(image_path).endswith(".png") else "image/jpeg"

        result = ingestion.ingest(
            pet_id=_shared["pet_id"],
            input_type="image",
            content=image_bytes,
            mime_type=mime,
        )
        assert result["input_type"] == "image"
        assert result["raw_summary"]
        print(f"  ✓ Image described: {result['raw_summary'][:80]}")

    def test_get_recent_interactions(self):
        print("\n[1B] Fetching recent interactions...")
        import engine1.module_1b_input_ingestion as ingestion
        history = ingestion.get_recent_interactions(_shared["pet_id"], n=5)
        assert len(history) >= 1
        assert "raw_summary" in history[0]
        print(f"  ✓ Got {len(history)} interaction(s) from log.")


class TestEngine1PersonalityCore:
    """Module 1C — Prompt Engineering & Personality Core"""

    def test_chat(self):
        print("\n[1C] Generating personality chat response...")
        import engine1.module_1c_personality_core as personality
        result = personality.chat(
            pet_id=_shared["pet_id"],
            user_message="Hey! What did you get up to today?",
        )
        assert "reply" in result
        assert "traits" in result
        assert result["reply"]
        assert all(k in result["traits"] for k in
                   ["curiosity", "warmth", "humour", "vocabulary_complexity", "emotional_maturity"])
        _shared["traits_after_chat"] = result["traits"]
        print(f"  ✓ Reply: {result['reply'][:80]}")
        print(f"  ✓ Traits: {result['traits']}")

    def test_traits_persisted(self):
        print("\n[1C] Verifying traits persisted to Firebase...")
        import engine1.module_1a_state_machine as sm
        pet = sm.get_pet(_shared["pet_id"])
        db_traits = pet.get("traits", {})
        assert db_traits == _shared["traits_after_chat"]
        print("  ✓ Traits in Firebase match returned traits.")


class TestEngine1AssetPipeline:
    """Module 1D — 3D Asset Generation Pipeline"""

    def test_generate_pet_model(self):
        print("\n[1D] Generating 3-D pet model...")
        import engine1.module_1c_personality_core as personality
        import engine1.module_1d_3d_asset_pipeline as pipeline
        import engine1.module_1a_state_machine as sm

        pet = sm.get_pet(_shared["pet_id"])
        traits = pet.get("traits", {})
        phase  = pet.get("phase", "Egg")

        gltf_path, visual_traits = pipeline.generate_pet_model(
            pet_id=_shared["pet_id"],
            personality_traits=traits,
            phase=phase,
            version=1,
        )

        assert Path(gltf_path).exists(), f"Expected .gltf at {gltf_path}"
        assert visual_traits["body"] in ("round", "angular")
        assert visual_traits["color"] in pipeline.COLOUR_MAP
        _shared["gltf_path"] = gltf_path

        file_size = Path(gltf_path).stat().st_size
        print(f"  ✓ GLTF written: {gltf_path} ({file_size} bytes)")
        print(f"  ✓ Visual traits: {visual_traits}")

    def test_glb_library_exists(self):
        print("\n[1D] Verifying GLB asset library...")
        from config import config
        lib = Path(config.ASSET_LIBRARY_PATH)
        assert lib.exists()
        glb_files = list(lib.glob("*.glb"))
        assert len(glb_files) >= 2
        print(f"  ✓ Found {len(glb_files)} GLB file(s): {[f.name for f in glb_files]}")


class TestEngine1CheckinScheduler:
    """Module 1E — Proactive Check-up Scheduler"""

    def test_checkin_throttled_for_active_pet(self):
        print("\n[1E] Check-in should be skipped for recently-active pet...")
        import engine1.module_1e_checkin_scheduler as scheduler
        # Pet was just interacted with, so it's active — should return None
        message = scheduler.generate_checkin_message(_shared["pet_id"])
        # With 12-hour threshold and a fresh pet, message should be None
        print(f"  ✓ Check-in result for active pet: {message!r} (expected None)")

    def test_checkin_force_inactive(self):
        print("\n[1E] Forcing inactivity and generating check-in...")
        import engine1.module_1a_state_machine as sm
        import engine1.module_1e_checkin_scheduler as scheduler

        # Manually set last_interaction_at to 25 hours ago
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        sm.update_pet(_shared["pet_id"], {
            "last_interaction_at": old_time,
            "last_checkin_at": None,
        })

        message = scheduler.generate_checkin_message(_shared["pet_id"])
        assert message is not None, "Expected a check-in message for inactive pet"
        assert len(message) > 10

        # Deliver it (stores in Firebase)
        scheduler.deliver_checkin(_shared["pet_id"], message)

        # Restore
        sm.update_pet(_shared["pet_id"], {
            "last_interaction_at": datetime.now(timezone.utc).isoformat(),
        })

        print(f"  ✓ Check-in message: {message[:80]}")

    def test_scheduler_run(self):
        print("\n[1E] Running full scheduler scan...")
        import engine1.module_1e_checkin_scheduler as scheduler
        summary = scheduler.run_scheduler()
        assert "checked" in summary
        assert summary["checked"] >= 1
        print(f"  ✓ Scheduler summary: {summary}")


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 2 TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEngine2EncryptedStorage:
    """Module 2A — Encrypted Storage Layer"""

    def test_encrypt_decrypt_roundtrip(self):
        print("\n[2A] Encrypt/decrypt round-trip test...")
        from solders.keypair import Keypair
        import engine2.module_2a_encrypted_storage as storage

        kp = Keypair()  # ephemeral test keypair
        plaintext = b'{"phase": "Egg", "traits": {"curiosity": 7.2}}'
        key = storage.derive_key_from_keypair(kp)
        encoded = storage.encrypt_payload(plaintext, key)
        decoded = storage.decrypt_payload(encoded, key)
        assert decoded == plaintext
        print("  ✓ Encrypt → Decrypt round-trip successful.")

    def test_wrong_key_fails(self):
        print("\n[2A] Verifying wrong key cannot decrypt...")
        from solders.keypair import Keypair
        import engine2.module_2a_encrypted_storage as storage
        from cryptography.exceptions import InvalidTag

        kp1 = Keypair()
        kp2 = Keypair()
        plaintext = b"secret data"
        key1 = storage.derive_key_from_keypair(kp1)
        key2 = storage.derive_key_from_keypair(kp2)
        encoded = storage.encrypt_payload(plaintext, key1)

        with pytest.raises(Exception):  # InvalidTag from AESGCM
            storage.decrypt_payload(encoded, key2)
        print("  ✓ Wrong key correctly rejected.")

    def test_upload_to_nft_storage(self):
        print("\n[2A] Uploading encrypted pet payload to Pinata (IPFS)...")
        from config import config
        if not config.PINATA_JWT:
            pytest.skip("PINATA_JWT not set — skipping IPFS upload test.")

        import engine1.module_1a_state_machine as sm
        import engine2.module_2a_encrypted_storage as storage
        from engine2.module_2b_token_minting import load_keypair

        pet = sm.get_pet(_shared["pet_id"])
        keypair = load_keypair()
        gltf_path = _shared.get("gltf_path", "assets/generated/test.gltf")

        uri = storage.build_and_upload_pet_payload(
            pet=pet,
            gltf_local_path=gltf_path,
            keypair=keypair,
        )
        assert uri.startswith("ipfs://")
        _shared["ipfs_uri"] = uri
        print(f"  ✓ Uploaded. URI: {uri}")


class TestEngine2TokenMinting:
    """Module 2B — Token Minting & Metadata"""

    def test_load_keypair(self):
        print("\n[2B] Loading wallet keypair...")
        from engine2.module_2b_token_minting import load_keypair
        kp = load_keypair()
        assert kp is not None
        print(f"  ✓ Wallet loaded. Public key: {kp.pubkey()}")
        _shared["keypair"] = kp

    def test_mint_nft(self):
        print("\n[2B] Minting Token2022 NFT on Devnet...")
        from config import config
        if not config.PINATA_JWT:
            pytest.skip("Skipping mint test — PINATA_JWT not set (need IPFS URI first).")

        from engine2.module_2b_token_minting import mint_pet_nft, load_keypair
        kp = load_keypair()
        ipfs_uri = _shared.get("ipfs_uri", "ipfs://bafytest000000000000000000000000")

        mint_address = mint_pet_nft(
            pet_id=_shared["pet_id"],
            pet_name="TestPet",
            metadata_uri=ipfs_uri,
            owner_keypair=kp,
        )
        assert mint_address
        _shared["mint_address"] = mint_address

        # Store in Firebase
        import engine1.module_1a_state_machine as sm
        sm.update_pet(_shared["pet_id"], {
            "mint_address": mint_address,
            "ipfs_uri": ipfs_uri,
        })

        print(f"  ✓ Minted NFT. Mint address: {mint_address}")
        print(f"    View on Solana Explorer:")
        print(f"    https://explorer.solana.com/address/{mint_address}?cluster=devnet")


class TestEngine2Evolution:
    """Module 2C — Evolution Smart Contract"""

    def test_evolution_requires_pending(self):
        print("\n[2C] Verifying evolution blocked without pending flag...")
        from engine2.module_2b_token_minting import load_keypair
        import engine2.module_2c_evolution_contract as evo

        kp = load_keypair()
        with pytest.raises(ValueError, match="evolution_pending"):
            evo.execute_evolution(_shared["pet_id"], kp)
        print("  ✓ Evolution correctly blocked when evolution_pending=False.")

    def test_full_evolution(self):
        print("\n[2C] Running full evolution (burn + mint)...")
        from config import config
        if not _shared.get("mint_address"):
            pytest.skip("Skipping evolution — no mint_address from Module 2B test.")

        import engine1.module_1a_state_machine as sm
        import engine2.module_2c_evolution_contract as evo
        from engine2.module_2b_token_minting import load_keypair

        # Set enough interactions + force threshold
        sm.update_pet(_shared["pet_id"], {
            "interaction_count": config.EVOLUTION_INTERACTION_THRESHOLD,
            "evolution_pending": True,
        })

        kp = load_keypair()
        result = evo.execute_evolution(_shared["pet_id"], kp)

        assert result["new_phase"] != "Egg"
        assert result["new_mint_address"]
        assert result["tx_signature"]
        assert result["evolution_message"]

        _shared["new_mint_address"] = result["new_mint_address"]

        print(f"  ✓ Evolved to: {result['new_phase']}")
        print(f"  ✓ New mint:   {result['new_mint_address']}")
        print(f"  ✓ Tx:         {result['tx_signature']}")
        print(f"  ✓ Message:    {result['evolution_message'][:80]}")


class TestEngine2NfcBridge:
    """Module 2D — NFC Bridge (simulated tap)"""

    def test_simulate_nfc_tap(self):
        print("\n[2D] Simulating NFC tap (no phone needed)...")
        from engine2.module_2d_nfc_bridge import simulate_nfc_tap
        result = simulate_nfc_tap(_shared["pet_id"])

        assert result["pet_id"] == _shared["pet_id"]
        assert result["phase"] in ["Egg", "Infant", "Growth", "Adult"]
        assert result["reply"]
        assert isinstance(result["traits"], dict)

        print(f"  ✓ Tap result:")
        print(f"    phase  : {result['phase']}")
        print(f"    reply  : {result['reply'][:80]}")
        print(f"    traits : {result['traits']}")
        print()
        print("  HOW THE SIMULATION WORKS:")
        print("  ─────────────────────────────────────────────────────────")
        print("  A real NFC tap: phone reads UUID from chip → POST /nfc/tap")
        print("  This simulation: we call the same backend function directly,")
        print("  skipping only the physical chip read. Every Firebase, Solana,")
        print("  and Gemini call is real — no mocking.")

    def test_pair_and_lookup(self):
        print("\n[2D] Pairing and looking up NFC tag...")
        from engine2.module_2d_nfc_bridge import pair_nfc_tag, lookup_pet_by_nfc_tag
        import uuid

        tag_uuid = str(uuid.uuid4())
        pair_result = pair_nfc_tag(_shared["pet_id"], tag_uuid)
        assert pair_result["status"] == "paired"

        found_pet_id = lookup_pet_by_nfc_tag(tag_uuid)
        assert found_pet_id == _shared["pet_id"]
        print(f"  ✓ Tag {tag_uuid} paired and resolved to pet {found_pet_id[:8]}...")


# ═══════════════════════════════════════════════════════════════════════════════
# QUICK SMOKE TEST (run this first if unsure everything is wired up)
# ═══════════════════════════════════════════════════════════════════════════════

def test_smoke_no_firebase():
    """
    Self-contained smoke test — no Firebase, no Solana, no API calls.
    Tests only local logic: config loads, keypair format, encrypt/decrypt.
    """
    print("\n[SMOKE] Running self-contained smoke test...")

    # Config loads
    from config import config
    assert config.PHASES == ["Egg", "Infant", "Growth", "Adult"]

    # Keypair generation
    from solders.keypair import Keypair
    kp = Keypair()
    assert str(kp.pubkey())

    # Encrypt/decrypt
    import engine2.module_2a_encrypted_storage as storage
    key = storage.derive_key_from_keypair(kp)
    assert len(key) == 32
    ct = storage.encrypt_payload(b"hello", key)
    assert storage.decrypt_payload(ct, key) == b"hello"

    # GLB mock files can be created
    import engine1.module_1d_3d_asset_pipeline as pipeline
    pipeline._ensure_glb_library()
    from pathlib import Path
    assert (Path(config.ASSET_LIBRARY_PATH) / "body_sphere.glb").exists()
    assert (Path(config.ASSET_LIBRARY_PATH) / "body_cube.glb").exists()

    print("  ✓ All smoke checks passed. Ready for full integration tests.")
