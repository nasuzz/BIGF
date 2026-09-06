from __future__ import annotations

import argparse
import json
import os

from .pipeline import BAgentPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="B-agent query planner")
    parser.add_argument("question")
    parser.add_argument("--question-id", default="demo")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument(
        "--snapshot-date",
        default=os.getenv("MIRAE_SNAPSHOT_DATE"),
        help="최근 N개월을 고정 날짜 범위로 바꿀 기준일(YYYY-MM-DD)",
    )
    args = parser.parse_args()

    pipeline = BAgentPipeline(snapshot_date=args.snapshot_date)
    if args.plan_only:
        payload = pipeline.plan(args.question).to_dict()
    else:
        payload = pipeline.run(args.question_id, args.question).to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
