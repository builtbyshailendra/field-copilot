"""Versioned prompts, kept as files and reviewed like code.

A prompt file (YAML) holds the system prompt, the user template, and the settings that
go with it (model role, effort). Its version is a hash of that content, so every
recorded response, metric and eval result can name exactly which prompt produced it.

Customer packs can override a platform prompt by putting a file with the same id in
`packs/<tenant>/prompts/`. The override is a new version, measured like any other.

Templates use {{name}} placeholders, so JSON examples in prompts don't need escaping.
A missing variable is an error, never a silently empty string.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from fieldcopilot.llm.types import LLMRequest

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
PLATFORM_PROMPTS = Path(__file__).parent / "library"


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class Prompt:
    id: str
    description: str
    system: str
    user: str
    role: str = "strong"
    effort: str = "medium"
    max_tokens: int = 16000
    source: str = "platform"
    variables: tuple[str, ...] = field(default_factory=tuple)

    @property
    def version(self) -> str:
        blob = "\x1f".join([self.id, self.system, self.user, self.role, self.effort, str(self.max_tokens)])
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def render(self, **values: Any) -> tuple[str, str]:
        return _fill(self.system, values, self.id), _fill(self.user, values, self.id)

    def request(self, purpose: str | None = None, tenant: str | None = None, **values: Any) -> LLMRequest:
        system, user = self.render(**values)
        return LLMRequest(
            purpose=purpose or self.id, system=system, messages=[{"role": "user", "content": user}],
            role=self.role, effort=self.effort, max_tokens=self.max_tokens, tenant=tenant,
        )


def _fill(template: str, values: dict[str, Any], prompt_id: str) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise PromptError(f"prompt {prompt_id!r} needs {{{{{name}}}}}")
        return str(values[name])

    return PLACEHOLDER.sub(replace, template)


def load_prompt(path: Path, source: str) -> Prompt:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    missing = {"id", "description", "system", "user"} - set(data)
    if missing:
        raise PromptError(f"{path.name} is missing {sorted(missing)}")
    names = tuple(sorted(set(PLACEHOLDER.findall(data["system"]) + PLACEHOLDER.findall(data["user"]))))
    return Prompt(
        id=data["id"], description=data["description"], system=data["system"].strip(), user=data["user"].strip(),
        role=data.get("role", "strong"), effort=data.get("effort", "medium"),
        max_tokens=int(data.get("max_tokens", 16000)), source=source, variables=names,
    )


class PromptRegistry:
    def __init__(self, platform_dir: Path = PLATFORM_PROMPTS, pack_dirs: dict[str, Path] | None = None):
        self._platform = self._load_dir(platform_dir, "platform")
        self._packs = {tenant: self._load_dir(d, f"pack:{tenant}") for tenant, d in (pack_dirs or {}).items()}

    @staticmethod
    def _load_dir(directory: Path, source: str) -> dict[str, Prompt]:
        prompts: dict[str, Prompt] = {}
        if not directory.exists():
            return prompts
        for path in sorted(directory.glob("*.yaml")):
            prompt = load_prompt(path, source)
            if prompt.id in prompts:
                raise PromptError(f"duplicate prompt id {prompt.id!r} in {directory}")
            prompts[prompt.id] = prompt
        return prompts

    def get(self, prompt_id: str, tenant: str | None = None) -> Prompt:
        if tenant and prompt_id in self._packs.get(tenant, {}):
            return self._packs[tenant][prompt_id]
        if prompt_id in self._platform:
            return self._platform[prompt_id]
        raise PromptError(f"no prompt {prompt_id!r}")

    def catalogue(self) -> list[dict[str, str]]:
        rows = [{"id": p.id, "version": p.version, "source": p.source, "description": p.description}
                for p in self._platform.values()]
        for prompts in self._packs.values():
            rows += [{"id": p.id, "version": p.version, "source": p.source, "description": p.description}
                     for p in prompts.values()]
        return sorted(rows, key=lambda r: (r["id"], r["source"]))
