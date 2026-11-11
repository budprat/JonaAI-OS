# ABOUTME: Synchronous repository for persistent Earth Intelligence resources.

from __future__ import annotations

import threading
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, desc, func, or_, select

from openeo_ai.lifecycle import DELIVERY, OBSERVATION, SIGNAL, WATCH, Lifecycle, check_user_move
from openeo_ai.storage.job_archive import _get_session
from openeo_ai.storage.models import (
    Base,
    Baseline,
    CatalogItem,
    Delivery,
    EarthAsset,
    EarthObservation,
    EarthSignal,
    EarthWatch,
    EvidencePackage,
    ObservationEvent,
    SavedResult,
)

_tables_lock = threading.Lock()
_tables_ready = False


def _clean_update(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def delivery_retry_delay(attempts: int) -> timedelta:
    """Wait before retrying a delivery after `attempts` failed tries: 1, 2, 4... minutes, at most an hour."""
    return timedelta(seconds=min(60 * 2 ** max(attempts - 1, 0), 3600))


def _signal_target(current: str, status: Optional[str], acknowledged: Optional[bool],
                   resolved: Optional[bool], was_acknowledged: bool = False) -> str:
    """Turn a signal update's status and acknowledged/resolved flags into one target status."""
    if status is not None:
        return status
    if resolved is True:
        return "resolved"
    if resolved is False and current == "resolved":
        keep_ack = acknowledged is True or (acknowledged is None and was_acknowledged)
        return "acknowledged" if keep_ack else "open"
    if acknowledged is True:
        return "acknowledged" if current == "open" else current
    if acknowledged is False and current == "acknowledged":
        return "open"
    return current


def _move_status(lifecycle: Lifecycle, row: Any, data: Dict[str, Any], by_user: bool = True) -> None:
    """Check a requested status move against its lifecycle before any field is written.

    By default the mover is an API caller, so check_user_move also limits where it may move
    from; the worker passes by_user=False for its own retries."""
    target = data.get("status")
    if target is None:
        return
    if target == "canceled":
        target = data["status"] = "cancelled"
    if by_user:
        check_user_move(lifecycle, row.status, target)
    else:
        lifecycle.check(row.status, target)


def _parse_cadence(cadence: Optional[str]) -> Optional[timedelta]:
    if not cadence:
        return None

    value = cadence.strip().lower()
    named = {
        "hourly": timedelta(hours=1),
        "daily": timedelta(days=1),
        "weekly": timedelta(days=7),
        "monthly": timedelta(days=30),
    }
    if value in named:
        return named[value]

    # Minimal ISO-8601 duration support for common operator input: PT6H, P1D, P2W.
    import re

    match = re.fullmatch(r"p(?:(?P<days>\d+)d)?(?:(?P<weeks>\d+)w)?(?:t(?P<hours>\d+)h)?", value)
    if match:
        days = int(match.group("days") or 0)
        weeks = int(match.group("weeks") or 0)
        hours = int(match.group("hours") or 0)
        delta = timedelta(days=days + (weeks * 7), hours=hours)
        return delta if delta.total_seconds() > 0 else None

    match = re.fullmatch(r"(?P<count>\d+)\s*(?P<unit>minute|minutes|hour|hours|day|days|week|weeks)", value)
    if match:
        count = int(match.group("count"))
        unit = match.group("unit")
        if unit.startswith("minute"):
            return timedelta(minutes=count)
        if unit.startswith("hour"):
            return timedelta(hours=count)
        if unit.startswith("day"):
            return timedelta(days=count)
        if unit.startswith("week"):
            return timedelta(days=count * 7)

    return None


def _window_start_from_policy(policy: Dict[str, Any], now: datetime) -> Optional[datetime]:
    lookback_days = policy.get("lookback_days") or policy.get("window_days")
    lookback_hours = policy.get("lookback_hours") or policy.get("window_hours")
    try:
        if lookback_days is not None:
            return now - timedelta(days=float(lookback_days))
        if lookback_hours is not None:
            return now - timedelta(hours=float(lookback_hours))
    except (TypeError, ValueError):
        return None
    return None


class EarthIntelligenceRepository:
    """Data access for assets, watches, observations, evidence, and signals."""

    def _session(self):
        session = _get_session()
        self._ensure_tables(session)
        return session

    @staticmethod
    def _ensure_tables(session) -> None:
        global _tables_ready
        if _tables_ready:
            return
        with _tables_lock:
            if _tables_ready:
                return
            engine = session.get_bind()
            Base.metadata.create_all(
                bind=engine,
                tables=[
                    EarthAsset.__table__,
                    EarthWatch.__table__,
                    CatalogItem.__table__,
                    EarthObservation.__table__,
                    ObservationEvent.__table__,
                    EvidencePackage.__table__,
                    Baseline.__table__,
                    EarthSignal.__table__,
                    Delivery.__table__,
                ],
                checkfirst=True,
            )
            _tables_ready = True

    # ── Assets ──────────────────────────────────────────────────────

    def list_assets(
        self,
        user_id: str,
        project_id: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            stmt = select(EarthAsset).where(EarthAsset.user_id == user_id)
            if project_id:
                stmt = stmt.where(EarthAsset.project_id == project_id)
            if not include_archived:
                stmt = stmt.where(EarthAsset.archived_at == None)
            stmt = stmt.order_by(desc(EarthAsset.updated_at)).limit(limit).offset(offset)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]
        finally:
            session.close()

    def create_asset(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        session = self._session()
        try:
            asset = EarthAsset(user_id=user_id, **data)
            session.add(asset)
            session.commit()
            session.refresh(asset)
            return asset.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_asset(self, user_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            row = session.execute(
                select(EarthAsset).where(EarthAsset.user_id == user_id, EarthAsset.id == asset_id)
            ).scalar_one_or_none()
            return row.to_dict() if row else None
        finally:
            session.close()

    def delete_asset(self, user_id: str, asset_id: str) -> bool:
        """Delete a user's asset. Its watches go with it; evidence keeps its record."""
        session = self._session()
        try:
            # A direct delete, so the database's foreign keys decide what follows. Through
            # the ORM, session.delete() would first try to blank each watch's asset_id.
            deleted = session.execute(
                delete(EarthAsset).where(EarthAsset.user_id == user_id, EarthAsset.id == asset_id)
            ).rowcount
            session.commit()
            return bool(deleted)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_asset(self, user_id: str, asset_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            row = session.execute(
                select(EarthAsset).where(EarthAsset.user_id == user_id, EarthAsset.id == asset_id)
            ).scalar_one_or_none()
            if not row:
                return None
            archived = data.pop("archived", None)
            for key, value in _clean_update(data).items():
                if hasattr(row, key):
                    setattr(row, key, value)
            if archived is not None:
                row.archived_at = datetime.utcnow() if archived else None
            row.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(row)
            return row.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Watches ─────────────────────────────────────────────────────

    def list_watches(
        self,
        user_id: str,
        asset_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            stmt = select(EarthWatch).where(EarthWatch.user_id == user_id)
            if asset_id:
                stmt = stmt.where(EarthWatch.asset_id == asset_id)
            if status:
                stmt = stmt.where(EarthWatch.status == status)
            stmt = stmt.order_by(desc(EarthWatch.updated_at)).limit(limit).offset(offset)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]
        finally:
            session.close()

    def create_watch(self, user_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            asset = session.execute(
                select(EarthAsset).where(
                    EarthAsset.user_id == user_id,
                    EarthAsset.id == data["asset_id"],
                    EarthAsset.archived_at == None,
                )
            ).scalar_one_or_none()
            if not asset:
                return None
            if not data.get("project_id"):
                data["project_id"] = asset.project_id
            # A cadence makes a watch scheduled. The worker's due-query
            # keys on next_run_at only (enqueue_due_watches), so derive it here —
            # the single source of truth for all callers (monitor_tools exclude_unset,
            # api_v1/routes plain model_dump). Explicit next_run_at always wins.
            if data.get("cadence") and data.get("next_run_at") is None:
                data["next_run_at"] = datetime.utcnow()
                data["trigger_type"] = "scheduled"  # display label; scheduler uses next_run_at
            watch = EarthWatch(user_id=user_id, **data)
            session.add(watch)
            session.commit()
            session.refresh(watch)
            return watch.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_watch(self, user_id: str, watch_id: str) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            row = session.execute(
                select(EarthWatch).where(EarthWatch.user_id == user_id, EarthWatch.id == watch_id)
            ).scalar_one_or_none()
            return row.to_dict() if row else None
        finally:
            session.close()

    def update_watch(self, user_id: str, watch_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            row = session.execute(
                select(EarthWatch).where(EarthWatch.user_id == user_id, EarthWatch.id == watch_id)
            ).scalar_one_or_none()
            if not row:
                return None
            _move_status(WATCH, row, data)
            for key, value in _clean_update(data).items():
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(row)
            return row.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Observations ────────────────────────────────────────────────

    def create_observation_for_watch(self, user_id: str, watch_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            watch = session.execute(
                select(EarthWatch).where(EarthWatch.user_id == user_id, EarthWatch.id == watch_id)
            ).scalar_one_or_none()
            if not watch:
                return None
            observation = EarthObservation(
                user_id=user_id,
                watch_id=watch.id,
                asset_id=watch.asset_id,
                status="queued",
                window_start=data.get("window_start"),
                window_end=data.get("window_end"),
                source_catalog_item_ids=data.get("source_catalog_item_ids") or [],
                quality_metrics=data.get("quality_metrics") or {},
                cost_estimate=data.get("cost_estimate") or {},
            )
            watch.last_run_at = datetime.utcnow()
            session.add(observation)
            session.flush()
            session.add(
                ObservationEvent(
                    user_id=user_id,
                    observation_id=observation.id,
                    event_type="queued",
                    message="Observation queued for execution.",
                    payload={"watch_id": watch.id, "asset_id": watch.asset_id, "source": "manual_run"},
                )
            )
            session.commit()
            session.refresh(observation)
            return observation.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_observations(
        self,
        user_id: str,
        asset_id: Optional[str] = None,
        watch_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            stmt = select(EarthObservation).where(EarthObservation.user_id == user_id)
            if asset_id:
                stmt = stmt.where(EarthObservation.asset_id == asset_id)
            if watch_id:
                stmt = stmt.where(EarthObservation.watch_id == watch_id)
            if status:
                stmt = stmt.where(EarthObservation.status == status)
            stmt = stmt.order_by(desc(EarthObservation.created_at)).limit(limit).offset(offset)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]
        finally:
            session.close()

    def get_observation(self, user_id: str, observation_id: str) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            row = session.execute(
                select(EarthObservation).where(
                    EarthObservation.user_id == user_id,
                    EarthObservation.id == observation_id,
                )
            ).scalar_one_or_none()
            return row.to_dict() if row else None
        finally:
            session.close()

    def update_observation(self, user_id: str, observation_id: str, data: Dict[str, Any],
                           *, by_user: bool = True) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            row = session.execute(
                select(EarthObservation).where(
                    EarthObservation.user_id == user_id,
                    EarthObservation.id == observation_id,
                )
            ).scalar_one_or_none()
            if not row:
                return None
            _move_status(OBSERVATION, row, data, by_user=by_user)
            for key, value in _clean_update(data).items():
                if hasattr(row, key):
                    setattr(row, key, value)
            if data.get("status") in {"completed", "failed", "cancelled"}:
                row.locked_by = None
                row.locked_at = None
                row.next_attempt_at = None
            elif data.get("status") == "queued":
                row.locked_by = None
                row.locked_at = None
            session.commit()
            session.refresh(row)
            return row.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def start_observation(self, user_id: str, observation_id: str, worker_id: str) -> Optional[Dict[str, Any]]:
        """Move one queued or failed observation to running for an immediate run.

        Returns None when it is no longer startable, for example because a worker claimed it
        first, so the same observation never runs twice at once.
        """
        session = self._session()
        try:
            row = session.execute(
                select(EarthObservation)
                .where(
                    EarthObservation.user_id == user_id,
                    EarthObservation.id == observation_id,
                    EarthObservation.status.in_(("queued", "failed")),
                )
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if not row:
                return None
            now = datetime.utcnow()
            row.status = "running"
            row.started_at = now
            row.locked_by = worker_id
            row.locked_at = now
            row.error = None
            session.commit()
            session.refresh(row)
            return row.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def claim_next_observation(
        self,
        worker_id: str,
        user_id: Optional[str] = None,
        stale_after_seconds: int = 3600,
    ) -> Optional[Dict[str, Any]]:
        """Claim the oldest queued observation, or a stale running one.

        PostgreSQL deployments use row locks when available. SQLite/local
        development ignores the lock hint but still exercises the same state
        transition path.
        """
        session = self._session()
        try:
            now = datetime.utcnow()
            stale_cutoff = now - timedelta(seconds=stale_after_seconds)
            filters = [
                or_(
                    EarthObservation.status == "queued",
                    (
                        (EarthObservation.status == "running")
                        & or_(
                            (
                                (EarthObservation.locked_at != None)
                                & (EarthObservation.locked_at < stale_cutoff)
                            ),
                            # started_at survives deferrals, so it only counts for a row
                            # that was never locked. Otherwise a fresh claim looks stale.
                            (
                                (EarthObservation.locked_at == None)
                                & (EarthObservation.started_at != None)
                                & (EarthObservation.started_at < stale_cutoff)
                            ),
                        )
                    ),
                )
            ]
            filters.append(
                or_(
                    EarthObservation.next_attempt_at == None,
                    EarthObservation.next_attempt_at <= now,
                )
            )
            if user_id:
                filters.append(EarthObservation.user_id == user_id)
            stmt = (
                select(EarthObservation)
                .where(*filters)
                .order_by(EarthObservation.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = session.execute(stmt).scalar_one_or_none()
            if not row:
                return None

            recovered_stale = row.status == "running"
            row.status = "running"
            row.started_at = row.started_at or now
            row.locked_by = worker_id
            row.locked_at = now
            row.attempt_count = (row.attempt_count or 0) + 1
            row.error = None
            metrics = dict(row.quality_metrics or {})
            metrics["worker"] = {
                "id": worker_id,
                "claimed_at": now.isoformat(),
                "recovered_stale": recovered_stale,
            }
            row.quality_metrics = metrics
            session.add(
                ObservationEvent(
                    user_id=row.user_id,
                    observation_id=row.id,
                    event_type="claimed",
                    message="Observation claimed by Earth Intelligence worker.",
                    payload={"worker_id": worker_id, "recovered_stale": recovered_stale},
                )
            )
            session.commit()
            session.refresh(row)
            return row.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_observation_context(self, user_id: str, observation_id: str) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            observation = session.execute(
                select(EarthObservation).where(
                    EarthObservation.user_id == user_id,
                    EarthObservation.id == observation_id,
                )
            ).scalar_one_or_none()
            if not observation:
                return None

            watch = None
            if observation.watch_id:
                watch = session.execute(
                    select(EarthWatch).where(
                        EarthWatch.user_id == user_id,
                        EarthWatch.id == observation.watch_id,
                    )
                ).scalar_one_or_none()

            asset = None
            if observation.asset_id:
                asset = session.execute(
                    select(EarthAsset).where(
                        EarthAsset.user_id == user_id,
                        EarthAsset.id == observation.asset_id,
                    )
                ).scalar_one_or_none()

            return {
                "observation": observation.to_dict(),
                "watch": watch.to_dict() if watch else None,
                "asset": asset.to_dict() if asset else None,
            }
        finally:
            session.close()

    # ── Catalog Items ───────────────────────────────────────────────

    def list_catalog_items(
        self,
        provider: Optional[str] = None,
        collection_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            stmt = select(CatalogItem)
            if provider:
                stmt = stmt.where(CatalogItem.provider == provider)
            if collection_id:
                stmt = stmt.where(CatalogItem.collection_id == collection_id)
            stmt = stmt.order_by(desc(CatalogItem.datetime), desc(CatalogItem.ingested_at)).limit(limit).offset(offset)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]
        finally:
            session.close()

    def upsert_catalog_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            rows: List[CatalogItem] = []
            for item in items:
                existing = session.execute(
                    select(CatalogItem).where(
                        CatalogItem.provider == item["provider"],
                        CatalogItem.collection_id == item["collection_id"],
                        CatalogItem.stac_id == item["stac_id"],
                    )
                ).scalar_one_or_none()
                if existing:
                    for key, value in item.items():
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                    row = existing
                else:
                    row = CatalogItem(**item)
                    session.add(row)
                rows.append(row)
            session.commit()
            for row in rows:
                session.refresh(row)
            return [row.to_dict() for row in rows]
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_observations_for_catalog_items(
        self,
        user_id: str,
        watch_id: str,
        catalog_item_ids: List[str],
        quality_metrics: Optional[Dict[str, Any]] = None,
        cost_estimate: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            watch = session.execute(
                select(EarthWatch).where(EarthWatch.user_id == user_id, EarthWatch.id == watch_id)
            ).scalar_one_or_none()
            if not watch:
                return []

            created: List[EarthObservation] = []
            for catalog_item_id in catalog_item_ids:
                item = session.execute(
                    select(CatalogItem).where(CatalogItem.id == catalog_item_id)
                ).scalar_one_or_none()
                if not item:
                    continue

                existing = session.execute(
                    select(EarthObservation).where(
                        EarthObservation.user_id == user_id,
                        EarthObservation.watch_id == watch_id,
                        EarthObservation.source_catalog_item_ids == [catalog_item_id],
                    )
                ).scalar_one_or_none()
                if existing:
                    continue

                observation = EarthObservation(
                    user_id=user_id,
                    watch_id=watch.id,
                    asset_id=watch.asset_id,
                    status="queued",
                    window_start=item.datetime,
                    window_end=item.datetime,
                    source_catalog_item_ids=[catalog_item_id],
                    quality_metrics=quality_metrics or {"catalog_item": item.to_dict()},
                    cost_estimate=cost_estimate or {},
                )
                watch.last_run_at = datetime.utcnow()
                session.add(observation)
                session.flush()
                session.add(
                    ObservationEvent(
                        user_id=user_id,
                        observation_id=observation.id,
                        event_type="queued",
                        message="Observation queued from a catalog acquisition.",
                        payload={
                            "watch_id": watch.id,
                            "asset_id": watch.asset_id,
                            "catalog_item_id": catalog_item_id,
                            "source": "catalog_acquisition",
                        },
                    )
                )
                created.append(observation)

            session.commit()
            for observation in created:
                session.refresh(observation)
            return [observation.to_dict() for observation in created]
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_observation_event(
        self,
        user_id: str,
        observation_id: str,
        event_type: str,
        message: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            observation = session.execute(
                select(EarthObservation).where(
                    EarthObservation.user_id == user_id,
                    EarthObservation.id == observation_id,
                )
            ).scalar_one_or_none()
            if not observation:
                return None
            event = ObservationEvent(
                user_id=user_id,
                observation_id=observation_id,
                event_type=event_type,
                message=message,
                payload=payload or {},
            )
            session.add(event)
            session.commit()
            session.refresh(event)
            return event.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_observation_events(
        self,
        user_id: str,
        observation_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            stmt = select(ObservationEvent).where(ObservationEvent.user_id == user_id)
            if observation_id:
                stmt = stmt.where(ObservationEvent.observation_id == observation_id)
            stmt = stmt.order_by(desc(ObservationEvent.created_at)).limit(limit).offset(offset)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]
        finally:
            session.close()

    def oldest_due_watch_lag_seconds(self, now: Optional[datetime] = None) -> Optional[float]:
        """Seconds the most-overdue active watch has waited; None when nothing is
        due. A healthy scheduler keeps this near zero — the Dead-Man Tick reads
        it as the worker-independent liveness truth."""
        session = self._session()
        try:
            now = now or datetime.utcnow()
            stmt = select(func.min(EarthWatch.next_run_at)).where(
                EarthWatch.status == "active",
                EarthWatch.next_run_at != None,  # noqa: E711 — SQLAlchemy expression
                EarthWatch.next_run_at <= now,
            )
            oldest = session.execute(stmt).scalar_one_or_none()
            return (now - oldest).total_seconds() if oldest else None
        finally:
            session.close()

    def enqueue_due_watches(self, limit: int = 25, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Materialize scheduled watches whose next_run_at is due into observations."""
        session = self._session()
        try:
            now = datetime.utcnow()
            filters = [
                EarthWatch.status == "active",
                EarthWatch.next_run_at != None,
                EarthWatch.next_run_at <= now,
            ]
            if user_id:
                filters.append(EarthWatch.user_id == user_id)
            stmt = (
                select(EarthWatch)
                .where(*filters)
                .order_by(EarthWatch.next_run_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            watches = session.execute(stmt).scalars().all()
            observations: List[EarthObservation] = []
            for watch in watches:
                observation = EarthObservation(
                    user_id=watch.user_id,
                    watch_id=watch.id,
                    asset_id=watch.asset_id,
                    status="queued",
                    window_start=_window_start_from_policy(watch.temporal_policy or {}, now),
                    window_end=now,
                    source_catalog_item_ids=[],
                    quality_metrics={},
                    cost_estimate={},
                )
                cadence_delta = _parse_cadence(watch.cadence)
                watch.last_run_at = now
                watch.next_run_at = now + cadence_delta if cadence_delta else None
                session.add(observation)
                session.flush()
                session.add(
                    ObservationEvent(
                        user_id=watch.user_id,
                        observation_id=observation.id,
                        event_type="queued",
                        message="Scheduled watch materialized into an observation.",
                        payload={
                            "watch_id": watch.id,
                            "asset_id": watch.asset_id,
                            "source": "schedule",
                            "cadence": watch.cadence,
                            "next_run_at": watch.next_run_at.isoformat() if watch.next_run_at else None,
                        },
                    )
                )
                observations.append(observation)
            session.commit()
            for observation in observations:
                session.refresh(observation)
            return [observation.to_dict() for observation in observations]
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Evidence ────────────────────────────────────────────────────

    def create_evidence(self, user_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            observation_id = data.get("observation_id")
            if observation_id:
                observation = session.execute(
                    select(EarthObservation).where(
                        EarthObservation.user_id == user_id,
                        EarthObservation.id == observation_id,
                    )
                ).scalar_one_or_none()
                if not observation:
                    return None

            saved_result_id = None
            save_id = data.get("saved_result_save_id")
            if save_id:
                saved = session.execute(
                    select(SavedResult).where(
                        SavedResult.save_id == save_id,
                        (SavedResult.user_id == user_id) | (SavedResult.user_id == None),
                    )
                ).scalar_one_or_none()
                if not saved:
                    return None
                saved_result_id = saved.id

            evidence = EvidencePackage(
                user_id=user_id,
                observation_id=observation_id,
                saved_result_id=saved_result_id,
                saved_result_save_id=save_id,
                stac_item=data.get("stac_item") or {},
                source_items=data.get("source_items") or [],
                artifacts=data.get("artifacts") or {},
                statistics=data.get("statistics") or {},
                provenance=data.get("provenance") or {},
                process_graph=data.get("process_graph") or {},
                scf=data.get("scf"),
                trace_id=data.get("trace_id"),
            )
            session.add(evidence)
            session.commit()
            session.refresh(evidence)
            return evidence.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_evidence(
        self,
        user_id: str,
        observation_id: Optional[str] = None,
        saved_result_save_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            stmt = select(EvidencePackage).where(EvidencePackage.user_id == user_id)
            if observation_id:
                stmt = stmt.where(EvidencePackage.observation_id == observation_id)
            if saved_result_save_id:
                stmt = stmt.where(EvidencePackage.saved_result_save_id == saved_result_save_id)
            stmt = stmt.order_by(desc(EvidencePackage.created_at)).limit(limit).offset(offset)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]
        finally:
            session.close()

    def get_evidence(self, user_id: str, evidence_id: str) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            row = session.execute(
                select(EvidencePackage).where(
                    EvidencePackage.user_id == user_id,
                    EvidencePackage.id == evidence_id,
                )
            ).scalar_one_or_none()
            return row.to_dict() if row else None
        finally:
            session.close()

    # ── Baselines ───────────────────────────────────────────────────

    def list_baselines(
        self,
        user_id: str,
        asset_id: Optional[str] = None,
        watch_id: Optional[str] = None,
        metric: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            stmt = select(Baseline).where(Baseline.user_id == user_id)
            if asset_id:
                stmt = stmt.where(Baseline.asset_id == asset_id)
            if watch_id:
                stmt = stmt.where(Baseline.watch_id == watch_id)
            if metric:
                stmt = stmt.where(Baseline.metric == metric)
            stmt = stmt.order_by(desc(Baseline.updated_at)).limit(limit).offset(offset)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]
        finally:
            session.close()

    def update_baseline_sample(
        self,
        user_id: str,
        asset_id: str,
        watch_id: Optional[str],
        metric: str,
        value: float,
        season_key: Optional[str] = None,
        window_policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session = self._session()
        try:
            stmt = select(Baseline).where(
                Baseline.user_id == user_id,
                Baseline.asset_id == asset_id,
                Baseline.metric == metric,
            )
            stmt = stmt.where(Baseline.watch_id == watch_id) if watch_id else stmt.where(Baseline.watch_id == None)
            stmt = stmt.where(Baseline.season_key == season_key) if season_key else stmt.where(Baseline.season_key == None)
            baseline = session.execute(stmt).scalar_one_or_none()

            previous_stats = dict(baseline.stats or {}) if baseline else {}
            previous_mean = previous_stats.get("mean")
            previous_std = previous_stats.get("std")
            previous_count = int(previous_stats.get("count") or (baseline.sample_count if baseline else 0) or 0)

            count = previous_count + 1
            mean = float(previous_mean) if isinstance(previous_mean, (int, float)) else 0.0
            m2 = float(previous_stats.get("m2") or 0.0)
            delta = value - mean
            mean += delta / count
            delta2 = value - mean
            m2 += delta * delta2
            variance = m2 / (count - 1) if count > 1 else 0.0
            std = math.sqrt(max(variance, 0.0))
            stats = {
                "count": count,
                "mean": mean,
                "std": std,
                "variance": variance,
                "m2": m2,
                "min": min(float(previous_stats.get("min", value)), value),
                "max": max(float(previous_stats.get("max", value)), value),
                "last_value": value,
                "last_updated_at": datetime.utcnow().isoformat(),
            }

            if not baseline:
                baseline = Baseline(
                    user_id=user_id,
                    asset_id=asset_id,
                    watch_id=watch_id,
                    metric=metric,
                    season_key=season_key,
                    window_policy=window_policy or {},
                    stats=stats,
                    sample_count=count,
                )
                session.add(baseline)
            else:
                baseline.window_policy = window_policy or baseline.window_policy or {}
                baseline.stats = stats
                baseline.sample_count = count
                baseline.version += 1
                baseline.updated_at = datetime.utcnow()

            session.commit()
            session.refresh(baseline)
            return {
                "baseline": baseline.to_dict(),
                "previous_mean": float(previous_mean) if isinstance(previous_mean, (int, float)) else None,
                "previous_std": float(previous_std) if isinstance(previous_std, (int, float)) else None,
                "previous_count": previous_count,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Signals ─────────────────────────────────────────────────────

    def create_signal(self, user_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """None if an asset, watch or observation it names isn't the caller's, as for watches,
        evidence and deliveries."""
        session = self._session()
        try:
            for model, key in ((EarthAsset, "asset_id"), (EarthWatch, "watch_id"),
                               (EarthObservation, "observation_id")):
                if data.get(key) and session.execute(
                    select(model.id).where(model.id == data[key], model.user_id == user_id)
                ).first() is None:
                    return None
            signal = EarthSignal(user_id=user_id, **data)
            session.add(signal)
            session.commit()
            session.refresh(signal)
            return signal.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_signals(
        self,
        user_id: str,
        asset_id: Optional[str] = None,
        watch_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            stmt = select(EarthSignal).where(EarthSignal.user_id == user_id)
            if asset_id:
                stmt = stmt.where(EarthSignal.asset_id == asset_id)
            if watch_id:
                stmt = stmt.where(EarthSignal.watch_id == watch_id)
            if status:
                stmt = stmt.where(EarthSignal.status == status)
            stmt = stmt.order_by(desc(EarthSignal.created_at)).limit(limit).offset(offset)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]
        finally:
            session.close()

    def update_signal(self, user_id: str, signal_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            row = session.execute(
                select(EarthSignal).where(EarthSignal.user_id == user_id, EarthSignal.id == signal_id)
            ).scalar_one_or_none()
            if not row:
                return None
            now = datetime.utcnow()
            target = _signal_target(row.status, data.pop("status", None),
                                    data.pop("acknowledged", None), data.pop("resolved", None),
                                    row.acknowledged_at is not None)
            SIGNAL.check(row.status, target)
            for key, value in _clean_update(data).items():
                if hasattr(row, key):
                    setattr(row, key, value)
            # The timestamps follow the status, so the two can't disagree.
            if target != row.status:
                row.status = target
                if target == "open":
                    row.acknowledged_at = None
                    row.resolved_at = None
                elif target == "acknowledged":
                    row.acknowledged_at = row.acknowledged_at or now
                    row.resolved_at = None
                elif target == "resolved":
                    row.resolved_at = now
            session.commit()
            session.refresh(row)
            return row.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Deliveries ──────────────────────────────────────────────────

    def create_delivery(self, user_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            signal = session.execute(
                select(EarthSignal).where(
                    EarthSignal.user_id == user_id,
                    EarthSignal.id == data["signal_id"],
                )
            ).scalar_one_or_none()
            if not signal:
                return None
            delivery = Delivery(
                user_id=user_id,
                signal_id=data["signal_id"],
                channel=data["channel"],
                target=data["target"],
                status=data.get("status") or "pending",
            )
            session.add(delivery)
            session.commit()
            session.refresh(delivery)
            return delivery.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_deliveries(
        self,
        user_id: str,
        signal_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        session = self._session()
        try:
            stmt = select(Delivery).where(Delivery.user_id == user_id)
            if signal_id:
                stmt = stmt.where(Delivery.signal_id == signal_id)
            if status:
                stmt = stmt.where(Delivery.status == status)
            stmt = stmt.order_by(desc(Delivery.created_at)).limit(limit).offset(offset)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]
        finally:
            session.close()

    def update_delivery(self, user_id: str, delivery_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = self._session()
        try:
            row = session.execute(
                select(Delivery).where(Delivery.user_id == user_id, Delivery.id == delivery_id)
            ).scalar_one_or_none()
            if not row:
                return None
            _move_status(DELIVERY, row, data)
            if data.get("status") == "pending" and row.status == "failed":
                # A hand retry gets a fresh attempt budget and goes out on the next tick.
                data.setdefault("attempt_count", 0)
                row.last_error = None
                row.next_attempt_at = None
            for key, value in _clean_update(data).items():
                if hasattr(row, key):
                    setattr(row, key, value)
            session.commit()
            session.refresh(row)
            return row.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def claim_pending_deliveries(
        self,
        limit: int = 10,
        user_id: Optional[str] = None,
        stale_after_seconds: int = 900,
    ) -> List[Dict[str, Any]]:
        """Atomically claim pending deliveries by flipping them to 'sending'.

        A pending row waits for its next_attempt_at. A row stuck at 'sending' whose
        claim is older than ``stale_after_seconds`` was abandoned mid-send and is
        taken again. Rows claimed before claimed_at existed fall back to created_at.

        Each returned dict carries the parent signal's severity/metric/value/
        explanation/asset_id so senders need no per-delivery follow-up queries.
        """
        session = self._session()
        try:
            now = datetime.utcnow()
            stale_cutoff = now - timedelta(seconds=stale_after_seconds)
            filters = [
                or_(
                    (Delivery.status == "pending")
                    & or_(Delivery.next_attempt_at == None, Delivery.next_attempt_at <= now),
                    (Delivery.status == "sending")
                    & or_(
                        Delivery.claimed_at < stale_cutoff,
                        (Delivery.claimed_at == None) & (Delivery.created_at < stale_cutoff),
                    ),
                )
            ]
            if user_id:
                filters.append(Delivery.user_id == user_id)
            stmt = (
                select(Delivery)
                .where(*filters)
                .order_by(Delivery.created_at)
                .limit(max(0, limit))
                .with_for_update(skip_locked=True)
            )
            rows = session.execute(stmt).scalars().all()
            if not rows:
                return []
            for row in rows:
                row.status = "sending"
                row.claimed_at = now
            session.commit()

            claimed = [row.to_dict() for row in rows]
            signal_ids = {item["signal_id"] for item in claimed}
            signals = {
                sig.id: sig
                for sig in session.execute(
                    select(EarthSignal).where(EarthSignal.id.in_(signal_ids))
                ).scalars().all()
            }
            for item in claimed:
                signal = signals.get(item["signal_id"])
                item["severity"] = signal.severity if signal else None
                item["metric"] = signal.metric if signal else None
                item["value"] = signal.value if signal else None
                item["explanation"] = signal.explanation if signal else None
                item["asset_id"] = signal.asset_id if signal else None
            return claimed
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def finish_delivery(
        self,
        delivery_id: str,
        *,
        success: bool,
        error: Optional[str] = None,
        retryable: bool = False,
        max_attempts: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """Record one dispatch attempt for a claimed delivery.

        Keyed by delivery id only: the global worker tick has no user scope
        and the worker is trusted (user-facing mutation stays on update_delivery).
        """
        session = self._session()
        try:
            row = session.execute(
                select(Delivery).where(Delivery.id == delivery_id)
            ).scalar_one_or_none()
            if not row:
                return None
            if row.status != "sending":
                # Someone else already settled it, or a user re-queued it meanwhile.
                return row.to_dict()
            row.attempt_count = (row.attempt_count or 0) + 1
            row.claimed_at = None
            if success:
                row.status = "sent"
                row.sent_at = datetime.utcnow()
                row.last_error = None
                row.next_attempt_at = None
            else:
                row.last_error = (error or "")[:2000]
                if retryable and row.attempt_count < max_attempts:
                    row.status = "pending"
                    row.next_attempt_at = datetime.utcnow() + delivery_retry_delay(row.attempt_count)
                else:
                    row.status = "failed"
                    row.next_attempt_at = None
            session.commit()
            session.refresh(row)
            return row.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
