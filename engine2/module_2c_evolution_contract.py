"""
engine2/module_2c_evolution_contract.py
Module 2C — Evolution Smart Contract

Responsibilities:
  - Verify evolution_pending === True in Firebase (Module 1A) before touching chain.
  - Build an atomic Solana transaction:
      Instruction 1: Burn the current phase token (close the token account).
      Instruction 2: Mint a new token with the evolved metadata URI.
  - Both instructions are in the SAME transaction — atomic, no orphaned state.
  - On success: advance phase in Firebase, clear evolution_pending, call
    Module 1C for a full personality rewrite, generate a new 3-D model,
    update the IPFS URI.

Why atomicity matters
---------------------
If we split burn and mint into two transactions and the second fails, the user
ends up with a burned token and no pet. Bundling them into one transaction
means the Solana runtime either commits both or rolls back completely.
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts

from config import config
import engine1.module_1a_state_machine as state_machine
import engine1.module_1c_personality_core as personality
import engine1.module_1d_3d_asset_pipeline as asset_pipeline
import engine2.module_2a_encrypted_storage as storage
import engine2.module_2b_token_minting as minting

logger = logging.getLogger(__name__)

TOKEN_2022_PROGRAM_ID = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string(
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1gdS"
)


# ── Burn helper ───────────────────────────────────────────────────────────────

def _build_burn_and_close_ixs(
    mint_pubkey: Pubkey,
    owner_pubkey: Pubkey,
):
    """
    Build instructions to:
      1. Burn the 1 token from the owner's ATA.
      2. Close the token account (reclaim rent).

    Returns list of Instructions.
    """
    from spl.token.instructions import (
        burn, BurnParams,
        close_account, CloseAccountParams,
        get_associated_token_address,
    )

    ata = get_associated_token_address(
        owner_pubkey, mint_pubkey,
        token_program_id=TOKEN_2022_PROGRAM_ID,
    )

    burn_ix = burn(BurnParams(
        program_id=TOKEN_2022_PROGRAM_ID,
        account=ata,
        mint=mint_pubkey,
        owner=owner_pubkey,
        amount=1,
        signers=[],
    ))

    close_ix = close_account(CloseAccountParams(
        program_id=TOKEN_2022_PROGRAM_ID,
        account=ata,
        dest=owner_pubkey,       # reclaim lamports to owner
        owner=owner_pubkey,
        signers=[],
    ))

    return [burn_ix, close_ix]


def _build_new_mint_ixs(
    new_mint_keypair: Keypair,
    owner_pubkey: Pubkey,
    new_metadata_uri: str,
    pet_id: str,
    new_phase: str,
):
    """
    Build instructions to create a fresh mint and mint 1 token.
    Returns list of Instructions + lamports needed.
    """
    from spl.token.instructions import (
        initialize_mint, InitializeMintParams,
        create_associated_token_account,
        mint_to, MintToParams,
        set_authority, SetAuthorityParams, AuthorityType,
    )
    from solders.system_program import create_account, CreateAccountParams

    client = minting.get_client()
    space    = minting._get_mint_space()
    lamports = client.get_minimum_balance_for_rent_exemption(space).value

    create_ix = create_account(CreateAccountParams(
        from_pubkey=owner_pubkey,
        to_pubkey=new_mint_keypair.pubkey(),
        lamports=lamports,
        space=space,
        owner=TOKEN_2022_PROGRAM_ID,
    ))

    init_ix = initialize_mint(InitializeMintParams(
        decimals=0,
        program_id=TOKEN_2022_PROGRAM_ID,
        mint=new_mint_keypair.pubkey(),
        mint_authority=owner_pubkey,
        freeze_authority=owner_pubkey,
    ))

    from spl.token.instructions import get_associated_token_address
    ata = get_associated_token_address(
        owner_pubkey, new_mint_keypair.pubkey(),
        token_program_id=TOKEN_2022_PROGRAM_ID,
    )

    create_ata_ix = create_associated_token_account(
        payer=owner_pubkey,
        owner=owner_pubkey,
        mint=new_mint_keypair.pubkey(),
        token_program_id=TOKEN_2022_PROGRAM_ID,
    )

    mint_to_ix = mint_to(MintToParams(
        program_id=TOKEN_2022_PROGRAM_ID,
        mint=new_mint_keypair.pubkey(),
        dest=ata,
        mint_authority=owner_pubkey,
        amount=1,
        signers=[],
    ))

    set_auth_ix = set_authority(SetAuthorityParams(
        program_id=TOKEN_2022_PROGRAM_ID,
        account=new_mint_keypair.pubkey(),
        authority=AuthorityType.MINT_TOKENS,
        current_authority=owner_pubkey,
        new_authority=None,
        signers=[],
    ))

    metadata_ix = minting._build_metaplex_create_metadata_ix(
        mint=new_mint_keypair.pubkey(),
        payer=owner_pubkey,
        name=f"D-pet [{new_phase}]",
        symbol="DPET",
        uri=new_metadata_uri,
    )

    return [create_ix, init_ix, create_ata_ix, mint_to_ix, set_auth_ix, metadata_ix]


# ── Public API ────────────────────────────────────────────────────────────────

def execute_evolution(pet_id: str, owner_keypair: Keypair) -> dict:
    """
    Orchestrate the full on-chain evolution:

      1. Verify evolution_pending == True (abort if not).
      2. Run personality rewrite (Module 1C) — determines new phase traits.
      3. Generate new 3-D model (Module 1D).
      4. Encrypt + upload new payload to IPFS (Module 2A).
      5. Build atomic burn-and-mint transaction.
      6. Send transaction to Devnet.
      7. On success: advance phase in Firebase, clear evolution_pending.

    Returns
    -------
    {
      "new_phase":       str,
      "new_mint_address": str,
      "ipfs_uri":        str,
      "tx_signature":    str,
      "evolution_message": str,
    }
    """
    # ── 1. Verify eligibility ─────────────────────────────────────────────────
    pet = state_machine.get_pet(pet_id)
    if pet is None:
        raise ValueError(f"Pet {pet_id} not found in Firebase.")

    if not pet.get("evolution_pending"):
        raise ValueError(
            f"Pet {pet_id} does not have evolution_pending=True. "
            "Cannot execute evolution."
        )

    current_phase = pet.get("phase", config.PHASES[0])
    phase_idx     = config.PHASES.index(current_phase) if current_phase in config.PHASES else 0
    new_phase     = config.PHASES[min(phase_idx + 1, len(config.PHASES) - 1)]

    logger.info("Evolution: pet %s %s → %s", pet_id, current_phase, new_phase)

    # ── 2. Personality rewrite ────────────────────────────────────────────────
    evo_result = personality.run_evolution_rewrite(pet_id, new_phase)
    new_traits  = evo_result["traits"]
    evo_message = evo_result["reply"]
    logger.info("Personality rewritten. Evolution message: %s", evo_message[:80])

    # ── 3. New 3-D model ──────────────────────────────────────────────────────
    evolution_count = pet.get("evolution_count", 0) + 1
    gltf_path, visual_traits = asset_pipeline.generate_pet_model(
        pet_id=pet_id,
        personality_traits=new_traits,
        phase=new_phase,
        version=evolution_count,
    )
    logger.info("New 3-D model: %s | visuals: %s", gltf_path, visual_traits)

    # ── 4. Upload to IPFS ─────────────────────────────────────────────────────
    # Refresh pet data (traits just updated by Module 1C)
    pet = state_machine.get_pet(pet_id)
    new_ipfs_uri = storage.build_and_upload_pet_payload(
        pet=pet,
        gltf_local_path=gltf_path,
        keypair=owner_keypair,
    )
    logger.info("New IPFS URI: %s", new_ipfs_uri)

    # ── 5 & 6. Atomic burn-and-mint ───────────────────────────────────────────
    client = minting.get_client()
    minting.airdrop_if_needed(client, owner_keypair.pubkey())

    old_mint_str = pet.get("mint_address")
    new_mint_keypair = Keypair()

    burn_ixs    = []
    if old_mint_str:
        old_mint_pubkey = Pubkey.from_string(old_mint_str)
        burn_ixs = _build_burn_and_close_ixs(old_mint_pubkey, owner_keypair.pubkey())
        logger.info("Burn instructions built for old mint: %s", old_mint_str)
    else:
        logger.warning("No old mint address found — skipping burn (first evolution).")

    mint_ixs = _build_new_mint_ixs(
        new_mint_keypair=new_mint_keypair,
        owner_pubkey=owner_keypair.pubkey(),
        new_metadata_uri=new_ipfs_uri,
        pet_id=pet_id,
        new_phase=new_phase,
    )

    # Combine into ONE atomic transaction
    all_ixs = burn_ixs + mint_ixs
    blockhash = client.get_latest_blockhash().value.blockhash

    from solders.transaction import Transaction as SoldersTransaction
    signers = [owner_keypair, new_mint_keypair]

    tx = SoldersTransaction.new_signed_with_payer(
        all_ixs,
        owner_keypair.pubkey(),
        signers,
        blockhash,
    )

    opts = TxOpts(skip_confirmation=False, preflight_commitment=Confirmed)
    result = client.send_raw_transaction(bytes(tx), opts=opts)
    tx_sig = result.value
    logger.info("Atomic burn+mint tx sent: %s", tx_sig)

    client.confirm_transaction(tx_sig, commitment=Confirmed)
    logger.info("Transaction confirmed.")

    # ── 7. Update Firebase ────────────────────────────────────────────────────
    new_phase_confirmed = state_machine.advance_phase(pet_id)     # clears evolution_pending
    state_machine.update_pet(pet_id, {
        "mint_address": str(new_mint_keypair.pubkey()),
        "ipfs_uri":     new_ipfs_uri,
    })

    logger.info(
        "Evolution complete. Pet %s is now in phase %s. Mint: %s",
        pet_id, new_phase_confirmed, new_mint_keypair.pubkey(),
    )

    return {
        "new_phase":         new_phase_confirmed,
        "new_mint_address":  str(new_mint_keypair.pubkey()),
        "ipfs_uri":          new_ipfs_uri,
        "tx_signature":      str(tx_sig),
        "evolution_message": evo_message,
    }
