"""User-editable settings. Everything here is editable from the UI, not just env vars."""

import os
from typing import Literal

from pydantic import BaseModel, Field

# Where Ollama lives on first run. docker-compose sets this to http://ollama:11434. Once settings are saved
# (from the UI) the saved values win.
OLLAMA_URL = os.environ.get("LORAPEDIA_OLLAMA_URL", "http://localhost:11434").rstrip("/")

DEFAULT_SYSTEM_PROMPT = (
    "You answer questions using ONLY the provided context. Be factual and direct. "
    "No preamble, no markdown, no emoji, plain ASCII where possible. "
    "If the context does not support an answer, reply exactly: not found."
)


class Settings(BaseModel):
    # Radio connection. "auto" uses a single USB device if one is plugged in, else the simulator.
    radio_method: Literal["auto", "simulator", "serial", "tcp", "ble"] = "auto"
    radio_address: str = ""  # serial port path, TCP host, or BLE address; empty = auto-detect

    # Trigger. LoRaPedia answers DIRECT MESSAGES ONLY and never posts to a public channel: that is not a setting. (An older settings
    # file may still hold trigger_mode / channel_index; they are ignored.) The prefixes are optional in a direct message and are removed
    # when present ("?what is a quasar", "!wiki quasar").
    trigger_prefixes: list[str] = ["?", "!wiki"]
    hop_limit: int = Field(3, ge=1, le=7)
    allowlist: list[str] = []  # empty = everyone allowed
    blocklist: list[str] = []

    # Byte budget / multi-part
    byte_limit: int = Field(200, ge=50, le=233)
    multipart_enabled: bool = True  # split long answers into "(i/n)" parts when needed
    max_parts: int = Field(3, ge=1, le=9)  # admin cap on parts per answer
    auto_send_parts: bool = True  # False: send part 1, rest on "?more"
    send_delay_s: float = Field(5.0, ge=0, le=120)  # airtime courtesy between sends
    # When the whole library is indexed and still has nothing, answer from the model's memory, marked as a guess.
    ai_fallback: bool = True

    # How the web interface shows temperatures and distances. The data itself is always kept in Celsius and kilometres.
    temperature_unit: Literal["C", "F"] = "C"
    distance_unit: Literal["km", "mi"] = "km"

    # Mesh AI. Questions about the mesh itself (nodes nearby, batteries, sensors...) are answered from mesh.db by fixed lookups, not by the model.
    mesh_questions: bool = True
    # Follow-up questions: each person's last few exchanges are kept in memory (never on disk) this long after their last question. 0 = off.
    memory_hours: int = Field(24, ge=0, le=168)

    # What the radio hears of the rest of the mesh (nodes, positions, telemetry, signal), kept in mesh.db. Never message content.
    mesh_record: bool = True
    mesh_retention_days: int = Field(30, ge=1, le=365)

    # Backups of the settings and question log (see backups.py)
    auto_backup: bool = True  # one a day while running
    backup_keep: int = Field(7, ge=1, le=60)  # how many automatic ones to keep

    # Rate limiting / queue
    queue_when_limited: bool = True  # hold a too-soon question and answer it on its turn, not reply "busy"
    max_queued_per_node: int = Field(3, ge=1, le=10)  # most questions one node may have waiting
    ack_queued: bool = True  # send a short "queued, answer in ~Ns" so the asker knows it was received
    rate_limit_queries: int = Field(1, ge=0)
    rate_limit_window_s: int = Field(60, ge=1)
    queue_max: int = Field(20, ge=1)

    # Updates (Settings -> Updates). The checks are the only things LoRaPedia does online without being asked.
    update_channel: Literal["stable", "beta"] = "stable"  # "beta": also receive pre-releases (vX.Y.Z-beta.N) from the beta branch
    update_check: bool = True           # look for a new LoRaPedia release on GitHub about once a day
    auto_update: bool = True            # apply it when the node is quiet (Docker installs with the updater only)
    wikipedia_update_check: bool = True    # look for a newer edition of each installed Wikipedia package
    wikipedia_auto_update: bool = True     # download and index it when the disk allows and the node is quiet

    # Models. Defaults assume a local Ollama; any OpenAI-compatible server works for the LLM.
    llm_backend: Literal["openai", "mock"] = "openai"  # "mock" = no model, for demos/UI testing
    llm_base_url: str = f"{OLLAMA_URL}/v1"
    llm_model: str = "llama3.2:3b"
    embed_backend: Literal["ollama", "hash"] = "ollama"  # "hash" = no model, keyword-style vectors
    embed_base_url: str = OLLAMA_URL
    embed_model: str = "nomic-embed-text"

    # Generation
    top_k: int = Field(4, ge=1, le=20)
    temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(160, ge=16, le=1024)
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @property
    def effective_max_parts(self) -> int:
        return self.max_parts if self.multipart_enabled else 1
