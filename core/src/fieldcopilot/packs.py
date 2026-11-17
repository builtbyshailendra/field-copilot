"""Customer packs: everything tenant-specific, loaded from packs/<tenant>/ (ADR 0001)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
PACKS_DIR = REPO / "packs"


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    kind: str
    root: Path
    config: dict[str, Any]

    @property
    def corpus_dir(self) -> Path:
        return self.root / self.config.get("retrieval", {}).get("corpus", "corpus")

    @property
    def prompts_dir(self) -> Path:
        return self.root / "prompts"

    @property
    def evals_dir(self) -> Path:
        return self.root / "evals"

    @property
    def top_k(self) -> int:
        return int(self.config.get("retrieval", {}).get("top_k", 5))

    @property
    def enabled_tools(self) -> list[str]:
        return list(self.config.get("tools", {}).get("enabled", []))

    @property
    def approver_role(self) -> str:
        return self.config.get("approvals", {}).get("approver_role", "approver")

    def budget(self, key: str, default: float) -> float:
        return float(self.config.get("budgets", {}).get(key, default))


@lru_cache(maxsize=None)
def load_pack(tenant: str, packs_dir: Path = PACKS_DIR) -> Pack:
    path = packs_dir / tenant / "tenant.yaml"
    if not path.exists():
        raise KeyError(f"no pack for tenant {tenant!r}")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("id") != tenant:
        raise ValueError(f"{path} declares id {config.get('id')!r}, expected {tenant!r}")
    return Pack(id=tenant, name=config["name"], kind=config["kind"], root=path.parent, config=config)


def all_packs(packs_dir: Path = PACKS_DIR) -> list[Pack]:
    return [load_pack(p.parent.name, packs_dir) for p in sorted(packs_dir.glob("*/tenant.yaml"))]
