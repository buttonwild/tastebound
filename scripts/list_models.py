"""List NVIDIA models available to your Nebius Token Factory key.

Usage: NEBIUS_API_KEY=... python scripts/list_models.py
Use the IDs printed here for TASTEBOUND_FAST_MODEL / TASTEBOUND_REASONING_MODEL.
"""

import os
import sys

from openai import OpenAI

if not os.environ.get("NEBIUS_API_KEY"):
    sys.exit("Set NEBIUS_API_KEY first.")

client = OpenAI(
    base_url=os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"),
    api_key=os.environ["NEBIUS_API_KEY"],
)
for model in sorted(m.id for m in client.models.list()):
    if "nvidia" in model.lower() or "nemotron" in model.lower():
        print(model)
