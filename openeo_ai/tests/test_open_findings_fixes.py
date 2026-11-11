"""Restart recovery, access tokens, account allowlist, spend limits, and catalog routing."""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException


@pytest.fixture()
def wi(monkeypatch, tmp_path):
    monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", "false")
    monkeypatch.setenv("JONA_RUN_LOG_DIR", str(tmp_path / "runs"))
    import openeo_ai.web_interface as module

    module._query_runs.clear()
    yield module
    module._query_runs.clear()


def _finish_a_run(wi, events, session="s1", owner="u1"):
    """Run a query that sends `events`. Without a done in them, it was cut off by a shutdown."""
    async def scenario():
        run = wi._QueryRun(session, owner)

        async def body():
            for event in events:
                await run.send_json(event)

        run.start(body())
        await run.task
        return run.run_id

    cut_off = not any(event["type"] == "done" for event in events)
    wi._shutting_down = cut_off
    try:
        return asyncio.run(scenario())
    finally:
        wi._shutting_down = False


def test_unfinished_run_recovers_for_its_owner_with_a_notice(wi):
    run_id = _finish_a_run(wi, [{"type": "text", "content": "partial"}])

    async def recover():
        return await wi._recover_run("s1", "u1")

    run = asyncio.run(recover())
    types = [event["type"] for event, _ in run._events]
    assert run.run_id == run_id
    assert types == ["text", "error", "done"]
    assert run.task.done()
    assert not run.fully_delivered
    assert [event["seq"] for event, _ in run._events] == [1, 2, 3]


def test_finished_run_recovers_without_a_notice(wi):
    _finish_a_run(wi, [{"type": "text", "content": "all"}, {"type": "done"}])
    run = asyncio.run(wi._recover_run("s1", "u1"))
    assert [event["type"] for event, _ in run._events] == ["text", "done"]


def test_another_user_cannot_recover_a_run(wi):
    _finish_a_run(wi, [{"type": "text", "content": "secret"}])
    assert asyncio.run(wi._recover_run("s1", "u2")) is None


@pytest.mark.parametrize("session", ["../../etc/passwd", "a/b", "", "x" * 65, "a.b"])
def test_unsafe_session_ids_get_no_log_file(wi, session):
    assert wi._run_log_path(session) is None


def test_old_run_logs_are_pruned(wi, tmp_path):
    path = wi._run_log_path("old")
    path.write_text("{}")
    import os

    os.utime(path, (time.time() - wi._RUN_RETENTION_SECONDS - 10,) * 2)
    wi._prune_run_logs()
    assert not path.exists()


def _token_kit(monkeypatch):
    from openeo_ai import access_guard

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setitem(
        access_guard._jwks_clients,
        "https://team.cloudflareaccess.com",
        SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())),
    )
    monkeypatch.setenv("JONA_ACCESS_TEAM_DOMAIN", "team.cloudflareaccess.com")
    monkeypatch.setenv("JONA_ACCESS_AUD", "app-aud")

    def token(**overrides):
        claims = {"aud": "app-aud", "iss": "https://team.cloudflareaccess.com",
                  "exp": datetime.now(timezone.utc) + timedelta(minutes=5), **overrides}
        return jwt.encode(claims, key, algorithm="RS256")

    return access_guard, token


def _call_guard(guard, scope_type, headers):
    sent, reached = [], []

    async def app(scope, receive, send):
        reached.append(True)

    async def send(message):
        sent.append(message)

    scope = {"type": scope_type, "path": "/x", "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    asyncio.run(guard.AccessGuard(app)(scope, None, send))
    return bool(reached), sent


@pytest.mark.parametrize("scope_type", ["http", "websocket"])
def test_enforce_mode_checks_the_signature(monkeypatch, scope_type):
    guard, token = _token_kit(monkeypatch)
    monkeypatch.setenv("JONA_ACCESS_MODE", "enforce")
    visitor = {"cf-connecting-ip": "1.1.1.1"}
    assert _call_guard(guard, scope_type, {**visitor, "cf-access-jwt-assertion": token()})[0] is True
    for bad in ("x", token(aud="other-app"), token(iss="https://evil"), token(exp=datetime.now(timezone.utc) - timedelta(minutes=1))):
        assert _call_guard(guard, scope_type, {**visitor, "cf-access-jwt-assertion": bad})[0] is False
    assert _call_guard(guard, scope_type, visitor)[0] is False


def test_requests_from_this_host_are_not_checked(monkeypatch):
    guard, _ = _token_kit(monkeypatch)
    monkeypatch.setenv("JONA_ACCESS_MODE", "enforce")
    assert _call_guard(guard, "http", {})[0] is True


def test_observe_mode_never_blocks(monkeypatch):
    guard, _ = _token_kit(monkeypatch)
    monkeypatch.setenv("JONA_ACCESS_MODE", "observe")
    assert _call_guard(guard, "http", {"cf-connecting-ip": "1.1.1.1", "cf-access-jwt-assertion": "x"})[0] is True


def test_enforce_without_settings_refuses_rather_than_trusting(monkeypatch):
    guard, _ = _token_kit(monkeypatch)
    monkeypatch.setenv("JONA_ACCESS_MODE", "enforce")
    monkeypatch.delenv("JONA_ACCESS_AUD")
    assert _call_guard(guard, "http", {"cf-connecting-ip": "1.1.1.1", "cf-access-jwt-assertion": "x"})[0] is False


def test_allowlist_limits_accounts_for_every_token_type(monkeypatch):
    from openeo_ai import supabase_auth

    monkeypatch.setattr(supabase_auth, "SUPABASE_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("JONA_ALLOWED_EMAILS", "Owner@Example.com, other@example.com")
    def signed(email):
        return jwt.encode({"sub": "u", "email": email, "aud": "authenticated",
                           "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, "s" * 40, algorithm="HS256")
    assert supabase_auth.verify_supabase_token(signed("owner@example.com"))["sub"] == "u"
    assert supabase_auth.verify_supabase_token(signed("stranger@example.com")) is None
    monkeypatch.delenv("JONA_ALLOWED_EMAILS")
    assert supabase_auth.verify_supabase_token(signed("stranger@example.com"))["sub"] == "u"


def test_basic_login_is_refused_through_the_proxy(monkeypatch):
    from openeo_app import auth

    monkeypatch.setattr(auth, "DEV_MODE", True)
    with pytest.raises(HTTPException) as refused:
        auth.DevAuthenticator.validate("Basic dXNlcjpwdw==", cf_connecting_ip="1.1.1.1", x_forwarded_for=None)
    assert refused.value.status_code == 401
    with pytest.raises(HTTPException):
        auth.DevAuthenticator.validate("Basic dXNlcjpwdw==", cf_connecting_ip=None, x_forwarded_for="1.1.1.1")


def test_monthly_limit_refuses_new_queries(monkeypatch):
    from openeo_ai.sdk import claude_sdk_bridge as module

    monkeypatch.setenv("JONA_MONTHLY_USD", "100")
    monkeypatch.setattr(module.budget, "total_spend_this_month", lambda: 100.5)
    bridge = module.ClaudeSDKBridge.__new__(module.ClaudeSDKBridge)
    assert "monthly limit" in asyncio.run(bridge._refusal_before_start("hi", "u1"))
    monkeypatch.setattr(module.budget, "total_spend_this_month", lambda: 5.0)
    monkeypatch.setattr(module.budget, "user_spend_today", lambda user: 0.0)
    assert asyncio.run(bridge._refusal_before_start("hi", "u1")) is None


def test_missing_saved_file_is_reported_rather_than_a_broken_map(wi, monkeypatch):
    sent = []

    class Socket:
        async def send_json(self, data):
            sent.append(data)

    monkeypatch.setattr("openeo_ai.storage.result_access.can_read_saved_result", lambda save, user: True)
    monkeypatch.setattr("openeo_ai.storage.job_archive.get_job",
                        lambda save: {"title": "Old", "result_path": "/Users/x/gone.tif", "colormap": "magma"})
    assert asyncio.run(wi._send_saved_job(Socket(), "s", "u")) is False
    assert sent[0]["type"] == "error" and "isn't on this server" in sent[0]["content"]


def test_trend_scenes_cover_the_whole_period():
    from openeo_ai.agentic_eo.executor import _spread_over_time

    items = [SimpleNamespace(properties={"datetime": f"2024-{m:02d}-01", "eo:cloud_cover": m}) for m in range(1, 13)]
    kept = _spread_over_time(items, 4)
    months = [item.properties["datetime"] for item in kept]
    assert len(kept) == 4 and months[0] < "2024-04" and months[-1] >= "2024-10"
    assert _spread_over_time(items[:3], 4) == items[:3]


def test_flat_trend_is_not_called_rising():
    import openeo_ai.agentic_eo.report as report
    import inspect

    assert "shows no clear trend" in inspect.getsource(report)


def test_paid_collections_use_the_free_catalog():
    from openeo_app.processes.load_collection import COLLECTION_STAC_MAP

    for collection in ("landsat-c2-l2", "sentinel-1-grd", "sentinel-1-rtc"):
        assert "planetarycomputer" in COLLECTION_STAC_MAP[collection]
    assert "earth-search" in COLLECTION_STAC_MAP["sentinel-2-l2a"]


def test_evidence_substring_no_longer_grants_a_file():
    from openeo_ai.storage.result_access import artifact_paths

    found = artifact_paths({"a": "/data/x/result.tif", "b": ["https://example.com/y.tif"], "c": {"d": "notes"}})
    assert found == {"/data/x/result.tif"}


def test_unsafe_graph_processes_are_not_available():
    from openeo_app.execution.executor import _import_processes

    processes = _import_processes()
    for unsafe in ("load_url", "load_stac", "run_udf"):
        assert unsafe not in processes
    assert "load_collection" in processes


def test_workflow_steps_get_each_tools_access_check():
    import inspect
    from openeo_ai.sdk import inprocess_tools

    assert "tool_handlers=wrapped" in inspect.getsource(inprocess_tools._create_all_handlers)


@pytest.mark.parametrize("key", ["source_file", "file_path"])
def test_other_users_file_is_refused_under_every_argument_name(key, monkeypatch):
    from openeo_ai.sdk import inprocess_tools
    from openeo_ai.sdk.user_context import set_current_user_id
    from openeo_ai.storage import result_access

    monkeypatch.setattr(result_access, "can_read_path", lambda path, user: False)
    set_current_user_id("u1")
    assert "Access denied" in asyncio.run(inprocess_tools._access_denied({key: "/x/y.tif"}))
    assert "Access denied" in asyncio.run(inprocess_tools._access_denied({"input_files": ["/x/y.tif"]}))
    set_current_user_id(None)


def test_project_member_cannot_write_into_the_owners_saved_result(monkeypatch, tmp_path):
    from openeo_ai.sdk.user_context import set_current_user_id
    from openeo_ai.storage import result_access
    from openeo_ai.tools import export_tools

    results = tmp_path / ".openeo_jobs" / "results" / "abc12345"
    results.mkdir(parents=True)
    (results / "result.tif").write_bytes(b"owner data")
    export = tmp_path / "result.tif"
    export.write_bytes(b"replacement")
    monkeypatch.setattr(export_tools.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(result_access, "owns_saved_result", lambda save, user: False)
    set_current_user_id("viewer")
    export_tools._copy_to_saved_result(str(export), str(results / "result.tif"))
    set_current_user_id(None)
    assert (results / "result.tif").read_bytes() == b"owner data"


@pytest.mark.parametrize("address", ["100.101.102.103", "100.64.0.1", "::ffff:100.64.0.1", "192.0.2.1"])
def test_shared_and_reserved_ranges_are_private(address):
    from openeo_ai.intelligence.worker import _is_private_host

    assert _is_private_host(address)


def test_public_address_is_allowed():
    from openeo_ai.intelligence.worker import _is_private_host

    assert not _is_private_host("8.8.8.8")


def test_named_webhook_connects_to_the_checked_address(monkeypatch):
    from openeo_ai.intelligence import worker

    seen = {}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, extensions=None, **kwargs):
            seen.update(url=str(url), headers=headers, extensions=extensions)
            return SimpleNamespace(status_code=200)

    monkeypatch.delenv("JONA_ALLOW_PRIVATE_WEBHOOKS", raising=False)
    monkeypatch.setattr(worker.httpx, "AsyncClient", Client)

    async def resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(worker, "_resolve_addresses", resolve)
    asyncio.run(worker.pinned_post("https://hooks.example.com/x", json={}))
    assert seen["url"] == "https://93.184.216.34/x"
    assert seen["headers"]["Host"] == "hooks.example.com"
    assert seen["extensions"]["sni_hostname"] == "hooks.example.com"


def test_new_keys_cannot_exceed_their_creator():
    from fastapi.routing import APIRoute
    from openeo_ai.api.router import router

    guarded = [r.path for r in router.routes if isinstance(r, APIRoute) and "/analyze" in r.path]
    assert guarded
    from openeo_ai.api.auth import AuthenticatedClient

    client = AuthenticatedClient(client_id="c", user_id="u", scopes=["results"])
    assert client.has_scope("results") and not client.has_scope("*") and not client.has_scope("batch")


def test_tokens_are_removed_from_access_logs(wi):
    import logging

    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s", ('GET /render-raster?path=a&token=SECRET&x=1',), None)
    wi._RedactTokens().filter(record)
    assert "SECRET" not in record.args[0] and "token=REDACTED" in record.args[0]


def test_two_tabs_both_follow_a_run_and_a_stalled_one_is_closed(wi):
    class Socket:
        def __init__(self, fail=False):
            self.sent, self.fail, self.closed = [], fail, None

        async def send_json(self, data, **kwargs):
            if self.fail:
                raise RuntimeError("stalled")
            self.sent.append(data)

        async def close(self, code=1000):
            self.closed = code

    async def scenario():
        run = wi._QueryRun("tabs", "u")
        one, two, bad = Socket(), Socket(), Socket(fail=True)
        run.attach_new(one)
        await run.attach(two, 0)
        run._sockets.append(bad)
        await run.send_json({"type": "text", "content": "x"})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return one, two, bad, run

    one, two, bad, run = asyncio.run(scenario())
    assert len(one.sent) == 1 and len(two.sent) == 1
    assert bad not in run._sockets and bad.closed == 1011


def test_missed_early_events_are_flagged_on_reattach(wi):
    class Socket:
        def __init__(self):
            self.sent = []

        async def send_json(self, data, **kwargs):
            self.sent.append(data)

    async def scenario():
        run = wi._QueryRun("trim", "u")
        for n in range(3):
            await run.send_json({"type": "text", "content": str(n)})
        run._events.popleft()
        socket = Socket()
        await run.attach(socket, 0)
        return socket

    assert "too long to keep" in asyncio.run(scenario()).sent[0]["content"]


def test_entry_without_an_agent_is_treated_as_a_new_session():
    import inspect
    from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge

    assert 'not entry.get("client")' in inspect.getsource(ClaudeSDKBridge._query)


class TestThirdReview:
    def test_batch_owner_can_read_their_own_records(self):
        from openeo_ai.batch.models import BatchJobResponse, ScheduleResponse, WorkflowTemplateResponse

        assert {"user_id"} <= set(BatchJobResponse.model_fields)
        assert {"user_id"} <= set(ScheduleResponse.model_fields)
        assert {"user_id", "is_public"} <= set(WorkflowTemplateResponse.model_fields)

    def test_owned_rules(self):
        from openeo_ai.batch.routes import _owned

        request = SimpleNamespace(state=SimpleNamespace(user_id="owner"))
        mine, theirs = SimpleNamespace(user_id="owner"), SimpleNamespace(user_id="other", is_public=False)
        public = SimpleNamespace(user_id="other", is_public=True)
        assert _owned(mine, request) is mine
        assert _owned(theirs, request) is None
        assert _owned(public, request) is None
        assert _owned(public, request, public_ok=True) is public
        assert _owned(None, request) is None

    def test_workflow_step_failure_is_not_counted_as_done(self):
        from openeo_ai.tools.workflow_executor import _raise_if_tool_failed

        ok = {"content": [{"type": "text", "text": "fine"}]}
        assert _raise_if_tool_failed(ok) is ok
        with pytest.raises(RuntimeError, match="Access denied"):
            _raise_if_tool_failed({"content": [{"type": "text", "text": "Access denied"}], "isError": True})

    def test_workflow_sees_tools_registered_after_it(self):
        from openeo_ai.sdk import inprocess_tools
        from openeo_ai.sdk.client import OpenEOAIConfig

        seen = {}
        original = __import__("openeo_ai.tools.workflow_executor", fromlist=["x"]).create_workflow_executor_tools

        def capture(config, tool_handlers=None):
            seen["tools"] = tool_handlers
            return original(config, tool_handlers=tool_handlers)

        import openeo_ai.tools.workflow_executor as we

        patch = pytest.MonkeyPatch()
        patch.setattr(we, "create_workflow_executor_tools", capture)
        try:
            handlers = inprocess_tools._create_all_handlers(OpenEOAIConfig())
        finally:
            patch.undo()
        assert "assess_result_quality" in seen["tools"]
        assert set(seen["tools"]) == set(handlers)

    def test_finished_run_with_a_trailing_event_is_not_called_interrupted(self, wi):
        _finish_a_run(wi, [{"type": "text", "content": "all"}, {"type": "done"}, {"type": "trace", "id": "t"}])
        run = asyncio.run(wi._recover_run("s1", "u1"))
        assert "error" not in [event["type"] for event, _ in run._events]

    def test_older_run_cannot_delete_a_newer_runs_log(self, wi):
        async def scenario():
            old = wi._QueryRun("shared", "u")
            old.start(asyncio.sleep(0))
            await old.task
            new = wi._QueryRun("shared", "u")
            new.start(asyncio.sleep(0))
            await new.task
            wi._query_runs["shared"] = new
            old._forget()
            return new._log_path.exists()

        assert asyncio.run(scenario())

    def test_log_write_failure_does_not_stop_a_query(self, wi, monkeypatch):
        def fail(session):
            raise OSError("disk full")

        monkeypatch.setattr(wi, "_run_log_path", fail)

        async def scenario():
            run = wi._QueryRun("x", "u")

            async def body():
                await run.send_json({"type": "text", "content": "fine"})

            run.start(body())
            await run.task
            return run

        # The run kept sending after the log failed, and still ended with its done.
        assert [event["type"] for event, _ in asyncio.run(scenario())._events] == ["text", "done"]

    def test_run_log_is_capped(self, wi, monkeypatch):
        monkeypatch.setattr(wi, "_RUN_BUFFER_MAX_BYTES", 100)

        async def scenario():
            run = wi._QueryRun("cap", "u")
            run.start(asyncio.sleep(0))
            for _ in range(10):
                await run.send_json({"type": "text", "content": "x" * 60})
            await run.task
            return run._log_path.stat().st_size

        assert asyncio.run(scenario()) < 400

    def test_evidence_values_that_are_not_paths_are_ignored(self):
        from openeo_ai.storage.result_access import artifact_paths

        artifacts = {"result": {"href": "/data/r.tif", "media_type": "image/tiff"}, "note": "NDVI/EVI", "date": "2026/01/02"}
        assert artifact_paths(artifacts) == {"/data/r.tif"}

    def test_dnbr_caution_handles_the_real_timestamp_format(self):
        from openeo_ai.web_interface import _dnbr_sign_warning

        assert _dnbr_sign_warning({"title": "dNBR", "created_at": "2026-11-06T17:00:00"})
        assert _dnbr_sign_warning({"title": "dNBR", "created_at": "2026-11-06T19:00:00"}) is None

    def test_default_scopes_are_those_of_the_creator(self):
        from openeo_ai.api.schemas import APIKeyCreate

        assert "scopes" not in APIKeyCreate(name="x").model_fields_set
        assert "scopes" in APIKeyCreate(name="x", scopes=["results"]).model_fields_set

    def test_notes_survive_replacing_an_agent(self):
        from openeo_ai.sdk.claude_sdk_bridge import _turn_notes

        assert _turn_notes({"client": object(), "last_trace_id": "t", "turn_tool_names": [1], "ws_ref": {}}) == {
            "last_trace_id": "t", "turn_tool_names": [1]}

    def test_gdal_allows_both_tiff_spellings(self):
        import openeo_ai.agentic_eo.executor as module

        source = __import__("inspect").getsource(module)
        assert '".tif,.tiff"' in source


def test_template_in_use_reports_a_conflict(monkeypatch):
    from openeo_ai.batch import routes

    class Store:
        def delete_template(self, template_id, user_id):
            raise RuntimeError('violates foreign key constraint "schedules_template_id_fkey"')

    monkeypatch.setattr(routes, "_get_template_store", lambda: Store())
    request = SimpleNamespace(state=SimpleNamespace(user_id="u"))
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(routes.delete_template("t", request))
    assert conflict.value.status_code == 409


def test_a_run_ends_with_exactly_one_done(wi):
    async def scenario(events):
        run = wi._QueryRun("s2", "u1")

        async def body():
            for event in events:
                await run.send_json(event)

        run.start(body())
        await run.task
        return [event["type"] for event, _ in run._events]

    # Stop and the specialist flow can each send one; only the first counts.
    assert asyncio.run(scenario([{"type": "text", "content": "a"}, {"type": "done"}, {"type": "done"}])) == ["text", "done"]
    # A task that ends without one, for example after an error, still ends the turn.
    assert asyncio.run(scenario([{"type": "error", "content": "boom"}])) == ["error", "done"]


def test_a_crashed_run_still_ends_the_turn(wi):
    async def scenario():
        run = wi._QueryRun("s3", "u1")

        async def body():
            await run.send_json({"type": "text", "content": "partial"})
            raise RuntimeError("bridge failed")

        run.start(body())
        try:
            await run.task
        except RuntimeError:
            pass
        return [event["type"] for event, _ in run._events]

    assert asyncio.run(scenario()) == ["text", "done"]


def test_a_reconnecting_tab_is_not_asked_a_closed_question(wi):
    class Socket:
        def __init__(self):
            self.sent = []

        async def send_json(self, data, **kwargs):
            self.sent.append(data["type"])

    async def scenario():
        run = wi._QueryRun("s4", "u1")
        for event in ({"type": "text", "content": "a"}, {"type": "clarification", "questions": [{}]},
                      {"type": "clarification_closed"}, {"type": "text", "content": "b"}):
            await run.send_json(event)
        tab = Socket()
        await run.attach(tab, 0)
        return tab.sent

    assert asyncio.run(scenario()) == ["text", "clarification_closed", "text"]
