# ABOUTME: Tools for listing and loading previously saved job results.
# Exposes saved_jobs_list, saved_jobs_load, saved_jobs_delete to the AI agent.

import json
from typing import Any, Dict


def create_saved_jobs_tools(config) -> Dict[str, Any]:
    """Create saved jobs tools dict for Claude SDK."""
    from ..storage.job_archive import list_jobs, get_job, delete_job
    from ..sdk.user_context import get_current_user_id

    async def _list_saved_jobs(args: Dict[str, Any]) -> Dict[str, Any]:
        """List all permanently saved job results (scoped to the current user)."""
        uid = get_current_user_id()
        if not uid:
            # Fail closed: list_jobs(user_id=None) would return EVERY tenant's rows.
            return {"content": [{"type": "text", "text": json.dumps({"count": 0, "jobs": []})}]}
        limit = args.get("limit", 20)
        jobs = list_jobs(limit=limit, user_id=uid)
        summary = []
        for j in jobs:
            summary.append({
                "save_id": j["save_id"],
                "title": j["title"],
                "created_at": j["created_at"],
                "size_bytes": j["size_bytes"],
                "has_bounds": j.get("bounds") is not None,
            })
        return {
            "content": [{"type": "text", "text": json.dumps({
                "count": len(summary),
                "jobs": summary
            })}]
        }

    async def _load_saved_job(args: Dict[str, Any]) -> Dict[str, Any]:
        """Load a saved job result for visualization on the map."""
        save_id = args["save_id"]
        uid = get_current_user_id()
        if not uid:  # fail closed: get_job(user_id=None) matches on save_id alone
            return {"content": [{"type": "text", "text": json.dumps(
                {"error": f"Saved job '{save_id}' not found"})}]}
        job = get_job(save_id, user_id=uid)
        if job is None:
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "error": f"Saved job '{save_id}' not found"
                })}]
            }
        # Return same structure as openeo_get_results so extract_visualization picks it up
        return {
            "content": [{"type": "text", "text": json.dumps({
                "output_path": job["result_path"],
                "path": job["result_path"],
                "bounds": job.get("bounds"),
                "colormap": job.get("colormap", "viridis"),
                "vmin": job.get("vmin"),
                "vmax": job.get("vmax"),
                "title": job["title"],
                "save_id": job["save_id"],
            })}]
        }

    async def _delete_saved_job(args: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a saved job result permanently (scoped to the current user)."""
        save_id = args["save_id"]
        uid = get_current_user_id()
        if not uid:  # fail closed: delete_job(user_id=None) deletes on save_id alone
            return {"content": [{"type": "text", "text": json.dumps(
                {"deleted": False, "save_id": save_id})}]}
        success = delete_job(save_id, user_id=uid)
        return {
            "content": [{"type": "text", "text": json.dumps({
                "deleted": success,
                "save_id": save_id
            })}]
        }

    return {
        "saved_jobs_list": _list_saved_jobs,
        "saved_jobs_load": _load_saved_job,
        "saved_jobs_delete": _delete_saved_job,
    }
