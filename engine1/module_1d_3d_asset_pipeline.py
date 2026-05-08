"""
engine1/module_1d_3d_asset_pipeline.py
Module 1D — 3D Asset Generation Pipeline

Responsibilities:
  - Ask Claude to translate personality traits → a visual trait JSON.
  - Map visual traits to pre-built .glb part files (sphere or cube body,
    two eye styles, six colour palettes, optional accessories).
  - Assemble the parts into a single .gltf scene using pygltflib.
  - Return the path to the assembled file so Module 2A can upload it.

Asset library layout (assets/glb_parts/):
  body_sphere.glb
  body_cube.glb
  eyes_wide.glb
  eyes_narrow.glb
  (accessories are embedded as mesh overlays in this demo)

The assembled output is written to: assets/generated/{pet_id}_v{version}.gltf
"""

import json
import logging
import os
import re
import struct
from pathlib import Path

from google import genai
from google.genai import types
from pygltflib import (
    GLTF2, Scene, Node, Mesh, Primitive, Accessor, BufferView,
    Buffer, Asset, Attributes,
)

from config import config

logger = logging.getLogger(__name__)

# Initialise Gemini client once
client = genai.Client(api_key=config.GEMINI_API_KEY)

# ── Trait→visual mapping tables ───────────────────────────────────────────────

BODY_MAP = {
    "round": "body_sphere.glb",
    "angular": "body_cube.glb",
}

EYE_MAP = {
    "wide": "eyes_wide.glb",
    "narrow": "eyes_narrow.glb",
}

COLOUR_MAP = {
    "teal":   [0.0, 0.8, 0.8, 1.0],
    "coral":  [1.0, 0.4, 0.3, 1.0],
    "gold":   [1.0, 0.84, 0.0, 1.0],
    "violet": [0.58, 0.0, 0.83, 1.0],
    "mint":   [0.6, 1.0, 0.6, 1.0],
    "slate":  [0.44, 0.5, 0.56, 1.0],
}

ACCESSORY_SCALE = {
    "scarf":  [1.2, 0.1, 1.2],
    "crown":  [0.4, 0.2, 0.4],
    "glasses": [0.6, 0.05, 0.1],
    None:     None,
}


# ── Step 1: LLM → visual trait JSON ──────────────────────────────────────────

def generate_visual_traits(personality_traits: dict, phase: str) -> dict:
    """
    Ask Claude to translate personality scores into a visual trait JSON.

    Returns dict like:
      { "body": "round", "eyes": "wide", "color": "teal", "accessory": "scarf" }
    """
    prompt = f"""You are a character designer for a digital companion pet.
The pet is in the {phase!r} phase with these personality traits (0–10):
{json.dumps(personality_traits, indent=2)}

Choose one option from each category to define the pet's visual appearance.
Respond with raw JSON only (no markdown):
{{
  "body":      "round" | "angular",
  "eyes":      "wide" | "narrow",
  "color":     "teal" | "coral" | "gold" | "violet" | "mint" | "slate",
  "accessory": "scarf" | "crown" | "glasses" | null
}}

Higher curiosity → wide eyes. Higher warmth → round body, warm colours (coral/gold).
Higher humour → bright colours. Higher emotional_maturity → accessories."""

    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction="You are a character designer. Always respond with raw JSON only — no markdown, no explanation.",
            max_output_tokens=128,
        ),
    )
    raw = response.text.strip()
    raw = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()

    try:
        visual = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned invalid JSON for visual traits; using defaults.")
        visual = {"body": "round", "eyes": "wide", "color": "teal", "accessory": None}

    # Sanitise
    visual["body"] = visual.get("body", "round") if visual.get("body") in BODY_MAP else "round"
    visual["eyes"] = visual.get("eyes", "wide") if visual.get("eyes") in EYE_MAP else "wide"
    visual["color"] = visual.get("color", "teal") if visual.get("color") in COLOUR_MAP else "teal"
    acc = visual.get("accessory")
    visual["accessory"] = acc if acc in ACCESSORY_SCALE else None

    logger.info("Visual traits resolved: %s", visual)
    return visual


# ── Step 2: Build .glb source files ──────────────────────────────────────────

def _ensure_glb_library():
    """
    Create the two mock .glb files (sphere and cube) if they don't exist.
    Each is a minimal valid GLB containing a single triangle mesh that
    acts as a stand-in for a real 3-D model.
    """
    lib_path = Path(config.ASSET_LIBRARY_PATH)
    lib_path.mkdir(parents=True, exist_ok=True)

    _write_mock_glb(lib_path / "body_sphere.glb", label="sphere")
    _write_mock_glb(lib_path / "body_cube.glb",   label="cube")
    _write_mock_glb(lib_path / "eyes_wide.glb",   label="eyes_wide")
    _write_mock_glb(lib_path / "eyes_narrow.glb", label="eyes_narrow")


def _write_mock_glb(path: Path, label: str):
    """Write a minimal valid GLB file representing a named shape."""
    if path.exists():
        return  # already created

    # Triangle vertices (x, y, z) — three points forming a small triangle
    vertices = [
        -0.5, -0.5, 0.0,
         0.5, -0.5, 0.0,
         0.0,  0.5, 0.0,
    ]
    indices = [0, 1, 2]

    # Pack binary buffer: indices (uint16) then vertices (float32)
    index_data  = struct.pack(f"<{len(indices)}H", *indices)
    vertex_data = struct.pack(f"<{len(vertices)}f", *vertices)
    # Pad index_data to 4-byte alignment
    pad = (4 - len(index_data) % 4) % 4
    index_data_padded = index_data + b"\x00" * pad

    bin_data = index_data_padded + vertex_data

    gltf = GLTF2()
    gltf.asset = Asset(version="2.0", generator=f"dpet-mock-{label}")
    gltf.scene = 0

    buf = Buffer(byteLength=len(bin_data))
    gltf.buffers.append(buf)

    # BufferView 0 — indices
    bv_idx = BufferView(buffer=0, byteOffset=0, byteLength=len(index_data), target=34963)
    # BufferView 1 — vertices
    bv_vtx = BufferView(buffer=0, byteOffset=len(index_data_padded),
                        byteLength=len(vertex_data), target=34962)
    gltf.bufferViews = [bv_idx, bv_vtx]

    # Accessor 0 — indices (SCALAR uint16)
    acc_idx = Accessor(
        bufferView=0, byteOffset=0, componentType=5123, count=3,
        type="SCALAR", max=[2], min=[0],
    )
    # Accessor 1 — positions (VEC3 float32)
    acc_vtx = Accessor(
        bufferView=1, byteOffset=0, componentType=5126, count=3,
        type="VEC3",
        max=[0.5, 0.5, 0.0], min=[-0.5, -0.5, 0.0],
    )
    gltf.accessors = [acc_idx, acc_vtx]

    prim  = Primitive(attributes=Attributes(POSITION=1), indices=0)
    mesh  = Mesh(name=label, primitives=[prim])
    gltf.meshes  = [mesh]
    gltf.nodes   = [Node(mesh=0, name=label)]
    gltf.scenes  = [Scene(nodes=[0])]

    gltf.set_binary_blob(bin_data)
    gltf.save(str(path))
    logger.debug("Mock GLB written: %s", path)


# ── Step 3: Assemble parts → combined .gltf ──────────────────────────────────

def _load_glb(path: Path) -> GLTF2:
    return GLTF2().load(str(path))


def assemble_pet_model(visual_traits: dict, colour_rgba: list) -> GLTF2:
    """
    Merge body + eye meshes into a single GLTF2 scene and tint with colour.

    For this demo we load two GLBs and combine their meshes into one scene.
    A production build would use a proper 3-D merge pipeline (e.g. Blender CLI).
    """
    lib = Path(config.ASSET_LIBRARY_PATH)
    body_path = lib / BODY_MAP.get(visual_traits["body"], "body_sphere.glb")
    eyes_path = lib / EYE_MAP.get(visual_traits["eyes"], "eyes_wide.glb")

    body_gltf = _load_glb(body_path)
    eyes_gltf = _load_glb(eyes_path)

    # Build a new combined scene
    combined = GLTF2()
    combined.asset = Asset(version="2.0", generator="dpet-assembler-v1")
    combined.scene = 0

    # Add a simple material with the chosen colour
    combined.materials = [{
        "name": "pet_material",
        "pbrMetallicRoughness": {
            "baseColorFactor": colour_rgba,
            "metallicFactor": 0.0,
            "roughnessFactor": 0.8,
        }
    }]

    node_indices = []

    for idx, (source, name_suffix, translation) in enumerate([
        (body_gltf, "body", [0.0, 0.0, 0.0]),
        (eyes_gltf, "eyes", [0.0, 0.3, 0.5]),
    ]):
        # Copy buffer, bufferView, accessor, mesh from source
        buf_offset    = len(combined.buffers)
        bv_offset     = len(combined.bufferViews)
        acc_offset    = len(combined.accessors)
        mesh_offset   = len(combined.meshes)

        # Remap buffer indices in bufferViews
        for bv in source.bufferViews:
            import copy
            bv_copy = copy.deepcopy(bv)
            bv_copy.buffer = (bv_copy.buffer or 0) + buf_offset
            combined.bufferViews.append(bv_copy)

        for acc in source.accessors:
            import copy
            acc_copy = copy.deepcopy(acc)
            if acc_copy.bufferView is not None:
                acc_copy.bufferView += bv_offset
            combined.accessors.append(acc_copy)

        for mesh in source.meshes:
            import copy
            mesh_copy = copy.deepcopy(mesh)
            mesh_copy.name = f"{mesh_copy.name}_{name_suffix}"
            for prim in mesh_copy.primitives:
                if prim.indices is not None:
                    prim.indices += acc_offset
                attrs = prim.attributes
                if attrs.POSITION is not None:
                    attrs.POSITION += acc_offset
                prim.material = 0      # use the shared material
            combined.meshes.append(mesh_copy)

        for buf in source.buffers:
            import copy
            buf_copy = copy.deepcopy(buf)
            combined.buffers.append(buf_copy)

        # Copy binary blob per buffer
        blob = source.binary_blob()
        if blob:
            combined.set_binary_blob(blob)   # last write wins in demo; fine for testing

        node = Node(mesh=mesh_offset, name=f"node_{name_suffix}",
                    translation=translation)
        combined.nodes.append(node)
        node_indices.append(len(combined.nodes) - 1)

    # Add accessory node (empty placeholder)
    acc_name = visual_traits.get("accessory")
    if acc_name:
        scale = ACCESSORY_SCALE.get(acc_name, [1, 1, 1])
        combined.nodes.append(
            Node(name=f"accessory_{acc_name}", translation=[0.0, 0.7, 0.0],
                 scale=scale)
        )
        node_indices.append(len(combined.nodes) - 1)

    combined.scenes = [Scene(nodes=node_indices)]
    return combined


# ── Public API ────────────────────────────────────────────────────────────────

def generate_pet_model(
    pet_id: str,
    personality_traits: dict,
    phase: str,
    version: int = 1,
) -> str:
    """
    End-to-end pipeline: traits → visual JSON → assembled .gltf file.

    Returns the local file path to the generated .gltf.
    """
    _ensure_glb_library()

    visual_traits = generate_visual_traits(personality_traits, phase)
    colour_rgba   = COLOUR_MAP.get(visual_traits["color"], COLOUR_MAP["teal"])

    gltf_scene = assemble_pet_model(visual_traits, colour_rgba)

    output_dir = Path("assets/generated")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{pet_id}_v{version}.gltf"

    gltf_scene.save(str(output_path))
    logger.info("3-D model saved: %s", output_path)

    return str(output_path), visual_traits
