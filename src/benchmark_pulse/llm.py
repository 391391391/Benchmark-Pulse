"""The single swap point between language-model providers.

Nothing else in the codebase imports a vendor SDK. That matters for three
reasons, in ascending order of importance:

  1. A free tier is enough to build on today, and the firm can move to Azure
     OpenAI inside its own tenant later by changing one environment variable.
  2. Every response is cached to disk, so a rehearsal costs no quota and the
     demo survives a dead conference-room connection.
  3. Because numbers are computed in metrics.py and directalpha.py and handed to
     the model as finished facts, the model is only ever asked to classify and
     to write prose. That is undemanding work, which is precisely why a free
     model is adequate here and a frontier model is not required.

Provider is chosen from the environment, in this order:
    GEMINI_API_KEY      -> gemini      (free tier, recommended)
    GROQ_API_KEY        -> groq        (free tier, fastest)
    ANTHROPIC_API_KEY   -> anthropic
    AZURE_OPENAI_*      -> azure_openai
    (none)              -> stub        (templates, no network)

Override with BENCHMARK_PULSE_LLM=<name>.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import LLM_AUDIT_LOG as AUDIT_LOG
from .paths import LLM_CACHE_DIR as CACHE_DIR
from .paths import PROJECT_ROOT as _PROJECT_ROOT


def _load_dotenv() -> None:
    """Read keys from a .env file beside the project.

    Preferred over machine environment variables because setx only affects new
    processes, which reliably confuses anyone who sets a key and then wonders
    why the already-running app has not noticed it.
    """
    env_file = _PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()

# The Gemini SDK logs an automatic-function-calling advisory on every single
# generate_content call. We do not use function calling, and a wall of identical
# warnings behind a live demo is worse than useless.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

#: Free tiers rate-limit aggressively. These waits are deliberately patient --
#: a demo that pauses four seconds is fine; one that raises mid-render is not.
_RETRY_WAITS = (2, 5, 12, 30)

#: Gemini charges internal reasoning against the output budget, so anything
#: tighter than this can return an empty response for a perfectly valid prompt.
_GEMINI_MIN_OUTPUT_TOKENS = 1200

#: Free-tier quotas are per model per day, and the headline models are tight --
#: gemini-3.6-flash allows 20 requests a day, which a single 19-holding run
#: exhausts. Lite models carry far larger allowances and are more than capable
#: of this workload, because the model here only classifies and writes prose
#: over figures the engine has already computed. On a daily-quota refusal the
#: client walks down this chain rather than failing, so the demo survives.
#: Verified callable on a new free-tier key, 13 Sep 2026.
_GEMINI_FALLBACK_CHAIN = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-3.6-flash",
)


class LLMError(RuntimeError):
    """A model call failed in a way the caller must handle explicitly."""


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    cached: bool = False
    raw: Any = field(default=None, repr=False)


def _cache_key(provider: str, model: str, system: str, prompt: str,
               schema: dict | None) -> str:
    blob = json.dumps(
        {"p": provider, "m": model, "s": system, "u": prompt, "j": schema},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response.

    Models wrap JSON in prose or fences no matter how firmly they are asked not
    to, and a free-tier model does it more often. Rather than fail the report
    over formatting, peel the common wrappers and try progressively harder.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise LLMError(f"no JSON object found in response: {text[:200]!r}")


def _unwrap_schema_echo(payload: dict, expected: dict | list) -> dict:
    """Undo a model echoing the schema's own wrapper back at us.

    Asked for an object matching a JSON Schema, a model will sometimes return
    ``{"properties": {...the actual answer...}}`` -- reproducing the shape of
    the schema rather than an instance of it. Left alone this silently loses
    every field, and the caller quietly degrades to a fallback without any
    obvious error. Observed with Gemini on roughly one call in twenty.
    """
    keys = set(expected if isinstance(expected, (list, set)) else expected.keys())
    if not keys or keys & payload.keys():
        return payload

    for wrapper in ("properties", "response", "result", "output", "data"):
        inner = payload.get(wrapper)
        if isinstance(inner, dict) and keys & inner.keys():
            return inner

    # A single unrecognised wrapper key around a dict that looks like the answer.
    if len(payload) == 1:
        only = next(iter(payload.values()))
        if isinstance(only, dict) and keys & only.keys():
            return only

    return payload


class LLMClient:
    """Provider-agnostic client. Construct once and reuse."""

    def __init__(self, provider: str | None = None, model: str | None = None,
                 use_cache: bool = True) -> None:
        self.provider = (provider or os.getenv("BENCHMARK_PULSE_LLM")
                         or self._detect_provider())
        self.model = model or self._default_model(self.provider)
        self.use_cache = use_cache
        self._client = None
        #: Set when a quota refusal forced a different model than configured, so
        #: the UI can be honest about which one actually answered.
        self.fallback_model: str | None = None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _detect_provider() -> str:
        if os.getenv("GEMINI_API_KEY"):
            return "gemini"
        if os.getenv("GROQ_API_KEY"):
            return "groq"
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY"):
            return "azure_openai"
        return "stub"

    @staticmethod
    def _default_model(provider: str) -> str:
        return {
            # Pinned rather than using the "-latest" alias: an alias can move
            # under a running build, and a benchmark rationale that changes
            # wording between the rehearsal and the demo is not worth the risk.
            # A lite model leads the chain because daily quota, not capability,
            # is the binding constraint here.
            "gemini": os.getenv("GEMINI_MODEL", _GEMINI_FALLBACK_CHAIN[0]),
            "groq": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
            "azure_openai": os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"),
            "stub": "template-stub",
        }.get(provider, "unknown")

    @property
    def is_live(self) -> bool:
        """False when running on templates, so the UI can say so honestly."""
        return self.provider != "stub"

    # -- public API ---------------------------------------------------------

    def complete(self, prompt: str, system: str = "", *,
                 max_tokens: int = 2000, temperature: float = 0.2) -> LLMResponse:
        """Free-text generation. Used for narrative prose only, never for maths."""
        return self._call(prompt, system, None, max_tokens, temperature)

    def structured(self, prompt: str, schema: dict, system: str = "", *,
                   max_tokens: int = 2000) -> dict:
        """Generation constrained to a JSON shape.

        Used for benchmark assignment, IPO scoring and news classification --
        the judgement calls. The schema is enforced where the provider supports
        it and repaired by extract_json where it does not.
        """
        fields = schema.get("properties", schema)
        instruction = (
            f"{prompt}\n\nRespond with a single JSON object with exactly these "
            f"keys at the top level: {', '.join(fields)}.\n"
            f"Types: {json.dumps(fields)}\n"
            "Do not nest them under any wrapper key. No prose, no code fences."
        )
        resp = self._call(instruction, system, schema, max_tokens, 0.0)
        return _unwrap_schema_echo(extract_json(resp.text), fields)

    # -- internals ----------------------------------------------------------

    def _call(self, prompt: str, system: str, schema: dict | None,
              max_tokens: int, temperature: float) -> LLMResponse:
        key = _cache_key(self.provider, self.model, system, prompt, schema)
        cache_file = CACHE_DIR / f"{key}.json"

        if self.use_cache and cache_file.exists():
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            return LLMResponse(text=payload["text"], provider=self.provider,
                               model=self.model, cached=True)

        last_error: Exception | None = None
        for attempt, wait in enumerate((0,) + _RETRY_WAITS):
            if wait:
                time.sleep(wait)
            try:
                text = self._dispatch(prompt, system, schema, max_tokens, temperature)
                break
            except Exception as exc:  # noqa: BLE001 - provider SDKs raise freely
                last_error = exc
                if not self._is_retryable(exc) or attempt == len(_RETRY_WAITS):
                    raise LLMError(
                        f"{self.provider}/{self.model} failed after "
                        f"{attempt + 1} attempt(s): {exc}"
                    ) from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise LLMError(str(last_error))

        if self.use_cache:
            cache_file.write_text(
                json.dumps({"text": text, "provider": self.provider,
                            "model": self.model}),
                encoding="utf-8",
            )
        self._audit(prompt, system, text)
        return LLMResponse(text=text, provider=self.provider, model=self.model)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Distinguish a momentary limit from an exhausted daily allowance.

        A per-minute rate limit clears on its own and is worth waiting out. A
        daily quota does not, and spending 49 seconds of backoff discovering
        that during a live demo is the worst possible time to find out.
        """
        marker = f"{type(exc).__name__} {exc}".lower()
        if "per day" in marker or "daily" in marker:
            return False
        return any(s in marker for s in (
            "rate", "429", "quota", "timeout", "timed out", "503", "502",
            "overloaded", "unavailable", "connection",
        ))

    def _audit(self, prompt: str, system: str, text: str) -> None:
        """Append-only record of every model call.

        The pitch claims a full audit trail; this is it. Prompts are hashed
        rather than stored in full so the log stays small and carries no
        portfolio detail.
        """
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": self.provider,
            "model": self.model,
            "prompt_sha256": hashlib.sha256(
                (system + prompt).encode("utf-8")).hexdigest()[:16],
            "prompt_chars": len(prompt),
            "response_chars": len(text),
        }
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _dispatch(self, prompt: str, system: str, schema: dict | None,
                  max_tokens: int, temperature: float) -> str:
        handler = getattr(self, f"_call_{self.provider}", None)
        if handler is None:
            raise LLMError(f"unknown provider {self.provider!r}")
        return handler(prompt, system, schema, max_tokens, temperature)

    def _call_gemini(self, prompt, system, schema, max_tokens, temperature) -> str:
        from google import genai
        from google.genai import types

        if self._client is None:
            self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

        config = types.GenerateContentConfig(
            system_instruction=system or None,
            # Current Gemini models spend internal reasoning tokens from this
            # same budget, so a tight cap is consumed entirely by thinking and
            # the call returns successfully with no text at all. The floor
            # leaves room for both.
            max_output_tokens=max(max_tokens, _GEMINI_MIN_OUTPUT_TOKENS),
            temperature=temperature,
            response_mime_type="application/json" if schema else "text/plain",
        )
        # Walk the chain on a daily-quota refusal. Starting from the configured
        # model means a healthy quota never pays for this, and an exhausted one
        # degrades to a slightly smaller model instead of to nothing.
        chain = [self.model] + [m for m in _GEMINI_FALLBACK_CHAIN if m != self.model]
        resp = None
        exhausted: list[str] = []

        for candidate in chain:
            try:
                resp = self._client.models.generate_content(
                    model=candidate, contents=prompt, config=config
                )
            except Exception as exc:  # noqa: BLE001 - provider SDK raises broadly
                message = str(exc)
                if "429" in message or "RESOURCE_EXHAUSTED" in message:
                    exhausted.append(candidate)
                    continue
                raise
            if candidate != self.model:
                self.fallback_model = candidate
            break

        if resp is None:
            raise LLMError(
                "every Gemini model is out of free-tier quota for today "
                f"({', '.join(exhausted)}). Quotas reset on a rolling daily "
                "basis; cached responses still work, and BENCHMARK_PULSE_LLM=stub "
                "runs the full pipeline without a model."
            )

        if not resp.text:
            reasons = [
                str(getattr(c, "finish_reason", "unknown"))
                for c in (resp.candidates or [])
            ] or ["no candidates returned"]
            raise LLMError(
                f"gemini returned no text (finish_reason: {', '.join(reasons)}). "
                "A MAX_TOKENS reason means the reasoning budget consumed the "
                "whole allowance -- raise max_tokens."
            )
        return resp.text

    def _call_groq(self, prompt, system, schema, max_tokens, temperature) -> str:
        from groq import Groq

        if self._client is None:
            self._client = Groq(api_key=os.environ["GROQ_API_KEY"])

        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        resp = self._client.chat.completions.create(
            model=self.model, messages=messages,
            max_tokens=max_tokens, temperature=temperature,
            response_format={"type": "json_object"} if schema else None,
        )
        return resp.choices[0].message.content or ""

    def _call_anthropic(self, prompt, system, schema, max_tokens, temperature) -> str:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        resp = self._client.messages.create(
            model=self.model, max_tokens=max_tokens, temperature=temperature,
            system=system or anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    def _call_azure_openai(self, prompt, system, schema, max_tokens, temperature) -> str:
        from openai import AzureOpenAI

        if self._client is None:
            self._client = AzureOpenAI(
                api_key=os.environ["AZURE_OPENAI_API_KEY"],
                azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        resp = self._client.chat.completions.create(
            model=self.model, messages=messages,
            max_tokens=max_tokens, temperature=temperature,
            response_format={"type": "json_object"} if schema else None,
        )
        return resp.choices[0].message.content or ""

    def _call_stub(self, prompt, system, schema, max_tokens, temperature) -> str:
        """Deterministic output with no network call.

        Not a mock for tests -- a real fallback. It lets the whole pipeline run
        end to end with no credential at all, and it is the safety net if the
        provider is rate-limited or offline mid-demo. Output is obviously
        templated so nobody mistakes it for generated analysis.
        """
        if schema:
            return json.dumps(_stub_for_schema(schema))
        return (
            "[TEMPLATE OUTPUT -- no language model configured]\n\n"
            "The figures in this section were computed by the analytics engine "
            "and are correct. The surrounding commentary is a placeholder: set "
            "GEMINI_API_KEY (or another provider key) to generate written "
            "analysis over these facts."
        )


def _stub_for_schema(schema: dict) -> dict:
    """Build a shape-correct placeholder so downstream parsing still succeeds."""
    props = schema.get("properties", schema)
    out: dict = {}
    for name, spec in props.items():
        kind = spec.get("type", "string") if isinstance(spec, dict) else "string"
        if kind == "number" or kind == "integer":
            out[name] = 0
        elif kind == "boolean":
            out[name] = False
        elif kind == "array":
            out[name] = []
        elif kind == "object":
            out[name] = _stub_for_schema(spec)
        else:
            out[name] = "[not generated - no model configured]"
    return out


_default_client: LLMClient | None = None


def get_client() -> LLMClient:
    """Process-wide client, so the provider is detected once."""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
