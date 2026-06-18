"""Diagnostics shared by refactor analysis and edit planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RefactorDiagnostic:
    """A diagnostic emitted while analyzing a refactor candidate."""

    code: str
    message: str
    severity: str = "warning"

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
