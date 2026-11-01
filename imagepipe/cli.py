"""Command line entry point for unattended image production."""

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

import yaml

from .models import ImageConfig
from .pipeline import load_run, run
from .plan import compile_plan


def main() -> int:
    parser = argparse.ArgumentParser(prog="imagepipe")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("run", help="Fill an episode or season's image slots")
    start.add_argument("package", type=Path)
    start.add_argument("--config", type=Path, default=Path("configs/imagepipe.yaml"))
    start.add_argument("--output", type=Path)
    for name in ("resume", "status"):
        commands.add_parser(name).add_argument("run_dir", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "status":
            state = json.loads((args.run_dir / "state.json").read_text())
            print(json.dumps({
                "run_dir": str(args.run_dir),
                "elapsed_seconds": round(time.time() - state["started_at"], 1),
                "counts": dict(Counter(item["status"] for item in state["tasks"].values())),
                "errors": {key: item["error"] for key, item in state["tasks"].items() if item.get("error")},
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "resume":
            output = args.run_dir.resolve()
            plan, config = load_run(output)
        else:
            config = ImageConfig.model_validate(yaml.safe_load(args.config.read_text()) or {})
            config.api.env_file = str(Path(config.api.env_file).resolve())
            output = (args.output or Path("runs/imagepipe") / args.package.name).resolve()
            if (output / "tasks.json").exists():
                plan, config = load_run(output)
                if Path(plan.package_root) != args.package.resolve():
                    raise ValueError(f"{output} belongs to {plan.package_root}; choose a different --output")
            else:
                plan = compile_plan(args.package.resolve(), config)
        report = asyncio.run(run(plan, config, output))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["success"] else 1
    except KeyboardInterrupt:
        print("Interrupted. Remote jobs continue; use imagepipe resume with the run directory.", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError) as error:
        print(f"imagepipe: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
