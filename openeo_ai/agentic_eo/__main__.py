# ABOUTME: CLI entrypoint — `python -m openeo_ai.agentic_eo "<natural-language EO question>"`.
# ABOUTME: Prints the decision-grade report, or a clarification prompt when the AOI can't be resolved.

from __future__ import annotations

import argparse
import sys

from .pipeline import run_query


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m openeo_ai.agentic_eo",
        description="Agentic Earth Observation — natural-language question to decision-grade insight.",
    )
    parser.add_argument("query", nargs="+", help="the natural-language EO question")
    parser.add_argument("--no-llm", action="store_true", help="skip the headless-claude refinement step")
    args = parser.parse_args(argv)

    query = " ".join(args.query)
    out = run_query(query, use_llm=not args.no_llm)

    if out.intent.clarification_needed:
        print("# Clarification needed\n")
        print(out.intent.clarification_needed + "\n")
        print(f"Parsed intent so far: variable={out.intent.variable.value}, "
              f"analysis={out.intent.analysis_type.value}, "
              f"period={out.intent.time.start}…{out.intent.time.end}.")
        return 2

    print(out.report.to_markdown())
    return 0


if __name__ == "__main__":
    sys.exit(main())
