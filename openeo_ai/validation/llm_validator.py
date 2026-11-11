"""Claude SDK integration for process graph validation diagnostics."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from openeo_ai import budget
from openeo_ai.sdk.user_context import get_current_user_id
from openeo_ai.validation.graph_validator import LLMValidationResult, LLMIssue

logger = logging.getLogger(__name__)


@dataclass
class LLMValidationConfig:
    enabled: bool = False
    provider: str = "claude"
    model: str = "claude-opus-5-5"
    max_tokens: int = 16000
    timeout_seconds: int = 20
    api_key: Optional[str] = None

    @classmethod
    def from_env(cls) -> "LLMValidationConfig":
        def _bool_env(name: str, default: bool = False) -> bool:
            val = os.getenv(name)
            if val is None:
                return default
            return str(val).lower() in {"1", "true", "yes", "on"}

        api_key = os.getenv("OPENEO_CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY")

        return cls(
            enabled=_bool_env("OPENEO_LLM_VALIDATION_ENABLED", False),
            provider=os.getenv("OPENEO_LLM_PROVIDER", "claude"),
            model=os.getenv("OPENEO_LLM_MODEL", "claude-opus-5-5"),
            max_tokens=int(os.getenv("OPENEO_LLM_MAX_TOKENS", "16000")),
            timeout_seconds=int(os.getenv("OPENEO_LLM_TIMEOUT_SECONDS", "20")),
            api_key=api_key,
        )


class ClaudeProcessGraphValidator:
    """Validate process graphs using Claude SDK (optional advisory layer)."""

    def __init__(self, config: LLMValidationConfig):
        self.config = config

    @classmethod
    def from_env(cls) -> "ClaudeProcessGraphValidator":
        return cls(LLMValidationConfig.from_env())

    def validate(self, graph_summary: Dict[str, Any], process_specs: Dict[str, Any]) -> LLMValidationResult:
        if not self.config.enabled:
            return LLMValidationResult(reason="LLM validation disabled")
        if self.config.provider.lower() != "claude":
            return LLMValidationResult(reason=f"Unsupported LLM provider: {self.config.provider}")
        if not self.config.api_key:
            return LLMValidationResult(reason="Missing Claude API key")

        try:
            import anthropic
        except Exception as e:
            return LLMValidationResult(reason=f"Claude SDK not available: {e}")

        prompt = self._build_prompt(graph_summary, process_specs)

        try:
            budget.record_llm_call(get_current_user_id(), "validator", self.config.model)
        except Exception:  # noqa: BLE001
            logger.debug("[budget] validator tag failed", exc_info=True)

        try:
            client = anthropic.Anthropic(api_key=self.config.api_key)
            response = client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=(
                    "You are an expert OpenEO process-graph validator. "
                    "Return strict JSON only."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            text = self._extract_text(response)
            return self._parse_response(text)
        except Exception as e:
            logger.warning(f"Claude validation failed: {e}")
            return LLMValidationResult(reason=str(e))

    def _build_prompt(self, graph_summary: Dict[str, Any], process_specs: Dict[str, Any]) -> str:
        payload = {
            "task": "Validate and review the OpenEO process graph for chaining errors, dimension mismatches, missing steps, and risky patterns. Provide suggestions for corrections.",
            "graph_summary": graph_summary,
            "process_specs": process_specs,
            "response_schema": {
                "errors": [{"code": "string", "message": "string", "node_id": "string?", "suggestion": "string?"}],
                "warnings": [{"code": "string", "message": "string", "node_id": "string?", "suggestion": "string?"}],
                "suggestions": [{"code": "string", "message": "string", "node_id": "string?", "suggestion": "string?"}],
                "patches": [
                    {"op": "string", "node_id": "string", "payload": "object"}
                ],
            },
        }
        return json.dumps(payload, ensure_ascii=True)

    def _extract_text(self, response: Any) -> str:
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        parts.append(block["text"])
                    elif hasattr(block, "text"):
                        parts.append(block.text)
                return "\n".join(parts)
        if hasattr(response, "completion"):
            return response.completion
        return str(response)

    def _parse_response(self, text: str) -> LLMValidationResult:
        try:
            data = json.loads(text)
        except Exception:
            # Attempt to salvage by locating first JSON object in response
            try:
                start = text.find("{")
                end = text.rfind("}")
                if start >= 0 and end > start:
                    data = json.loads(text[start:end + 1])
                else:
                    return LLMValidationResult(raw=text, reason="Invalid JSON response")
            except Exception:
                return LLMValidationResult(raw=text, reason="Invalid JSON response")

        return LLMValidationResult(
            errors=self._parse_issues(data.get("errors", [])),
            warnings=self._parse_issues(data.get("warnings", [])),
            suggestions=self._parse_issues(data.get("suggestions", [])),
            patches=data.get("patches", []) if isinstance(data.get("patches", []), list) else [],
            raw=text,
        )

    def _parse_issues(self, raw: Any) -> list[LLMIssue]:
        issues: list[LLMIssue] = []
        if not isinstance(raw, list):
            return issues
        for item in raw:
            if not isinstance(item, dict):
                continue
            issues.append(
                LLMIssue(
                    code=str(item.get("code", "LLM")),
                    message=str(item.get("message", "")),
                    node_id=item.get("node_id"),
                    suggestion=item.get("suggestion"),
                )
            )
        return issues
