"""Trajectory-based evaluation system for JonaAI agent tool calling.

Captures tool call sequences (trajectories) during agent queries and scores
them against ground truth references using 4 metrics:
- Tool-Any-Order: Are the right tools used (regardless of order)?
- Tool-In-Order: Are tools used in the correct order (LCS)?
- Tool-Exact-Match: Does the sequence exactly match from the start?
- Parameter-Accuracy: Are key parameters correct?

Feature flag: set DISABLE_TRAJECTORY_EVAL=1 to disable entirely.
"""
