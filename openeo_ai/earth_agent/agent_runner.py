"""Meta-tool subprocess wrapper for Earth-Agent ReAct analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

# Guard relative imports — when run as a standalone script by the EA Python
# (which doesn't have openeo_ai installed), these will fail. The __main__
# block below only uses stdlib + langchain deps, no relative imports needed.
try:
    from .path_resolver import EarthAgentPathResolver
    from .prompt_builder import build_analysis_prompt
    from .security import EarthAgentRateLimiter, EnvironmentScrubber, PathValidator, SubprocessGate
except ImportError:
    # Running as standalone script — only __main__ block functions are used
    EarthAgentPathResolver = None  # type: ignore
    build_analysis_prompt = None  # type: ignore
    EarthAgentRateLimiter = None  # type: ignore
    EnvironmentScrubber = None  # type: ignore
    PathValidator = None  # type: ignore
    SubprocessGate = None  # type: ignore


MODEL_ENV_VARS = (
    "EARTH_AGENT_API_KEY",
    "EARTH_AGENT_BASE_URL",
    "EARTH_AGENT_MODEL",
)

# Phase 16/Earth-Agent: Langfuse env vars that must propagate into the EA
# subprocess so it can attach spans to the parent JonaAI trace.
LANGFUSE_ENV_VARS = (
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_PARENT_TRACE_ID",  # injected per-call (not sourced from parent env)
    "LANGSMITH_OTEL_ENABLED",
    "LANGSMITH_OTEL_ONLY",
    "LANGSMITH_TRACING",
)


def _subprocess_error_detail(stderr: str, limit: int = 500) -> str:
    """Surface the real subprocess failure, not the FastMCP startup banner.

    The EA tool servers print a large banner to stderr on startup; the actual
    failure is the LAST {"error": ...} JSON line main() emits. A first-N-chars
    truncation showed only banner and hid a 401 for days.
    """
    for line in reversed(stderr.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and "error" in payload:
                return str(payload["error"])[:limit]
    return stderr.strip()[-limit:]


class EarthAgentRunner:
    """Execute Earth-Agent's multi-step analysis flow inside an isolated subprocess."""

    def __init__(
        self,
        earth_agent_root: Path | str | None = None,
        python_executable: str | None = None,
        conda_env: str = "earth-agent",
        path_resolver: EarthAgentPathResolver | None = None,
        path_validator: PathValidator | None = None,
        rate_limiter: EarthAgentRateLimiter | None = None,
        subprocess_gate: SubprocessGate | None = None,
        timeout_seconds: int = 300,
    ) -> None:
        self.earth_agent_root = (
            Path(earth_agent_root).expanduser().resolve(strict=False)
            if earth_agent_root
            else Path(__file__).resolve().parents[2] / "Earth-Agent"
        )
        self.python_executable = python_executable
        self.conda_env = conda_env
        self.path_resolver = path_resolver or EarthAgentPathResolver()
        self.path_validator = path_validator or self.path_resolver.path_validator
        self.rate_limiter = rate_limiter or EarthAgentRateLimiter(max_calls=10, window_seconds=60)
        self.subprocess_gate = subprocess_gate or SubprocessGate(max_concurrency=5)
        self.timeout_seconds = timeout_seconds

    async def analyze(
        self,
        query: str,
        input_files: Sequence[str | Path],
        data_context: Mapping[str, Any] | None = None,
        analysis_mode: str = "auto",
        max_steps: int = 50,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        self.rate_limiter.check("earth_agent_analyze")
        resolved_inputs = self.path_resolver.resolve_input_files(input_files)
        call_context = self.path_resolver.create_call_context(prefix="earth-agent-agent", job_id=job_id)
        prompt = build_analysis_prompt(
            query=query,
            input_files=[str(path) for path in resolved_inputs],
            data_context=data_context,
            analysis_mode=analysis_mode,
            max_steps=max_steps,
        )

        command = self._build_command(
            prompt=prompt,
            input_files=resolved_inputs,
            call_temp_dir=call_context.temp_dir,
            max_steps=max_steps,
            analysis_mode=analysis_mode,
        )

        async with self.subprocess_gate.acquire():
            stdout, stderr, returncode = await self._run_command(command)

        if returncode != 0:
            raise RuntimeError(
                f"Earth-Agent analysis failed with exit code {returncode}: "
                f"{_subprocess_error_detail(stderr)}"
            )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            # EA may print debug output above JSON — try last non-empty line
            for line in reversed(stdout.strip().splitlines()):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
            else:
                raise RuntimeError(
                    f"Earth-Agent returned non-JSON output (exit {returncode}).\n"
                    f"stdout (last 500 chars): {stdout.strip()[-500:]}\n"
                    f"stderr (last 500 chars): {stderr.strip()[-500:]}"
                )
        path_mapping = self.path_resolver.materialize_outputs(call_context)
        mapped_payload = self.path_resolver.rewrite_paths_in_payload(payload, path_mapping)
        mapped_payload["output_files"] = sorted(path_mapping.values())
        mapped_payload["temp_dir"] = str(call_context.temp_dir)
        return mapped_payload

    async def _run_command(self, command: Sequence[str]) -> tuple[str, str, int]:
        # Forward the in-flight Langfuse trace_id (if any) to the subprocess
        # so EA LLM/tool spans nest under the JonaAI root trace.
        overrides: dict[str, str] = {"GTIFF_SRS_SOURCE": "EPSG"}
        try:
            from ..langfuse_setup import current_trace_id

            parent_trace_id = current_trace_id()
            if parent_trace_id:
                overrides["LANGFUSE_PARENT_TRACE_ID"] = parent_trace_id
        except Exception:
            # Langfuse may not be initialized (tests, CI). Subprocess will
            # run with a detached trace — still captured, not linked.
            pass

        env = EnvironmentScrubber.clean_env(
            extra_vars=(*MODEL_ENV_VARS, *LANGFUSE_ENV_VARS),
            overrides=overrides,
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError(
                f"earth_agent_analyze timed out after {self.timeout_seconds}s"
            ) from None
        return (
            stdout_bytes.decode("utf-8"),
            stderr_bytes.decode("utf-8"),
            process.returncode or 0,
        )

    def _build_command(
        self,
        prompt: str,
        input_files: Sequence[Path],
        call_temp_dir: Path,
        max_steps: int,
        analysis_mode: str,
    ) -> list[str]:
        # Use the EA Python to run the standalone runner script.
        # The runner's __main__ block uses only absolute imports (no relative)
        # so it can run in the EA venv which has langchain/langgraph deps.
        ea_python = self._resolve_ea_python()
        runner_script = str(Path(__file__).resolve())
        command = [
            ea_python,
            runner_script,
            "--execute",
            "--earth-agent-root",
            str(self.earth_agent_root),
            "--temp-dir",
            str(call_temp_dir),
            "--max-steps",
            str(max_steps),
            "--analysis-mode",
            analysis_mode,
            "--query",
            prompt,
        ]
        for input_file in input_files:
            command.extend(["--input-file", str(input_file)])
        return command

    def _resolve_ea_python(self) -> str:
        """Resolve the Earth-Agent Python interpreter."""
        if self.python_executable:
            return self.python_executable
        configured = os.getenv("EARTH_AGENT_PYTHON")
        if configured:
            return configured
        venv_python = self.earth_agent_root / ".venv" / "bin" / "python3"
        if venv_python.exists():
            return str(venv_python)
        venv_python2 = self.earth_agent_root / ".venv" / "bin" / "python"
        if venv_python2.exists():
            return str(venv_python2)
        return sys.executable


def _serialize_message(message: Any) -> dict[str, Any]:
    content = getattr(message, "content", "")
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls is None and hasattr(message, "additional_kwargs"):
        tool_calls = message.additional_kwargs.get("tool_calls")
    return {
        "type": getattr(message, "type", type(message).__name__),
        "name": getattr(message, "name", None),
        "content": content,
        "tool_calls": tool_calls,
    }


def _extract_final_answer(response: Mapping[str, Any]) -> str:
    messages = response.get("messages", [])
    for message in reversed(messages):
        content = getattr(message, "content", "")
        if content:
            return str(content)
    return ""


def _init_langfuse_for_subprocess() -> tuple[Any, list[Any]]:
    """Initialize Langfuse inside the EA subprocess and return (client, callbacks).

    Returns (None, []) if Langfuse is unavailable or not configured — the
    subprocess continues to function, just without observability.
    """
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        return None, []
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
    except Exception as exc:  # noqa: BLE001 — broad: langfuse optional in EA venv
        print(
            json.dumps({"warn": f"langfuse unavailable in EA subprocess: {exc}"}),
            file=sys.stderr,
        )
        return None, []

    host = (
        os.getenv("LANGFUSE_HOST")
        or os.getenv("LANGFUSE_BASE_URL")
        or "https://us.cloud.langfuse.com"
    )
    try:
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        handler = CallbackHandler()
        return client, [handler]
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps({"warn": f"langfuse init failed in EA subprocess: {exc}"}),
            file=sys.stderr,
        )
        return None, []


async def _execute_react_agent(
    *,
    earth_agent_root: Path,
    temp_dir: Path,
    query: str,
    input_files: Sequence[Path],
    max_steps: int,
) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langgraph.prebuilt import create_react_agent

    # Langfuse callback — binds the EA LLM + tool spans to the JonaAI parent
    # trace if LANGFUSE_PARENT_TRACE_ID was forwarded from the parent process.
    lf_client, lf_callbacks = _init_langfuse_for_subprocess()
    parent_trace_id = os.getenv("LANGFUSE_PARENT_TRACE_ID")

    # Auto-detect LLM provider: prefer EARTH_AGENT_API_KEY (isolated key),
    # fall back to ANTHROPIC_API_KEY only inside the subprocess (never forwarded
    # through MODEL_ENV_VARS to avoid leaking the main JonaAI key).
    # SECURITY: Never fall back to ANTHROPIC_API_KEY — use only the isolated EA key.
    anthropic_key = os.getenv("EARTH_AGENT_API_KEY")
    if not anthropic_key:
        raise RuntimeError(
            "EARTH_AGENT_API_KEY is required for Earth-Agent. "
            "Do not use ANTHROPIC_API_KEY in EA context."
        )
    base_url = os.getenv("EARTH_AGENT_BASE_URL")

    if anthropic_key and not base_url:
        from langchain_anthropic import ChatAnthropic
        model_name = os.getenv("EARTH_AGENT_MODEL", "claude-haiku-4-5-20251001")
        llm = ChatAnthropic(
            model=model_name,
            api_key=anthropic_key,
            temperature=0.1,
            max_tokens=4096,
            timeout=120.0,
            callbacks=lf_callbacks or None,
        )
    elif anthropic_key and base_url:
        # OpenAI-compatible endpoint (e.g. vLLM, Azure, etc.)
        from langchain_openai import ChatOpenAI
        model_name = os.getenv("EARTH_AGENT_MODEL", "gpt-4o")
        llm = ChatOpenAI(
            model=model_name,
            api_key=anthropic_key,
            base_url=base_url,
            temperature=0.1,
            request_timeout=120,
            callbacks=lf_callbacks or None,
        )
    else:
        raise RuntimeError(
            "No LLM API key configured. Set EARTH_AGENT_API_KEY."
        )

    # Use the EA venv Python for MCP tool servers (they need GDAL/numpy)
    ea_python = os.getenv("EARTH_AGENT_PYTHON", "")
    if not ea_python:
        ea_venv = earth_agent_root / ".venv" / "bin" / "python3"
        ea_python = str(ea_venv) if ea_venv.exists() else sys.executable

    mcp_servers = {}
    tools_dir = earth_agent_root / "agent" / "tools"
    for server_name in ("Statistics", "Inversion", "Analysis", "Index", "Perception"):
        mcp_servers[server_name] = {
            "command": ea_python,
            "args": [
                str(tools_dir / f"{server_name}.py"),
                "--temp_dir",
                str(temp_dir),
            ],
            "transport": "stdio",
        }

    client = MultiServerMCPClient(mcp_servers)

    # Build the LangChain run config. The Langfuse callback captures every
    # LLM request and tool call inside the ReAct agent as child spans.
    run_config: dict[str, Any] = {
        "recursion_limit": max_steps,
        "max_execution_time": 300,
    }
    if lf_callbacks:
        run_config["callbacks"] = lf_callbacks
        run_config["metadata"] = {
            "component": "earth-agent",
            "earth_agent_model": os.getenv("EARTH_AGENT_MODEL", ""),
            "max_steps": max_steps,
            "parent_trace_id": parent_trace_id or "",
        }
        run_config["run_name"] = "earth_agent.react_agent"
        run_config["tags"] = ["earth-agent", "subprocess", "react"]

    # Atom-of-thought decomposition for the root-span wrapping:
    #   Atom 1: lf_client is a live Langfuse client (checked above)
    #   Atom 2: parent_trace_id is a valid 32-hex trace_id from the JonaAI side
    #   Atom 3: start_as_current_observation(trace_context={"trace_id": ...})
    #           binds the new span (and all its children) to that trace
    #   Atom 4: The LangChain callback creates child spans inside this context,
    #           so LLM/tool spans automatically nest under the EA root span.
    # Result: one root span per EA invocation, parented to the JonaAI trace.
    from contextlib import nullcontext

    if lf_client is not None and parent_trace_id:
        root_span_cm = lf_client.start_as_current_observation(
            name="earth_agent.subprocess",
            as_type="agent",
            input={
                "query": query[:500],
                "input_file_count": len(input_files),
                "max_steps": max_steps,
            },
            metadata={
                "component": "earth-agent",
                "mcp_servers": sorted(mcp_servers.keys()),
                "ea_model": os.getenv("EARTH_AGENT_MODEL", ""),
                "subprocess": True,
            },
            trace_context={"trace_id": parent_trace_id},
        )
    elif lf_client is not None:
        # No parent — start a detached root span under a new trace. This
        # still gives EA invocations their own Langfuse traces when JonaAI
        # isn't in the call chain (e.g., direct CLI invocation).
        root_span_cm = lf_client.start_as_current_observation(
            name="earth_agent.subprocess",
            as_type="agent",
            input={
                "query": query[:500],
                "input_file_count": len(input_files),
                "max_steps": max_steps,
            },
            metadata={"component": "earth-agent", "subprocess": True, "detached": True},
        )
    else:
        root_span_cm = nullcontext()

    response: dict[str, Any] = {}
    try:
        with root_span_cm as root_span:
            try:
                tools = await client.get_tools()
                agent = create_react_agent(llm, tools)
                response = await agent.ainvoke(
                    {"messages": [HumanMessage(content=query)]},
                    config=run_config,
                )
                # Annotate the root span with a summary before closing.
                if root_span is not None and hasattr(root_span, "update"):
                    try:
                        answer_text = _extract_final_answer(response)
                        root_span.update(
                            output={
                                "answer_preview": answer_text[:500],
                                "message_count": len(response.get("messages", [])),
                            }
                        )
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                close = getattr(client, "close", None)
                if close:
                    maybe = close()
                    if asyncio.iscoroutine(maybe):
                        await maybe
    finally:
        # Ensure all buffered spans are flushed before the subprocess exits
        # — otherwise short EA runs lose their last-batch telemetry.
        if lf_client is not None:
            try:
                lf_client.flush()
            except Exception as exc:  # noqa: BLE001
                print(
                    json.dumps({"warn": f"langfuse flush failed: {exc}"}),
                    file=sys.stderr,
                )

    return {
        "query": query,
        "input_files": [str(path) for path in input_files],
        "answer": _extract_final_answer(response),
        "trajectory": [_serialize_message(message) for message in response.get("messages", [])],
        "raw_response": {
            "message_count": len(response.get("messages", [])),
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute the Earth-Agent meta-tool runner")
    parser.add_argument("--execute", action="store_true", help="Run the CLI execution path")
    parser.add_argument("--earth-agent-root", required=False, default=None)
    parser.add_argument("--temp-dir", required=False, default=None)
    parser.add_argument("--analysis-mode", required=False, default="auto")
    parser.add_argument("--max-steps", required=False, type=int, default=50)
    parser.add_argument("--query", required=False, default="")
    parser.add_argument("--input-file", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.execute:
        return 0

    earth_agent_root = (
        Path(args.earth_agent_root).expanduser().resolve(strict=False)
        if args.earth_agent_root
        else Path(__file__).resolve().parents[2] / "Earth-Agent"
    )
    temp_dir_value = args.temp_dir or str(Path("/tmp/ea_results") / "earth-agent-cli")
    temp_dir = Path(temp_dir_value).expanduser().resolve(strict=False)
    temp_dir.mkdir(parents=True, exist_ok=True)
    input_files = [Path(path).expanduser().resolve(strict=False) for path in args.input_file]

    try:
        result = asyncio.run(
            _execute_react_agent(
                earth_agent_root=earth_agent_root,
                temp_dir=temp_dir,
                query=args.query,
                input_files=input_files,
                max_steps=args.max_steps,
            )
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
