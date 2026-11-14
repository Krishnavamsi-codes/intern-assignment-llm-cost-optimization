"""Per-call token and points breakdown for one ticket.

Usage:
    python scripts/cost_breakdown.py q1 T-1001
    python scripts/cost_breakdown.py q2 T-1001 --sections

`--sections` also splits each prompt into its blank-line-separated blocks and
shows the tokens each block costs, which is how you find what to cut.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.billing import CACHED_INPUT_PRICE, INPUT_PRICE, OUTPUT_PRICE  # noqa: E402
from shared.tokens import count_tokens  # noqa: E402


def make_agent(track: str):
    if track == "q1":
        from question1.agent import SupportCopilot

        return SupportCopilot()
    from question2.agent import SupportPipeline

    return SupportPipeline()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("track", choices=["q1", "q2"])
    ap.add_argument("ticket")
    ap.add_argument("--sections", action="store_true")
    args = ap.parse_args()

    agent = make_agent(args.track)
    res = agent.handle_ticket(args.ticket)
    print(f"{args.track} {args.ticket}: action={res.action} kb_article={res.kb_article}\n")
    print(f"{'call':<16}{'input':>8}{'cached':>8}{'output':>8}{'points':>9}")
    for call in agent.session.calls:
        cached = min(call.cached_prefix_tokens, call.input_tokens)
        print(
            f"{call.label:<16}{call.input_tokens:>8,}{cached:>8,}"
            f"{call.output_tokens:>8,}{call.points():>9,}"
        )
        if args.sections:
            for block in call.prompt.split("\n\n"):
                tok = count_tokens(block)
                head = " ".join(block.split())[:50]
                print(f"    {tok:>7,} tok {tok * INPUT_PRICE:>8,} pts  {head}")
            print(f"    output {call.output_tokens} tok x {OUTPUT_PRICE} = "
                  f"{call.output_tokens * OUTPUT_PRICE:,} pts")
    print(f"\nTOTAL {agent.session.total_points():,} points "
          f"(input {INPUT_PRICE}, cached {CACHED_INPUT_PRICE}, output {OUTPUT_PRICE} per token)")


if __name__ == "__main__":
    main()
