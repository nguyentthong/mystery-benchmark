"""Multimodal LLM client wrapping Anthropic, OpenAI, and Google.

All three providers accept base64-encoded images as part of the user message
content; the request format differs slightly between Anthropic-native and
OpenAI-compatible (used by both OpenAI and Google's OpenAI-compatible endpoint).

If no API client is available we fall back to a heuristic JSON response so
downstream code still runs in tests / CI without API keys.
"""
from __future__ import annotations

import base64
import json
import os


def _b64(image_bytes: bytes) -> str:
    return base64.standard_b64encode(image_bytes).decode("ascii")


class MultimodalClient:
    """Thin wrapper around (Anthropic | OpenAI | Google) multimodal APIs."""

    def __init__(self, provider: str = "anthropic", model: str = "claude-sonnet-4-20250514"):
        self.provider = provider
        self.model = model
        self._client = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        try:
            if self.provider == "anthropic":
                import anthropic
                self._client = anthropic.Anthropic()
            elif self.provider == "openai":
                import openai
                self._client = openai.OpenAI()
            elif self.provider == "google":
                import openai
                self._client = openai.OpenAI(
                    api_key=os.environ.get("GOOGLE_API_KEY", ""),
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                )
        except Exception:
            self._client = None

    def complete(
        self,
        system: str,
        user_text: str,
        images: list[bytes] | None = None,
        max_tokens: int = 4096,
    ) -> tuple[str, int]:
        """Send a multimodal completion. Returns (response_text, token_count).

        `images` is a list of PNG-encoded image bytes. They are appended as
        image content blocks before the text in the user message.
        """
        self._ensure_client()
        if self._client is None:
            return self._heuristic_response(), 0
        images = images or []
        try:
            if self.provider == "anthropic":
                content: list = []
                for img in images:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": _b64(img),
                        },
                    })
                content.append({"type": "text", "text": user_text})
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": content}],
                )
                text = resp.content[0].text
                tokens = resp.usage.input_tokens + resp.usage.output_tokens
                return text, tokens

            # OpenAI / Google (OpenAI-compatible)
            content = []
            for img in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_b64(img)}"},
                })
            content.append({"type": "text", "text": user_text})
            resp = self._client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            )
            text = resp.choices[0].message.content or ""
            tokens = resp.usage.total_tokens if resp.usage else 0
            return text, tokens
        except Exception as e:
            return json.dumps({
                "reasoning": f"API error: {e}",
                "beliefs": {},
                "action": "EXAMINE_LOCATION",
                "action_args": {},
            }), 0

    @staticmethod
    def _heuristic_response() -> str:
        return json.dumps({
            "reasoning": "No multimodal LLM available; heuristic fallback.",
            "beliefs": {
                "top_suspect": None, "suspect_confidence": 0.0,
                "top_weapon": None, "weapon_confidence": 0.0,
                "top_location": None, "location_confidence": 0.0,
                "eliminated_suspects": [], "new_facts": [],
            },
            "action": "EXAMINE_LOCATION",
            "action_args": {},
        })
