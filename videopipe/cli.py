import argparse
import json
import sys
from pathlib import Path

import httpx
import yaml

from .inputs import load_config, load_package, validate_plan
from .pipeline import Production


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            package = load_package(args.package)
            config = load_config(args.config)
            validate_plan(package, config)
            _print({"title": package.title, "shots": len(package.shots),
                    "generator": config.generator, "reviewer": config.review.backend})
            return 0
        if args.command == "status":
            _print(json.loads((args.run_dir / "state.json").read_text(encoding="utf-8")))
            return 0
        if args.command == "run":
            production = Production.create(load_package(args.package), load_config(args.config), args.output)
            state = production.run(auto_approve=args.auto_approve)
        elif args.command == "approve":
            state = Production(args.run_dir).approve()
        else:
            production = Production(args.run_dir)
            if args.command == "retry":
                production.retry(args.shot, feedback=args.feedback, new_round=args.new_round)
            state = production.run(auto_approve=args.auto_approve)
        _print(state)
        return 2 if state["status"] in {"failed", "needs_attention"} else 0
    except (OSError, ValueError, RuntimeError, httpx.HTTPError, yaml.YAMLError) as exc:
        print(f"videopipe: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("videopipe: interrupted; use resume to continue", file=sys.stderr)
        return 130


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline video production with ComfyUI and vLLM")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="Validate package and configuration without inference")
    run = sub.add_parser("run", help="Create a production run")
    for command in (validate, run):
        command.add_argument("package", type=Path)
        command.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--auto-approve", action="store_true", help="Proceed to delivery without a manual approval step")
    for name in ("status", "resume", "approve", "retry"):
        command = sub.add_parser(name)
        command.add_argument("run_dir", type=Path)
        if name in {"resume", "retry"}:
            command.add_argument("--auto-approve", action="store_true")
        if name == "retry":
            command.add_argument("--shot", action="append", required=True, help="Shot ID; repeat for multiple shots")
            command.add_argument("--feedback", default="", help="Record human feedback without changing generation inputs")
            command.add_argument("--new-round", action="store_true", help="Explicitly request a new production round (up to three generations); retain all history")
    return parser
