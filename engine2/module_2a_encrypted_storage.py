"""
engine2/module_2a_encrypted_storage.py
Module 2A — Encrypted Storage Layer

Responsibilities:
  - Compile the full pet payload into a single JSON blob.
  - Encrypt it with AES-256-GCM using a key derived from the user's wallet
    signature (wallet signs a fixed message; we hash the signature as the key).
  - Upload the encrypted blob to Pinata (IPFS).
  - Return the ipfs:// content URI for on-chain storage.

Encryption scheme:
  key   = SHA-256( wallet.sign("dpet-storage-v1") )   [32 bytes]
  nonce = os.urandom(12)                               [12 bytes, AES-GCM]
  ciphertext + tag = AES-256-GCM.encrypt(key, nonce, plaintext)
  stored blob = base64( nonce || ciphertext || tag )

The stored IPFS file is a JSON wrapper:
  { "v": 1, "enc": "<base64 blob>", "pet_id": "<uuid>" }

Decryption requires the same wallet to re-sign the same message and derive
the same key — only the owner can read the plaintext.

Storage provider: Pinata (free tier, 1GB)
  Setup: app.pinata.cloud → API Keys → New Key → copy the JWT token
  .env key: PINATA_JWT=your_jwt_here

NOTE: In this Python implementation we simulate the wallet-derived key by
accepting the user's private key bytes and signing locally via solders.
In the mobile app the wallet adapter handles signing; only the signature
reaches the backend (never the private key).
"""

import base64
import hashlib
import json
import logging
import os
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from solders.keypair import Keypair

from config import config

logger = logging.getLogger(__name__)

SIGN_MESSAGE = b"dpet-storage-v1"


# ── Key derivation ─────────────────────────────────────────────────────────────

def derive_key_from_keypair(keypair: Keypair) -> bytes:
    """
    Sign the fixed message with the wallet keypair and hash the signature
    to produce a 32-byte AES key.

    In production the mobile wallet adapter returns only the signature bytes;
    the private key never leaves the device.
    """
    signature: bytes = keypair.sign_message(SIGN_MESSAGE).to_bytes()
    key = hashlib.sha256(bytes(signature)).digest()
    logger.debug("Derived AES key from wallet signature (first 8 bytes: %s).", key[:8].hex())
    return key


# ── Encryption / Decryption ───────────────────────────────────────────────────

def encrypt_payload(plaintext: bytes, key: bytes) -> str:
    """
    AES-256-GCM encrypt.
    Returns a base64-encoded string: nonce(12) || ciphertext || tag(16).
    """
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)
    blob = nonce + ciphertext_with_tag
    return base64.b64encode(blob).decode()


def decrypt_payload(encoded_blob: str, key: bytes) -> bytes:
    """
    AES-256-GCM decrypt. Inverse of encrypt_payload.
    """
    blob = base64.b64decode(encoded_blob)
    nonce = blob[:12]
    ciphertext_with_tag = blob[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext_with_tag, None)


# ── Pinata upload ─────────────────────────────────────────────────────────────

PINATA_UPLOAD_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"


def upload_to_pinata(payload_json: dict, pet_id: str) -> str:
    """
    Upload a JSON object to Pinata and return the ipfs:// CID URI.

    Pinata's pinJSONToIPFS endpoint accepts:
      { "pinataContent": <your json>, "pinataMetadata": { "name": "<label>" } }

    Returns: "ipfs://<CID>"

    Setup:
      1. Go to app.pinata.cloud → sign up (free, 1GB)
      2. API Keys → New Key → enable pinFileToIPFS + pinJSONToIPFS → Generate
      3. Copy the JWT token (the long one, not the API key/secret)
      4. Add to .env: PINATA_JWT=your_jwt_here
    """
    jwt = config.PINATA_JWT
    if not jwt:
        raise EnvironmentError(
            "PINATA_JWT is not set in .env.\n"
            "Get it from: app.pinata.cloud → API Keys → New Key → copy JWT"
        )

    body = {
        "pinataContent":  payload_json,
        "pinataMetadata": {"name": f"dpet-{pet_id[:8]}"},
        "pinataOptions":  {"cidVersion": 1},
    }

    headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type":  "application/json",
    }

    with httpx.Client(timeout=60) as client:
        resp = client.post(PINATA_UPLOAD_URL, json=body, headers=headers)

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Pinata upload failed: {resp.status_code} — {resp.text[:300]}"
        )

    cid = resp.json()["IpfsHash"]
    uri = f"ipfs://{cid}"
    logger.info("Uploaded to IPFS via Pinata. CID=%s URI=%s", cid, uri)
    return uri


# ── Public API ────────────────────────────────────────────────────────────────

def build_and_upload_pet_payload(
    pet: dict,
    gltf_local_path: str,
    keypair: Keypair,
) -> str:
    """
    Compile, encrypt, and upload the complete pet payload to IPFS.

    Parameters
    ----------
    pet             : Full pet dict from Firebase (from Module 1A).
    gltf_local_path : Local path to the assembled .gltf model file.
    keypair         : Solana Keypair used to derive the AES key.

    Returns
    -------
    ipfs:// URI string — store this on-chain as the token metadata URI.
    """
    # 1. Compile payload
    # Hash interaction history so the chain sees a fingerprint but not raw text
    import hashlib as _hl
    history_hash = _hl.sha256(
        json.dumps(pet.get("interaction_log", {}), sort_keys=True).encode()
    ).hexdigest()

    payload = {
        "phase":                  pet.get("phase"),
        "traits":                 pet.get("traits", {}),
        "3d_model_uri":           gltf_local_path,   # in prod: IPFS URI of model file
        "interaction_history_hash": history_hash,
        "created_at":             pet.get("created_at"),
        "pet_id":                 pet.get("pet_id"),
    }
    logger.debug("Pet payload compiled: %s", list(payload.keys()))

    # 2. Encrypt
    key = derive_key_from_keypair(keypair)
    plaintext = json.dumps(payload, separators=(",", ":")).encode()
    encoded_blob = encrypt_payload(plaintext, key)

    # 3. Wrap in a storage envelope
    storage_doc = {
        "v":      1,
        "pet_id": pet.get("pet_id"),
        "enc":    encoded_blob,
    }

    # 4. Upload to IPFS via Pinata
    uri = upload_to_pinata(storage_doc, pet.get("pet_id", "unknown"))
    return uri


def decrypt_from_ipfs(ipfs_json: dict, keypair: Keypair) -> dict:
    """
    Decrypt a storage document fetched from IPFS.

    Parameters
    ----------
    ipfs_json : The parsed JSON fetched from the IPFS URI.
    keypair   : Owner's Solana Keypair (used to re-derive the AES key).

    Returns
    -------
    The original plaintext payload as a dict.
    """
    key = derive_key_from_keypair(keypair)
    plaintext = decrypt_payload(ipfs_json["enc"], key)
    return json.loads(plaintext)
