"""Bundled llama.cpp server: spawn, constrained chat, guaranteed teardown.

Qwen3 runs with thinking disabled: llama.cpp ignores grammars while reasoning
is active, which would break the JSON-schema responses this pipeline needs.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests

from meeting_host import config, models

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LlmUnavailable(RuntimeError):
    """Raised when the local model cannot be used; callers must degrade."""


class LlamaServer:
    def __init__(
        self,
        *,
        model_path: Path,
        port: int = config.LLM_PORT,
        base_dir: Path | None = None,
    ) -> None:
        self._model_path = model_path
        self._port = port
        self._base_dir = base_dir
        self._process: subprocess.Popen | None = None
        self._base_url = f"http://127.0.0.1:{port}"

    def __enter__(self) -> "LlamaServer":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def start(self) -> None:
        binary = models.llama_server_path(self._base_dir)
        if binary is None:
            raise LlmUnavailable("llama-server binary is missing.")
        if not self._model_path.is_file():
            raise LlmUnavailable("Language model file is missing.")

        gpu_layers = "99" if models.has_gpu_llama(self._base_dir) else "0"
        command = [
            str(binary),
            "--model", str(self._model_path),
            "--host", "127.0.0.1",
            "--port", str(self._port),
            "--ctx-size", str(config.LLM_CONTEXT),
            "--n-gpu-layers", gpu_layers,
            "--jinja",
            "--reasoning-budget", "0",
            "--no-webui",
        ]

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(binary.parent),
                creationflags=creationflags,
            )
        except OSError as exc:
            raise LlmUnavailable(f"Could not start llama-server: {exc}") from exc

        if not self._wait_until_ready():
            self.stop()
            raise LlmUnavailable("llama-server did not become ready in time.")

    def _wait_until_ready(self) -> bool:
        deadline = time.monotonic() + config.LLM_STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                return False
            try:
                response = requests.get(f"{self._base_url}/health", timeout=5)
                if response.status_code == 200:
                    return True
            except requests.RequestException:
                pass
            time.sleep(1.0)
        return False

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass

    # --- inference -------------------------------------------------------
    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        max_tokens: int = 4096,
        temperature: float = config.LLM_TEMPERATURE,
    ) -> str:
        payload: dict = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": schema},
            }

        try:
            response = requests.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                timeout=config.LLM_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise LlmUnavailable(f"Local model request failed: {exc}") from exc

        if response.status_code != 200:
            raise LlmUnavailable(f"Local model returned HTTP {response.status_code}.")

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LlmUnavailable("Local model returned an unreadable response.") from exc

        return _THINK_BLOCK.sub("", content or "").strip()

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict,
        max_tokens: int = 4096,
    ) -> dict:
        """Constrained JSON, retried unconstrained before giving up."""
        attempts = ({"schema": schema}, {"schema": None})
        last_error: Exception | None = None

        for attempt in attempts:
            try:
                raw = self.complete(
                    system=system,
                    user=user,
                    schema=attempt["schema"],
                    max_tokens=max_tokens,
                )
                parsed = _parse_json_object(raw)
                if parsed is not None:
                    return parsed
                last_error = LlmUnavailable("Local model did not return JSON.")
            except LlmUnavailable as exc:
                last_error = exc

        raise LlmUnavailable(str(last_error or "Local model produced no usable JSON."))


def _parse_json_object(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        pass

    # Recover from fenced or prose-wrapped output.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
