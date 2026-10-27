"""dfctl: studio, site-operator and node command line."""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

import httpx
import yaml


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _issues(issues) -> int:
    for issue in issues:
        print(issue, file=sys.stderr if issue.level == "error" else sys.stdout)
    return 1 if any(issue.level == "error" for issue in issues) else 0


def _routes(site_path: Path | None):
    if site_path is None:
        return None
    from .cluster.config import load_site
    return load_site(site_path).routes


def cmd_keys(args) -> int:
    from .protocol import keys
    key_id = keys.generate(args.prefix)
    print(f"已生成 {args.prefix}.key（保密）与 {args.prefix}.pub（复制到对端 trusted_keys/）；key_id={key_id}")
    return 0


def cmd_schema(args) -> int:
    from .protocol.manifest import Manifest
    text = json.dumps(Manifest.model_json_schema(), ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


def cmd_package(args) -> int:
    from .protocol import keys
    from .protocol.package import PackageError, discard, inspect, seal, to_tar, verify
    if args.action == "check":
        code = _issues(inspect(args.dir, _routes(args.site)))
        if not code:
            print("检查通过")
        return code
    if args.action == "seal":
        code = _issues(inspect(args.dir, _routes(args.site)))
        if code:
            return code
        digest = seal(args.dir, keys.load_private(args.key))
        print(f"已封装 {args.dir}，摘要 {digest}")
        if args.tar:
            print(f"已打包 {to_tar(args.dir, args.tar)}")
        return 0
    site = None
    if args.site:
        from .cluster.config import load_site
        site = load_site(args.site)
    try:
        verified = verify(args.path, keys.load_trust_dir(args.trust), routes=site.routes if site else None,
                          restricted_regions=site.license.restricted_regions if site else (),
                          authorized_regions=site.license.authorized_regions if site else ())
    except PackageError as exc:
        return _issues(exc.issues)
    discard(verified)
    _issues(verified.warnings)
    print(f"校验通过：{verified.manifest.package_id}，{len(verified.manifest.shots)} 个镜头，签名 {verified.key_id}")
    return 0


def cmd_server(args) -> int:
    import uvicorn

    from .cluster.config import load_site, secret
    from .control.api import create_app
    from .control.controller import Controller
    from .storage import make_store
    from .store.db import make_sessions
    site = load_site(args.site)
    worker, operator = secret(site.worker_token), secret(site.operator_token)
    if not worker or not operator:
        print("请设置 worker_token / operator_token（如环境变量 DF_WORKER_TOKEN / DF_OPERATOR_TOKEN）", file=sys.stderr)
        return 2
    store = make_store(site.store, Path(site.staging_dir) / "cache")
    controller = Controller(make_sessions(secret(site.database_url)), store, site)
    uvicorn.run(create_app(controller, worker, operator, tick_s=site.scheduler.tick_s), host=args.host,
                port=args.port, workers=1)
    return 0


def cmd_agent(args) -> int:
    from .cluster.config import load_node, secret
    from .cluster.profiles import validate_node
    from .node.agent import ControlClient, NodeAgent
    node = load_node(args.node)
    errors, warnings = validate_node(node)
    for message in warnings:
        logging.warning(message)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    NodeAgent(node, ControlClient(node.control_url, secret(node.token))).serve_forever()
    return 0


def cmd_node(args) -> int:
    from .cluster.config import load_node
    from .cluster.profiles import capacity, render_compose, validate_node
    node = load_node(args.node)
    errors, warnings = validate_node(node)
    for message in warnings:
        print(f"[warning] {message}")
    for message in errors:
        print(f"[error] {message}", file=sys.stderr)
    if errors:
        return 1
    if args.action == "check":
        _print({name: capacity(node, name) for name in node.profiles})
        return 0
    out = args.out or Path(node.compose_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name in node.profiles:
        path = out / f"{name}.yml"
        path.write_text(yaml.safe_dump(render_compose(node, name), sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"已生成 {path}")
    return 0


def _api(args) -> httpx.Client:
    token = args.token or os.environ.get("DF_OPERATOR_TOKEN", "")
    return httpx.Client(base_url=args.control, headers={"Authorization": f"Bearer {token}"}, timeout=600)


def cmd_ops(args) -> int:
    with _api(args) as api:
        if args.command == "status":
            response = api.get("/v1/status")
        elif args.command == "import":
            response = api.post("/v1/packages/import", json={"path": str(args.path)})
        elif args.command == "show":
            path = f"/v1/packages/{args.package}" + (f"/shots/{args.shot}" if args.shot else "")
            response = api.get(path)
        elif args.command == "tasks":
            response = api.get("/v1/human-tasks", params={"status": args.status})
        elif args.command == "decide":
            plan = json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else None
            response = api.post(f"/v1/human-tasks/{args.task}/decision", json={
                "action": args.action, "operator": args.operator, "take_id": args.take, "plan": plan,
                "extra_rounds": args.extra_rounds, "note": args.note})
        elif args.command == "pause":
            response = api.post("/v1/system/pause", json={"reason": args.reason, "operator": args.operator})
        elif args.command == "resume":
            response = api.post("/v1/system/resume", json={"operator": args.operator})
        elif args.command == "resume-node":
            response = api.post(f"/v1/nodes/{args.node_id}/resume", json={"operator": args.operator})
        elif args.command == "profile":
            response = api.post(f"/v1/nodes/{args.node_id}/profile", json={"profile": args.profile,
                                                                           "operator": args.operator})
        else:
            response = api.post(f"/v1/packages/{args.package}/export", json={"media": args.media})
    try:
        _print(response.json())
    except ValueError:
        print(response.text)
    return 0 if response.is_success else 1


def cmd_demo(args) -> int:
    from .demo import run_demo
    run_demo(args.workdir, render=not args.no_render)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfctl", description="8xB200 AI 短剧工厂控制系统")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("keys", help="生成 Ed25519 密钥对（云端工作室 / 站点各一对）")
    p.add_argument("prefix", type=Path)
    p.set_defaults(func=cmd_keys)
    p = sub.add_parser("schema", help="输出 manifest JSON Schema，交给云端 GPT 按格式生成任务包")
    p.add_argument("--out", type=Path)
    p.set_defaults(func=cmd_schema)

    package = sub.add_parser("package", help="任务包：check / seal / verify")
    actions = package.add_subparsers(dest="action", required=True)
    p = actions.add_parser("check", help="封装前检查（云端）")
    p.add_argument("dir", type=Path)
    p.add_argument("--site", type=Path, help="按站点生成池能力检查")
    p = actions.add_parser("seal", help="写入校验和与签名（云端）")
    p.add_argument("dir", type=Path)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--site", type=Path)
    p.add_argument("--tar", type=Path, help="同时打成 tar 便于离线传输")
    p = actions.add_parser("verify", help="导入前完整校验（站点）")
    p.add_argument("path", type=Path)
    p.add_argument("--trust", type=Path, required=True)
    p.add_argument("--site", type=Path)
    package.set_defaults(func=cmd_package)

    p = sub.add_parser("server", help="启动控制服务（单进程）")
    p.add_argument("--site", type=Path, required=True)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8700)
    p.set_defaults(func=cmd_server)
    p = sub.add_parser("agent", help="启动节点代理")
    p.add_argument("--node", type=Path, required=True)
    p.set_defaults(func=cmd_agent)

    node = sub.add_parser("node", help="节点 GPU 布局：check / render")
    actions = node.add_subparsers(dest="action", required=True)
    p = actions.add_parser("check", help="校验 GPU/MIG 分配并输出各 profile 的池容量")
    p.add_argument("--node", type=Path, required=True)
    p = actions.add_parser("render", help="为每个 profile 生成 docker compose 文件")
    p.add_argument("--node", type=Path, required=True)
    p.add_argument("--out", type=Path)
    node.set_defaults(func=cmd_node)

    def ops(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--control", default=os.environ.get("DF_CONTROL_URL", "http://127.0.0.1:8700"))
        p.add_argument("--token", default="")
        p.add_argument("--operator", default=os.environ.get("USERNAME") or os.environ.get("USER") or "operator")
        p.set_defaults(func=cmd_ops)
        return p

    ops("status", "站点状态、队列、节点与告警")
    ops("import", "导入 inbox 中的任务包").add_argument("path", type=Path)
    p = ops("show", "查看包或镜头详情")
    p.add_argument("package")
    p.add_argument("shot", nargs="?")
    ops("tasks", "人工队列").add_argument("--status", default="open", choices=["open", "resolved"])
    p = ops("decide", "处理人工任务")
    p.add_argument("task")
    p.add_argument("--action", required=True, choices=["approve", "reject", "retry"])
    p.add_argument("--take")
    p.add_argument("--plan", type=Path, help="RepairPlan JSON 文件")
    p.add_argument("--extra-rounds", type=int, default=1)
    p.add_argument("--note", default="")
    ops("pause", "暂停领取新的生成任务").add_argument("--reason", default="manual")
    ops("resume", "解除站点暂停与暂停的生成池")
    ops("resume-node", "解除节点排空").add_argument("node_id")
    p = ops("profile", "切换节点 GPU profile")
    p.add_argument("node_id")
    p.add_argument("profile")
    p = ops("export", "导出签名结果包")
    p.add_argument("package")
    p.add_argument("--media", default="approved", choices=["approved", "candidates", "none"])

    p = sub.add_parser("demo", help="单机模拟完整流程（无需 GPU）")
    p.add_argument("--workdir", type=Path, default=Path("demo-run"))
    p.add_argument("--no-render", action="store_true", help="不调用 ffmpeg 渲染测试片")
    p.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
