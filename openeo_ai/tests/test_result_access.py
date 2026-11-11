# ABOUTME: Per-user result access. Regressions from the 2026-11-06 audit, where any
# ABOUTME: signed-in user could read another user's rasters and jobs by path or job id.
import asyncio
import base64
import json
import uuid

import httpx
import pytest

from openeo_ai.storage import result_access as ra

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
JOB = "86a70cc7-14ae-4314-aa85-e60d774b844b"


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    """Result root and home in tmp, with Alice owning JOB and saved result abc12345."""
    monkeypatch.setenv("RESULT_STORAGE_PATH", str(tmp_path / "results-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ra, "USER_OUTPUT_ROOT", tmp_path / "outputs" / "users")
    monkeypatch.setattr(ra, "can_read_job", lambda job, user: job == JOB and user == ALICE)
    monkeypatch.setattr(ra, "can_read_saved_result", lambda save, user: save == "abc12345" and user == ALICE)
    monkeypatch.setattr(ra, "_in_user_evidence", lambda path, user: False)
    return tmp_path


class TestCanReadPath:
    def test_job_result_is_readable_only_by_its_owner(self, storage):
        path = str(storage / "results-root" / "results" / JOB / "result.tif")
        assert ra.can_read_path(path, ALICE) is True
        assert ra.can_read_path(path, BOB) is False

    def test_saved_result_follows_saved_result_access(self, storage):
        path = str(storage / "home" / ".openeo_jobs" / "results" / "abc12345" / "result.tif")
        assert ra.can_read_path(path, ALICE) is True
        assert ra.can_read_path(path, BOB) is False

    def test_output_folder_is_private(self, storage):
        path = str(ra.user_output_dir(ALICE) / "report.html")
        assert ra.can_read_path(path, ALICE) is True
        assert ra.can_read_path(path, BOB) is False

    @pytest.mark.parametrize("path", [
        "/etc/passwd",
        "/tmp/stray.tif",
        "/var/lib/jonaai/.claude.json",
    ])
    def test_untraceable_files_are_denied(self, storage, path):
        assert ra.can_read_path(path, ALICE) is False

    def test_traversal_out_of_own_folder_is_denied(self, storage):
        path = str(ra.user_output_dir(ALICE) / ".." / user_slug(BOB) / "report.html")
        assert ra.can_read_path(path, ALICE) is False

    def test_output_root_itself_is_not_a_user_file(self, storage):
        assert ra.can_read_path(str(ra.USER_OUTPUT_ROOT / "stray.tif"), ALICE) is False

    def test_evidence_grants_access(self, storage, monkeypatch):
        monkeypatch.setattr(ra, "_in_user_evidence", lambda path, user: user == ALICE)
        assert ra.can_read_path("/somewhere/evidence.tif", ALICE) is True
        assert ra.can_read_path("/somewhere/evidence.tif", BOB) is False


def user_slug(user):
    return ra.user_folder_name(user)


def test_user_folder_name_cannot_escape():
    assert "/" not in ra.user_folder_name("../../etc")
    assert ".." not in ra.user_folder_name("../../etc")


def test_job_lookup_errors_deny(monkeypatch):
    assert ra.can_read_job("not-a-uuid", ALICE) is False


def test_owner_id_matches_the_openeo_login():
    assert ra.openeo_user_uuid(ALICE) == str(uuid.uuid5(uuid.NAMESPACE_DNS, f"dev-{ALICE}"))


class TestJobToolsIdentity:
    def _login(self, tools):
        request = httpx.Request("GET", "http://x/jobs", headers={"Authorization": tools.auth_header})
        asyncio.run(tools._act_as_current_user(request))
        decoded = base64.b64decode(request.headers["Authorization"][6:]).decode()
        return decoded

    def test_requests_log_in_as_the_current_user(self):
        from openeo_ai.sdk.user_context import set_current_user_id
        from openeo_ai.tools.job_tools import JobTools

        tools = JobTools("http://x", auth_token="Basic " + base64.b64encode(b"dev:secret").decode())
        token = set_current_user_id(ALICE)
        try:
            assert self._login(tools) == f"{ALICE}:secret"
        finally:
            from openeo_ai.sdk import user_context
            user_context._current_user_ref.reset(token)

    def test_background_work_acts_as_the_given_user(self):
        from openeo_ai.tools.job_tools import JobTools

        tools = JobTools("http://x", auth_token="Basic " + base64.b64encode(b"dev:secret").decode(), act_as_user=BOB)
        assert self._login(tools) == f"{BOB}:secret"

    def test_no_user_keeps_the_configured_login(self):
        from openeo_ai.tools.job_tools import JobTools

        tools = JobTools("http://x", auth_token="Basic " + base64.b64encode(b"dev:secret").decode())
        assert self._login(tools) == "dev:secret"

    def test_explicit_token_is_left_alone(self):
        from openeo_ai.sdk.user_context import set_current_user_id
        from openeo_ai.tools.job_tools import JobTools

        tools = JobTools("http://x", auth_token="Bearer abc")
        request = httpx.Request("GET", "http://x/jobs", headers={"Authorization": "Bearer abc"})
        token = set_current_user_id(ALICE)
        try:
            asyncio.run(tools._act_as_current_user(request))
        finally:
            from openeo_ai.sdk import user_context
            user_context._current_user_ref.reset(token)
        assert request.headers["Authorization"] == "Bearer abc"


class TestToolEnforcement:
    def _call(self, args, user=BOB):
        from openeo_ai.sdk import user_context
        from openeo_ai.sdk.inprocess_tools import _wrap_handler

        ran = []

        async def handler(received):
            ran.append(received)
            return {"content": [{"type": "text", "text": "secret stats"}]}

        async def run():
            token = user_context.set_current_user_id(user)
            try:
                return await _wrap_handler("result_inspect", handler)(args)
            finally:
                user_context._current_user_ref.reset(token)

        return asyncio.run(run()), ran

    @pytest.mark.parametrize("key", ["file_path", "file_path_a", "file_path_b", "input_path", "geotiff_path", "before_path", "after_path"])
    def test_other_users_file_is_refused_before_the_tool_runs(self, storage, key):
        path = str(storage / "results-root" / "results" / JOB / "result.tif")
        result, ran = self._call({key: path})
        assert ran == []
        assert result.get("isError") is True
        assert "secret stats" not in json.dumps(result)

    def test_owner_can_use_their_file(self, storage):
        path = str(storage / "results-root" / "results" / JOB / "result.tif")
        result, ran = self._call({"file_path": path}, user=ALICE)
        assert len(ran) == 1
        assert "secret stats" in json.dumps(result)

    def test_other_users_job_is_refused(self, storage):
        result, ran = self._call({"job_id": JOB})
        assert ran == []
        assert result.get("isError") is True

    def test_output_path_is_moved_into_the_users_folder(self, storage):
        target = str(ra.user_output_dir(ALICE) / "overwrite.tif")
        result, ran = self._call({"output_path": target})
        assert ran[0]["output_path"] == str(ra.user_output_dir(BOB) / "overwrite.tif")

    def test_no_current_user_is_not_blocked(self, storage):
        result, ran = self._call({"file_path": "/tmp/anything.tif"}, user=None)
        assert len(ran) == 1


class TestEndpoints:
    @pytest.fixture()
    def client(self, storage, monkeypatch):
        import rasterio
        import numpy as np
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from rasterio.transform import from_origin
        from openeo_ai import web_routes

        path = storage / "results-root" / "results" / JOB / "result.tif"
        path.parent.mkdir(parents=True)
        with rasterio.open(path, "w", driver="GTiff", height=2, width=2, count=1, dtype="float32",
                           crs="EPSG:4326", transform=from_origin(0, 1, 0.5, 0.5)) as ds:
            ds.write(np.ones((1, 2, 2), dtype="float32"))
        monkeypatch.setattr(web_routes, "_ALLOWED_DIRS", [storage.resolve()])

        app = FastAPI()
        app.include_router(web_routes.router)
        identity = {"sub": ALICE}
        app.dependency_overrides[web_routes.require_auth] = lambda: identity
        app.dependency_overrides[web_routes.require_auth_or_query_token] = lambda: identity
        return TestClient(app), str(path), identity

    @pytest.mark.parametrize("route,params", [
        ("/raster-stats", {}),
        ("/pixel-value", {"lat": 0.75, "lng": 0.25}),
        ("/render-raster", {}),
    ])
    def test_other_user_gets_403(self, client, route, params):
        http, path, identity = client
        identity["sub"] = BOB
        response = http.get(route, params={"path": path, **params})
        assert response.status_code == 403

    def test_owner_reads_stats(self, client):
        http, path, _ = client
        response = http.get("/raster-stats", params={"path": path})
        assert response.status_code == 200
        assert response.json()["mean"] == 1.0

    def test_head_is_also_checked(self, client):
        http, path, identity = client
        identity["sub"] = BOB
        assert http.head("/render-raster", params={"path": path}).status_code == 403
        identity["sub"] = ALICE
        assert http.head("/render-raster", params={"path": path}).status_code == 200


class TestLoadSavedJob:
    def _run(self, monkeypatch, user):
        import openeo_ai.web_interface as wi
        from openeo_ai.storage import job_archive

        sent = []

        class Socket:
            async def send_json(self, data, **kwargs):
                sent.append(data)

        monkeypatch.setattr(ra, "can_read_saved_result", lambda save, who: who == ALICE)
        monkeypatch.setattr(job_archive, "get_job", lambda save: {
            "result_path": "/nowhere/result.tif", "colormap": "viridis", "title": "t", "bounds": [0, 0, 1, 1],
        })
        return asyncio.run(wi._send_saved_job(Socket(), "abc12345", user)), sent

    def test_other_user_sees_not_found(self, monkeypatch):
        ok, sent = self._run(monkeypatch, BOB)
        assert ok is False
        assert sent[0]["type"] == "error" and "not found" in sent[0]["content"]

    def test_no_user_sees_not_found(self, monkeypatch):
        ok, _ = self._run(monkeypatch, None)
        assert ok is False


class TestOpenEOJobRoutes:
    def _register(self, monkeypatch, owner):
        from openeo_app.registers import jobs

        register = jobs.ExecutableJobsRegister.__new__(jobs.ExecutableJobsRegister)
        job = type("Job", (), {"user_id": owner})()
        monkeypatch.setattr(jobs, "get", lambda get_model, primary_key: job)
        return jobs, register

    def _user(self, user_id):
        return type("User", (), {"user_id": user_id})()

    @pytest.mark.parametrize("route,args", [
        ("get_job", ()),
        ("estimate", ()),
        ("update_job", (None,)),
    ])
    def test_other_users_job_is_forbidden(self, monkeypatch, route, args):
        from fastapi.exceptions import HTTPException

        _, register = self._register(monkeypatch, "owner")
        with pytest.raises(HTTPException) as caught:
            getattr(register, route)(JOB, *args, self._user("someone-else"))
        assert caught.value.status_code == 403

    def test_owner_passes_the_check(self, monkeypatch):
        _, register = self._register(monkeypatch, "owner")
        register._require_owner(JOB, self._user("owner"))

    def test_missing_job_is_left_to_the_route(self, monkeypatch):
        from openeo_app.registers import jobs

        register = jobs.ExecutableJobsRegister.__new__(jobs.ExecutableJobsRegister)
        monkeypatch.setattr(jobs, "get", lambda get_model, primary_key: None)
        register._require_owner(JOB, self._user("anyone"))

