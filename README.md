# D_pet
Colosseum Hackathon project
# D-pet — System Architecture Implementation

AI companion with on-chain identity and physical presence.

---

## Project Structure

```
dpet/
├── config.py                          # Centralised config (reads .env)
├── firebase_client.py                 # Firebase singleton
├── requirements.txt
├── .env.example                       # Copy to .env and fill in keys
├── firebase_service_account.json      # Download from Firebase console
├── wallet_keypair.json                # Your Solana keypair [234,232,...]
│
├── engine1/                           # AI Evolution Engine
│   ├── module_1a_state_machine.py     # Pet lifecycle + threshold checks
│   ├── module_1b_input_ingestion.py   # Text / audio / image ingestion
│   ├── module_1c_personality_core.py  # Claude-powered personality + chat
│   ├── module_1d_3d_asset_pipeline.py # Trait JSON → .gltf model
│   └── module_1e_checkin_scheduler.py # Proactive check-in cron job
│
├── engine2/                           # Blockchain Mechanics
│   ├── module_2a_encrypted_storage.py # AES-256-GCM + NFT.Storage upload
│   ├── module_2b_token_minting.py     # Token2022 NFT minting on Devnet
│   ├── module_2c_evolution_contract.py# Atomic burn-and-mint evolution
│   └── module_2d_nfc_bridge.py        # FastAPI NFC bridge + simulator
│
├── assets/
│   ├── glb_parts/                     # Auto-generated mock .glb files
│   └── generated/                     # Output .gltf models per pet
│
└── tests/
    └── test_all_modules.py            # Full integration test suite
