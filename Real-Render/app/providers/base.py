from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    ok: bool
    data: dict[str, Any]
    cost_usd: float = 0.0


class ReconstructionProvider(Protocol):
    name: str

    def reconstruct(
        self,
        *,
        job_id: str,
        input_dir: str,
        outputs_dir: str,
        options: dict[str, Any],
    ) -> ProviderResult: ...


class VideoProvider(Protocol):
    name: str

    def make_walkthrough(
        self,
        *,
        job_id: str,
        outputs_dir: str,
        options: dict[str, Any],
        reconstruction: dict[str, Any] | None,
        input_dir: str = "",
    ) -> ProviderResult: ...



