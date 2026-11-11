"""Phase 17: Persistent Earth Intelligence platform spine

Revision ID: phase17_earth_intelligence
Revises: phase15_project_members
Create Date: 2026-05-03

Adds durable Earth Intelligence domain tables:
- assets
- watches
- catalog_items
- observations
- observation_events
- evidence_packages
- baselines
- signals
- deliveries

These tables make recurring monitoring, evidence packages, baselines, signals,
and alert delivery first-class persisted resources instead of transient chat or
batch state.
"""
from alembic import op

revision = "phase17_earth_intelligence"
down_revision = "phase15_project_members"
branch_labels = None
depends_on = None


USER_TABLES = [
    "assets",
    "watches",
    "observations",
    "observation_events",
    "evidence_packages",
    "baselines",
    "signals",
    "deliveries",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
          id VARCHAR(36) PRIMARY KEY,
          user_id TEXT NOT NULL,
          project_id VARCHAR(36) REFERENCES projects(id) ON DELETE SET NULL,
          name VARCHAR(255) NOT NULL,
          asset_type VARCHAR(80) NOT NULL DEFAULT 'area',
          geometry JSONB,
          bbox JSONB,
          properties JSONB NOT NULL DEFAULT '{}'::jsonb,
          external_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          archived_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_assets_user_updated ON assets(user_id, updated_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assets_project ON assets(project_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS watches (
          id VARCHAR(36) PRIMARY KEY,
          user_id TEXT NOT NULL,
          project_id VARCHAR(36) REFERENCES projects(id) ON DELETE SET NULL,
          asset_id VARCHAR(36) NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
          name VARCHAR(255) NOT NULL,
          description TEXT,
          status VARCHAR(40) NOT NULL DEFAULT 'active',
          template_id VARCHAR(255),
          process_graph JSONB NOT NULL DEFAULT '{}'::jsonb,
          scf JSONB,
          collections JSONB NOT NULL DEFAULT '[]'::jsonb,
          trigger_type VARCHAR(40) NOT NULL DEFAULT 'manual',
          cadence VARCHAR(40),
          temporal_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
          quality_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
          budget_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
          next_run_at TIMESTAMPTZ,
          last_run_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_watches_user_status ON watches(user_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_watches_project ON watches(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_watches_asset ON watches(asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_watches_template ON watches(template_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_watches_next_run ON watches(next_run_at)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_items (
          id VARCHAR(36) PRIMARY KEY,
          provider VARCHAR(100) NOT NULL,
          collection_id VARCHAR(255) NOT NULL,
          stac_id VARCHAR(255) NOT NULL,
          datetime TIMESTAMPTZ,
          geometry JSONB,
          bbox JSONB,
          cloud_cover DOUBLE PRECISION,
          assets JSONB NOT NULL DEFAULT '{}'::jsonb,
          properties JSONB NOT NULL DEFAULT '{}'::jsonb,
          ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_catalog_items_source UNIQUE(provider, collection_id, stac_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_catalog_items_collection_datetime "
        "ON catalog_items(collection_id, datetime)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
          id VARCHAR(36) PRIMARY KEY,
          user_id TEXT NOT NULL,
          watch_id VARCHAR(36) REFERENCES watches(id) ON DELETE SET NULL,
          asset_id VARCHAR(36) REFERENCES assets(id) ON DELETE SET NULL,
          status VARCHAR(40) NOT NULL DEFAULT 'queued',
          window_start TIMESTAMPTZ,
          window_end TIMESTAMPTZ,
          source_catalog_item_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
          openeo_job_id VARCHAR(255),
          api_job_id VARCHAR(255),
          process_graph_hash VARCHAR(128),
          scf_hash VARCHAR(128),
          quality_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
          cost_estimate JSONB NOT NULL DEFAULT '{}'::jsonb,
          error TEXT,
          locked_by VARCHAR(120),
          locked_at TIMESTAMPTZ,
          attempt_count INTEGER NOT NULL DEFAULT 0,
          next_attempt_at TIMESTAMPTZ,
          started_at TIMESTAMPTZ,
          completed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_observations_user_created ON observations(user_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_observations_watch_status ON observations(watch_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_observations_asset ON observations(asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_observations_openeo_job ON observations(openeo_job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_observations_api_job ON observations(api_job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_observations_pg_hash ON observations(process_graph_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_observations_scf_hash ON observations(scf_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_observations_locked ON observations(locked_by, locked_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_observations_next_attempt ON observations(next_attempt_at)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS observation_events (
          id VARCHAR(36) PRIMARY KEY,
          user_id TEXT NOT NULL,
          observation_id VARCHAR(36) NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
          event_type VARCHAR(80) NOT NULL,
          message TEXT,
          payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_observation_events_user_created "
        "ON observation_events(user_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_observation_events_observation_created "
        "ON observation_events(observation_id, created_at)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_observation_events_type ON observation_events(event_type)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_packages (
          id VARCHAR(36) PRIMARY KEY,
          user_id TEXT NOT NULL,
          observation_id VARCHAR(36) REFERENCES observations(id) ON DELETE SET NULL,
          saved_result_id UUID REFERENCES saved_results(id) ON DELETE SET NULL,
          saved_result_save_id VARCHAR(8),
          stac_item JSONB NOT NULL DEFAULT '{}'::jsonb,
          source_items JSONB NOT NULL DEFAULT '[]'::jsonb,
          artifacts JSONB NOT NULL DEFAULT '{}'::jsonb,
          statistics JSONB NOT NULL DEFAULT '{}'::jsonb,
          provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
          process_graph JSONB NOT NULL DEFAULT '{}'::jsonb,
          scf JSONB,
          trace_id VARCHAR(255),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_evidence_user_created ON evidence_packages(user_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_evidence_observation ON evidence_packages(observation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_evidence_saved_result ON evidence_packages(saved_result_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_evidence_saved_result_save_id ON evidence_packages(saved_result_save_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_evidence_trace ON evidence_packages(trace_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS baselines (
          id VARCHAR(36) PRIMARY KEY,
          user_id TEXT NOT NULL,
          asset_id VARCHAR(36) NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
          watch_id VARCHAR(36) REFERENCES watches(id) ON DELETE SET NULL,
          metric VARCHAR(120) NOT NULL,
          season_key VARCHAR(80),
          window_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
          stats JSONB NOT NULL DEFAULT '{}'::jsonb,
          sample_count INTEGER NOT NULL DEFAULT 0,
          version INTEGER NOT NULL DEFAULT 1,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_baselines_scope UNIQUE(user_id, asset_id, watch_id, metric, season_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_baselines_asset_metric ON baselines(asset_id, metric)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_baselines_user ON baselines(user_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
          id VARCHAR(36) PRIMARY KEY,
          user_id TEXT NOT NULL,
          asset_id VARCHAR(36) REFERENCES assets(id) ON DELETE SET NULL,
          watch_id VARCHAR(36) REFERENCES watches(id) ON DELETE SET NULL,
          observation_id VARCHAR(36) REFERENCES observations(id) ON DELETE SET NULL,
          signal_type VARCHAR(80) NOT NULL DEFAULT 'metric',
          metric VARCHAR(120),
          value DOUBLE PRECISION,
          baseline_value DOUBLE PRECISION,
          delta DOUBLE PRECISION,
          severity VARCHAR(40) NOT NULL DEFAULT 'info',
          confidence DOUBLE PRECISION,
          geometry JSONB,
          status VARCHAR(40) NOT NULL DEFAULT 'open',
          explanation TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          acknowledged_at TIMESTAMPTZ,
          resolved_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_signals_user_status ON signals(user_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_signals_watch_created ON signals(watch_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_signals_asset ON signals(asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_signals_observation ON signals(observation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_signals_severity ON signals(severity)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS deliveries (
          id VARCHAR(36) PRIMARY KEY,
          user_id TEXT NOT NULL,
          signal_id VARCHAR(36) NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
          channel VARCHAR(80) NOT NULL,
          target TEXT NOT NULL,
          status VARCHAR(40) NOT NULL DEFAULT 'pending',
          attempt_count INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          sent_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_deliveries_user_status ON deliveries(user_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_deliveries_signal ON deliveries(signal_id)")

    for table in USER_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY user_isolation ON {table}
              FOR ALL
              TO authenticated
              USING (user_id = (select auth.uid())::text)
              WITH CHECK (user_id = (select auth.uid())::text)
            """
        )


def downgrade() -> None:
    for table in USER_TABLES:
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP TABLE IF EXISTS deliveries")
    op.execute("DROP TABLE IF EXISTS signals")
    op.execute("DROP TABLE IF EXISTS baselines")
    op.execute("DROP TABLE IF EXISTS evidence_packages")
    op.execute("DROP TABLE IF EXISTS observation_events")
    op.execute("DROP TABLE IF EXISTS observations")
    op.execute("DROP TABLE IF EXISTS catalog_items")
    op.execute("DROP TABLE IF EXISTS watches")
    op.execute("DROP TABLE IF EXISTS assets")
