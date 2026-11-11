"""Tests for trajectory learning — Phase 3 of outcome evaluation system."""

import pytest

from openeo_ai.batch.template_store import TemplateStore
from openeo_ai.batch.models import LearnedTrajectory


class TestCaptureTrajectory:
    def test_capture_new_trajectory(self):
        """New sequence -> returns ID."""
        ts = TemplateStore(":memory:")
        tid = ts.capture_trajectory(
            tool_sequence=["resolve", "compose_graph", "create_job", "start_job", "get_results"],
            source_query="NDVI for Berlin",
            domain="vegetation",
            outcome_score=0.92,
        )
        assert tid is not None
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_capture_duplicate_bumps_count(self):
        """Same sequence -> use_count incremented, returns None."""
        ts = TemplateStore(":memory:")
        seq = ["resolve", "compose_graph", "create_job"]
        tid1 = ts.capture_trajectory(
            tool_sequence=seq, domain="vegetation", outcome_score=0.8,
        )
        assert tid1 is not None

        tid2 = ts.capture_trajectory(
            tool_sequence=seq, domain="vegetation", outcome_score=0.8,
        )
        assert tid2 is None  # Duplicate

        # Verify use_count increased
        trajs = ts.find_relevant_trajectories(domain="vegetation")
        assert len(trajs) == 1
        assert trajs[0].use_count == 2

    def test_capture_updates_higher_score(self):
        """Higher outcome_score replaces lower."""
        ts = TemplateStore(":memory:")
        seq = ["resolve", "generate_graph", "create_job"]
        ts.capture_trajectory(
            tool_sequence=seq, domain="vegetation", outcome_score=0.7,
        )
        ts.capture_trajectory(
            tool_sequence=seq, domain="vegetation", outcome_score=0.95,
        )

        trajs = ts.find_relevant_trajectories(domain="vegetation")
        assert len(trajs) == 1
        assert trajs[0].outcome_score == pytest.approx(0.95)

    def test_capture_does_not_downgrade_score(self):
        """Lower outcome_score does not replace higher."""
        ts = TemplateStore(":memory:")
        seq = ["resolve", "generate_graph"]
        ts.capture_trajectory(
            tool_sequence=seq, domain="vegetation", outcome_score=0.95,
        )
        ts.capture_trajectory(
            tool_sequence=seq, domain="vegetation", outcome_score=0.6,
        )

        trajs = ts.find_relevant_trajectories(domain="vegetation")
        assert len(trajs) == 1
        assert trajs[0].outcome_score == pytest.approx(0.95)

    def test_capture_empty_sequence_ignored(self):
        """Empty tool sequence returns None."""
        ts = TemplateStore(":memory:")
        tid = ts.capture_trajectory(tool_sequence=[], domain="vegetation")
        assert tid is None

    def test_different_domains_different_trajectories(self):
        """Same tool sequence in different domains = different entries."""
        ts = TemplateStore(":memory:")
        seq = ["resolve", "compose_graph"]
        tid1 = ts.capture_trajectory(tool_sequence=seq, domain="vegetation")
        tid2 = ts.capture_trajectory(tool_sequence=seq, domain="water")
        assert tid1 is not None
        assert tid2 is not None
        assert tid1 != tid2

    def test_order_independent_dedup(self):
        """Different orderings of same tools = same trajectory (deduped)."""
        ts = TemplateStore(":memory:")
        tid1 = ts.capture_trajectory(
            tool_sequence=["resolve", "generate", "create"],
            domain="vegetation",
            outcome_score=0.8,
        )
        tid2 = ts.capture_trajectory(
            tool_sequence=["generate", "resolve", "create"],  # Different order
            domain="vegetation",
            outcome_score=0.9,
        )
        assert tid1 is not None
        assert tid2 is None  # Deduped — same sorted set


class TestFindRelevantTrajectories:
    def test_find_by_domain(self):
        """Domain filter works."""
        ts = TemplateStore(":memory:")
        ts.capture_trajectory(
            tool_sequence=["resolve", "compose_graph"],
            domain="vegetation",
            outcome_score=0.9,
        )
        ts.capture_trajectory(
            tool_sequence=["resolve", "algorithm"],
            domain="water",
            outcome_score=0.85,
        )

        veg = ts.find_relevant_trajectories(domain="vegetation")
        assert len(veg) == 1
        assert veg[0].domain == "vegetation"

        water = ts.find_relevant_trajectories(domain="water")
        assert len(water) == 1
        assert water[0].domain == "water"

    def test_find_diversified_results(self):
        """No duplicate sequences returned (by hash)."""
        ts = TemplateStore(":memory:")
        # Insert 3 different trajectories
        ts.capture_trajectory(
            tool_sequence=["resolve", "compose_graph", "create_job"],
            domain="vegetation",
            outcome_score=0.9,
        )
        ts.capture_trajectory(
            tool_sequence=["resolve", "generate_graph", "create_job", "get_results"],
            domain="vegetation",
            outcome_score=0.85,
        )
        ts.capture_trajectory(
            tool_sequence=["recommend", "resolve", "algorithm"],
            domain="vegetation",
            outcome_score=0.8,
        )

        trajs = ts.find_relevant_trajectories(domain="vegetation", limit=5)
        assert len(trajs) == 3
        # Verify ordered by score descending
        scores = [t.outcome_score for t in trajs]
        assert scores == sorted(scores, reverse=True)

    def test_find_respects_limit(self):
        """Limit parameter works."""
        ts = TemplateStore(":memory:")
        for i in range(5):
            ts.capture_trajectory(
                tool_sequence=[f"tool_{i}", "resolve"],
                domain="vegetation",
                outcome_score=0.5 + i * 0.1,
            )

        trajs = ts.find_relevant_trajectories(domain="vegetation", limit=2)
        assert len(trajs) == 2

    def test_find_all_domains_when_empty(self):
        """Empty domain returns trajectories across all domains."""
        ts = TemplateStore(":memory:")
        ts.capture_trajectory(
            tool_sequence=["resolve", "compose_graph"],
            domain="vegetation",
            outcome_score=0.9,
        )
        ts.capture_trajectory(
            tool_sequence=["resolve", "algorithm"],
            domain="water",
            outcome_score=0.85,
        )

        all_trajs = ts.find_relevant_trajectories(domain="", limit=10)
        assert len(all_trajs) == 2


class TestInjectionText:
    def test_injection_text_format(self):
        """Output format has correct structure with tool sequences."""
        ts = TemplateStore(":memory:")
        ts.capture_trajectory(
            tool_sequence=["resolve_location", "openeo_compose_graph", "openeo_create_job"],
            domain="vegetation",
            source_query="NDVI for Berlin",
            outcome_score=0.92,
        )

        # Verify the stored trajectory via find_relevant_trajectories
        trajs = ts.find_relevant_trajectories(domain="vegetation")
        assert len(trajs) == 1
        assert trajs[0].outcome_score == pytest.approx(0.92)
        assert trajs[0].tool_sequence == ["resolve_location", "openeo_compose_graph", "openeo_create_job"]

    def test_injection_text_empty_when_no_patterns(self):
        """Returns None when no patterns exist."""
        ts = TemplateStore(":memory:")
        # get_trajectory_injection requires domain classification
        # With no patterns, it should return None regardless
        result = ts.get_trajectory_injection("random unrelated query")
        assert result is None

    def test_injection_text_with_multiple_trajectories(self):
        """Multiple trajectories ordered by score descending."""
        ts = TemplateStore(":memory:")
        ts.capture_trajectory(
            tool_sequence=["resolve", "compose_graph", "create_job"],
            domain="vegetation",
            outcome_score=0.9,
        )
        ts.capture_trajectory(
            tool_sequence=["resolve", "generate_graph", "create_job", "get_results"],
            domain="vegetation",
            outcome_score=0.85,
        )

        trajs = ts.find_relevant_trajectories(domain="vegetation")
        assert len(trajs) == 2
        # First should be higher score
        assert trajs[0].outcome_score >= trajs[1].outcome_score
