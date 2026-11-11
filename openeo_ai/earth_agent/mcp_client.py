"""Persistent MCP stdio client for Earth-Agent tool servers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any, Iterable, Mapping

from .path_resolver import EarthAgentCallContext, EarthAgentPathResolver
from .security import EnvironmentScrubber, PathValidator, SubprocessGate


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EarthAgentServerSpec:
    """Runtime definition of one Earth-Agent MCP server."""

    name: str
    script_path: Path


class _MCPProtocolError(RuntimeError):
    """Raised when an MCP server returns invalid protocol data."""


class _MCPStdioProcess:
    """Minimal MCP-over-stdio client using NDJSON (newline-delimited JSON)."""

    def __init__(
        self,
        command: list[str],
        cwd: Path,
        env: Mapping[str, str],
        name: str,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = dict(env)
        self.name = name
        self.process: asyncio.subprocess.Process | None = None
        self._request_ids = count(1)
        self._lock = asyncio.Lock()
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=str(self.cwd),
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "jonaai-earth-agent", "version": "0.1.0"},
            },
        )
        await self.notify("notifications/initialized", {})

    async def close(self) -> None:
        if not self.process:
            return
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self._stderr_task:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task

    async def notify(self, method: str, params: Mapping[str, Any]) -> None:
        await self._write_message(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            request_id = next(self._request_ids)
            await self._write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            while True:
                message = await self._read_message()
                if "id" not in message:
                    continue
                if message["id"] != request_id:
                    continue
                if "error" in message:
                    raise _MCPProtocolError(
                        f"{self.name} MCP error for {method}: {message['error']}"
                    )
                return message["result"]

    async def _write_message(self, payload: Mapping[str, Any]) -> None:
        """Send a JSON-RPC message as a single newline-terminated line.

        MCP SDK v1.12+ uses newline-delimited JSON (NDJSON), not
        Content-Length framing.
        """
        if not self.process or not self.process.stdin:
            raise _MCPProtocolError(f"{self.name} process is not running")
        line = json.dumps(payload, ensure_ascii=True) + "\n"
        self.process.stdin.write(line.encode("utf-8"))
        await self.process.stdin.drain()

    async def _read_message(self) -> dict[str, Any]:
        """Read one newline-terminated JSON-RPC message from stdout."""
        if not self.process or not self.process.stdout:
            raise _MCPProtocolError(f"{self.name} process is not running")
        while True:
            raw_line = await self.process.stdout.readline()
            if not raw_line:
                raise _MCPProtocolError(f"{self.name} process closed stdout")
            text = raw_line.decode("utf-8").strip()
            if not text:
                # Skip empty lines, continue reading
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "[Earth-Agent:%s] Skipping non-JSON line: %s (%s)",
                    self.name, text[:200], exc,
                )
                continue

    async def _drain_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return
        while True:
            line = await self.process.stderr.readline()
            if not line:
                return
            logger.debug("[Earth-Agent:%s] %s", self.name, line.decode("utf-8", errors="replace").rstrip())


class EarthAgentMCPClient:
    """Bridge to Earth-Agent's Statistics, Inversion, and Analysis MCP servers."""

    SERVER_NAMES = ("Statistics", "Inversion", "Analysis", "Index", "Perception")

    def __init__(
        self,
        earth_agent_root: Path | str | None = None,
        python_executable: str | None = None,
        conda_env: str = "earth-agent",
        path_resolver: EarthAgentPathResolver | None = None,
        path_validator: PathValidator | None = None,
        subprocess_gate: SubprocessGate | None = None,
        timeout_seconds: int = 120,
        extra_env_vars: Iterable[str] = (),
    ) -> None:
        self.earth_agent_root = (
            Path(earth_agent_root).expanduser().resolve(strict=False)
            if earth_agent_root
            else Path(__file__).resolve().parents[2] / "Earth-Agent"
        )
        self.earth_agent_agent_dir = self.earth_agent_root / "agent"
        self.python_executable = python_executable
        self.conda_env = conda_env
        self.timeout_seconds = timeout_seconds
        self.subprocess_gate = subprocess_gate or SubprocessGate(max_concurrency=5)
        self.path_resolver = path_resolver or EarthAgentPathResolver()
        self.path_validator = path_validator or self.path_resolver.path_validator
        self.extra_env_vars = tuple(extra_env_vars)
        self._servers: dict[str, _MCPStdioProcess] = {}
        self._tool_cache: dict[str, list[dict[str, Any]]] = {}

    async def start(self) -> None:
        for spec in self._server_specs():
            if spec.name in self._servers:
                continue
            process = _MCPStdioProcess(
                command=self._build_server_command(spec),
                cwd=self.earth_agent_agent_dir,
                env=self._build_server_env(),
                name=spec.name,
            )
            await process.start()
            self._servers[spec.name] = process

    async def close(self) -> None:
        for process in list(self._servers.values()):
            await process.close()
        self._servers.clear()

    async def list_tools(self, server_name: str | None = None, force_refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
        await self.start()
        server_names = (server_name,) if server_name else self.SERVER_NAMES
        tools: dict[str, list[dict[str, Any]]] = {}
        for name in server_names:
            if not force_refresh and name in self._tool_cache:
                tools[name] = list(self._tool_cache[name])
                continue
            result = await asyncio.wait_for(
                self._servers[name].request("tools/list", {}),
                timeout=self.timeout_seconds,
            )
            server_tools = result.get("tools", [])
            self._tool_cache[name] = list(server_tools)
            tools[name] = list(server_tools)
        return tools

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        job_id: str | None = None,
    ) -> dict[str, Any]:
        if server_name not in self.SERVER_NAMES:
            raise ValueError(f"Unsupported Earth-Agent server: {server_name}")
        await self.start()
        call_context = self.path_resolver.create_call_context(
            prefix=f"ea-{server_name.lower()}",
            job_id=job_id,
        )
        safe_arguments = self._prepare_arguments(arguments, call_context)

        async with self.subprocess_gate.acquire():
            try:
                result = await asyncio.wait_for(
                    self._servers[server_name].request(
                        "tools/call",
                        {"name": tool_name, "arguments": safe_arguments},
                    ),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Earth-Agent tool timed out after {self.timeout_seconds}s: {server_name}/{tool_name}"
                ) from exc

        path_mapping = self.path_resolver.materialize_outputs(call_context)
        mapped_result = self.path_resolver.rewrite_paths_in_payload(result, path_mapping)
        return {
            "server": server_name,
            "tool_name": tool_name,
            "arguments": safe_arguments,
            "result": mapped_result,
            "output_files": sorted(path_mapping.values()),
            "temp_dir": str(call_context.temp_dir),
        }

    def _prepare_arguments(
        self,
        arguments: Mapping[str, Any],
        call_context: EarthAgentCallContext,
    ) -> dict[str, Any]:
        return {
            key: self._prepare_argument_value(key, value, call_context)
            for key, value in arguments.items()
        }

    def _prepare_argument_value(
        self,
        key: str,
        value: Any,
        call_context: EarthAgentCallContext,
    ) -> Any:
        lower_key = key.lower()
        if isinstance(value, list):
            return [
                self._prepare_argument_value(key, item, call_context)
                for item in value
            ]
        if not isinstance(value, str):
            return value
        # Output paths: route to temp_dir, don't validate as existing files
        if any(token in lower_key for token in ("output", "save", "result_path", "dest")):
            return self.path_resolver.build_output_argument(value, call_context)
        # Input paths: validate they exist and are in allowed dirs
        if any(token in lower_key for token in ("path", "file", "input")):
            try:
                return str(self.path_validator.validate_input_file(value))
            except Exception:
                # File doesn't exist — likely an output path with an ambiguous name.
                # Route it as an output instead of rejecting it.
                if not Path(value).exists():
                    return self.path_resolver.build_output_argument(value, call_context)
                raise
        return value

    def _server_specs(self) -> list[EarthAgentServerSpec]:
        tools_dir = self.earth_agent_agent_dir / "tools"
        return [
            EarthAgentServerSpec(name=name, script_path=tools_dir / f"{name}.py")
            for name in self.SERVER_NAMES
        ]

    def _build_server_env(self) -> dict[str, str]:
        return EnvironmentScrubber.clean_env(
            extra_vars=self.extra_env_vars,
            overrides={"GTIFF_SRS_SOURCE": "EPSG"},
        )

    def _build_server_command(self, spec: EarthAgentServerSpec) -> list[str]:
        return [
            *self._resolve_python_command(),
            str(spec.script_path),
            "--temp_dir",
            str(self.path_resolver.temp_dir_manager.root_dir),
        ]

    def _resolve_python_command(self) -> list[str]:
        if self.python_executable:
            return [self.python_executable]
        configured = os.getenv("EARTH_AGENT_PYTHON")
        if configured:
            return [configured]
        venv_python = self.earth_agent_root / ".venv" / "bin" / "python"
        if venv_python.exists():
            return [str(venv_python)]
        conda_exe = os.getenv("CONDA_EXE") or shutil.which("conda")
        if conda_exe:
            return [conda_exe, "run", "-n", self.conda_env, "python"]
        return [sys.executable]
