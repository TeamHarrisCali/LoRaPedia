"""User-editable settings. Everything here is editable from the UI, not just env vars."""

from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_SYSTEM_PROMPT = (
    "You answer questions using ONLY the provided context. Be factual and direct. "
    "No preamble, no markdown, no emoji, plain ASCII where possible. "
    "If the context does not support an answer, reply exactly: not found."
)


class Settings(BaseModel):
    # Radio connection. "auto" uses a single USB device if one is plugged in, else the simulator.
    radio_method: Literal["auto", "simulator", "serial", "tcp", "ble"] = "auto"
    radio_address: str = ""  # serial port path, TCP host, or BLE address; empty = auto-detect

    # Trigger
    trigger_mode: Literal["dm", "channel", "both"] = "dm"
    trigger_prefixes: list[str] = ["?", "!wiki"]
    channel_index: int = Field(0, ge=0, le=7)
    hop_limit: int = Field(3, ge=1, le=7)
    allowlist: list[str] = []  # empty = everyone allowed
    blocklist: list[str] = []

    # Byte budget / multi-part
    byte_limit: int = Field(200, ge=50, le=233)
    multipart_enabled: bool = True  # split long answers into "(i/n)" parts when needed
    max_parts: int = Field(3, ge=1, le=9)  # admin cap on parts per answer
    auto_send_parts: bool = True  # False: send part 1, rest on "?more"
    send_delay_s: float = Field(5.0, ge=0, le=120)  # airtime courtesy between sends
    source_tags: bool = True

    # Rate limiting / queue
    rate_limit_queries: int = Field(1, ge=0)
    rate_limit_window_s: int = Field(60, ge=1)
    queue_max: int = Field(20, ge=1)

    # Models. Defaults assume a local Ollama; any OpenAI-compatible server works for the LLM.
    llm_backend: Literal["openai", "mock"] = "openai"  # "mock" = no model, for demos/UI testing
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "llama3.2:3b"
    embed_backend: Literal["ollama", "hash"] = "ollama"  # "hash" = no model, keyword-style vectors
    embed_base_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"

    # Generation
    top_k: int = Field(4, ge=1, le=20)
    temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(160, ge=16, le=1024)
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @property
    def effective_max_parts(self) -> int:
        return self.max_parts if self.multipart_enabled else 1
