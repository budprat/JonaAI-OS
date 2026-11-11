# ABOUTME: Terminal WS client to drive a real JonaAI chat and stream every event.
# ABOUTME: Mints a dev JWT (HS256/SUPABASE_JWT_SECRET), connects /ws, prints all steps.
#
# Usage: jona_chat.py [--timeout N] [--sub ID] PROMPT [PROMPT ...]
#   One or more prompts are sent sequentially on the SAME session, so context
#   carries across them (a multi-turn conversation). --timeout is per turn.

import argparse
import asyncio
import json
import os
import time
import uuid

import jwt
import websockets

WS_URL = "ws://127.0.0.1:8080/ws"  # IPv4 explicit: the server binds IPv4-only, `localhost` may resolve to ::1


def _load_env_secret(path: str, name: str) -> str:
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(f"export {name}="):
                val = line.split("=", 1)[1].strip()
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                return val
    raise SystemExit(f"{name} not found in {path}")


def _trunc(obj, n=600):
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= n else s[:n] + f" …[+{len(s) - n} chars]"


def _mint_token(sub: str) -> str:
    # verify_supabase_token Path 2 decodes HS256 with SUPABASE_JWT_SECRET and
    # requires aud="authenticated".
    secret = _load_env_secret(os.path.join(os.getcwd(), ".env"), "SUPABASE_JWT_SECRET")
    now = int(time.time())
    return jwt.encode(
        {
            "sub": sub,
            "email": "terminal@dev.local",
            "role": "authenticated",
            "aud": "authenticated",
            "iat": now,
            "exp": now + 3600,
        },
        secret,
        algorithm="HS256",
    )


async def _stream_turn(recv, ws, timeout: float):
    """Stream one turn until 'done' (or error/timeout). Returns (tools, chars)."""
    tools, chars = [], 0
    turn_start = time.time()
    while (time.time() - turn_start) < timeout:
        try:
            ev = await recv()
        except asyncio.TimeoutError:
            print("\n[client] recv timeout — stopping turn.", flush=True)
            break

        etype = ev.get("type", "?")
        if etype == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            continue
        if etype in ("text", "content", "assistant", "text_delta"):
            chunk = ev.get("content") or ev.get("text") or ""
            chars += len(chunk)
            print(chunk, end="", flush=True)
        elif etype in ("text_stream_start", "text_stream_end"):
            pass
        elif etype == "thinking":
            print(f"\n  · thinking[{ev.get('thinking_type','')}] {_trunc(ev.get('content',''),120)}", flush=True)
        elif etype in ("tool_use", "tool", "tool_call", "tool_start"):
            name = ev.get("tool") or ev.get("name") or ev.get("tool_name") or "?"
            tools.append(name)
            print(f"\n  🔧 TOOL → {name}  input={_trunc(ev.get('input') or ev.get('tool_input') or {}, 300)}", flush=True)
        elif etype in ("tool_result", "tool_end", "tool_output"):
            print(f"  ← result {_trunc(ev.get('content') or ev.get('result') or ev, 300)}", flush=True)
        elif etype == "visualization":
            v = ev.get("visualization", {})
            print(f"\n  📊 VIZ [{v.get('type','?')}] {_trunc({k: v.get(k) for k in ('title','type')}, 120)}", flush=True)
        elif etype == "report_sections":
            print(f"\n  📄 report_sections ({len(ev.get('sections', []))} sections)", flush=True)
        elif etype == "trace":
            print(f"\n  🧭 trace_id={ev.get('trace_id')}", flush=True)
        elif etype == "error":
            print(f"\n  ❌ ERROR: {ev.get('content')}", flush=True)
            break
        elif etype in ("done", "complete", "end"):
            print(f"\n\n[done] {_trunc(ev, 200)}", flush=True)
            break
        else:
            print(f"\n  [{etype}] {_trunc(ev, 200)}", flush=True)
    return tools, chars


async def main():
    ap = argparse.ArgumentParser(
        description="Drive a JonaAI chat over the WS. Multiple prompts run as one "
                    "multi-turn conversation (context carries)."
    )
    ap.add_argument("prompts", nargs="+", help="One or more prompts (sent in order on one session).")
    ap.add_argument("--timeout", type=float, default=180.0, help="Per-turn recv timeout in seconds (default 180).")
    ap.add_argument("--sub", default=None, help="User id (JWT sub); default a random dev id.")
    args = ap.parse_args()

    token = _mint_token(args.sub or f"dev-terminal-{uuid.uuid4()}")
    all_tools, total_chars = [], 0
    started = time.time()

    async with websockets.connect(WS_URL, max_size=None) as ws:
        async def recv():
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=args.timeout))

        first = await recv()
        print(f"[connect] {_trunc(first, 300)}", flush=True)
        await ws.send(json.dumps({"type": "authenticate", "token": token}))

        # wait for auth ack
        while True:
            ev = await recv()
            if ev.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue
            if ev.get("type") == "auth_result" and not ev.get("authenticated"):
                raise SystemExit(f"[auth] failed: {ev.get('error')}")
            if ev.get("user_id") or ev.get("type") in ("authenticated", "auth_success", "auth_result"):
                print(f"[auth]  ok — user_id={str(ev.get('user_id'))[:12]}…", flush=True)
                break

        for i, prompt in enumerate(args.prompts, 1):
            print(f"\n{'='*70}\n  TURN {i}/{len(args.prompts)}: {prompt}\n{'='*70}\n", flush=True)
            await ws.send(json.dumps({"type": "message", "content": prompt}))
            tools, chars = await _stream_turn(recv, ws, args.timeout)
            all_tools += tools
            total_chars += chars

    dt = time.time() - started
    print(f"\n\n{'='*70}", flush=True)
    print(f"  SUMMARY  ({dt:.1f}s, {len(args.prompts)} turn(s))", flush=True)
    print(f"  tools called: {all_tools or '(none)'}", flush=True)
    print(f"  assistant chars: {total_chars}", flush=True)
    print(f"{'='*70}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
