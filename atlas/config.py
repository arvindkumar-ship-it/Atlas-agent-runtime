from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class Settings(BaseModel):
    database_url: str = "sqlite:///./data/atlas.db"
    data_dir: Path = Path("data")
    repo_root: Path = Path("repositories")
    api_key: str = ""
    environment: str = "development"
    backend: str = "demo"
    model_url: str = "http://127.0.0.1:8080/v1"
    model_name: str = "local-model"
    model_api_key: str = ""
    small_model_name: str = ""
    sandbox: str = "docker"
    sandbox_image: str = "atlas-sandbox:1.0"
    allow_trusted_local: bool = False
    embedded_worker: bool = True
    worker_concurrency: int = Field(default=2, ge=1, le=32)
    worker_metrics_port: int = Field(default=9091, ge=0, le=65535)
    inference_concurrency: int = Field(default=2, ge=1, le=32)
    queue_size: int = Field(default=128, ge=1)
    lease_seconds: int = Field(default=45, ge=6)
    model_timeout: int = Field(default=120, ge=1)
    test_timeout: int = Field(default=45, ge=1)
    kv_budget_bytes: int = Field(default=2 * 1024**3, ge=1024)
    kv_layers: int = Field(default=32, ge=1)
    kv_heads: int = Field(default=8, ge=1)
    kv_head_dim: int = Field(default=128, ge=1)
    cors_origins: list[str] = Field(default_factory=list)
    otlp_endpoint: str = ""

    @model_validator(mode="after")
    def validate_mode(self):
        if self.backend not in {"demo", "compatible", "vllm", "llamacpp"}:
            raise ValueError("unsupported model backend")
        if self.sandbox not in {"docker", "local"}:
            raise ValueError("sandbox must be docker or local")
        if self.environment == "production" and len(self.api_key) < 32:
            raise ValueError("production needs ATLAS_API_KEY with at least 32 characters")
        if self.sandbox == "local" and not self.allow_trusted_local:
            raise ValueError("local execution needs ATLAS_ALLOW_TRUSTED_LOCAL=true")
        return self

    @classmethod
    def from_env(cls) -> Settings:
        values = {}
        for name, field in cls.model_fields.items():
            raw = os.environ.get(f"ATLAS_{name.upper()}")
            if raw is None:
                continue
            if field.annotation is bool:
                values[name] = raw.lower() in {"true", "1", "yes"}
            elif name == "cors_origins":
                values[name] = [value.strip() for value in raw.split(",") if value.strip()]
            else:
                values[name] = raw
        return cls.model_validate(values)
