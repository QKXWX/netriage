#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口：serve / diagnose / demo / version。"""

import argparse
import json
import sys

from . import __version__
from .diagnose import diagnose, health_label, ticket_for
from .server import make_server


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _print_report(result, show_ticket=None):
    print("")
    print("  健康分 %d / 100 · %s" % (result["health"], health_label(result["health"])))
    print("  %s" % result["verdict"])
    print("  %s" % result["summary"])
    print("")
    print("  检查项")
    for item in result["checks"]:
        value = "未测" if item["value"] is None else ("%g %s" % (item["value"], item["unit"]))
        mark = {"ok": "✔", "warn": "!", "crit": "✘", "unknown": "-"}.get(item["status"], "-")
        print("   %s %-10s %s" % (mark, item["name"], value))
    if result["causes"]:
        print("")
        print("  根因排序")
        for index, cause in enumerate(result["causes"], 1):
            print("   %d. %s（%s，置信 %d%%）" % (
                index, cause["name"], cause["layer"], int(cause["confidence"] * 100)))
            for ev in cause["evidence"]:
                print("      - %s" % ev)
            print("      处置：")
            for action in cause["actions"]:
                print("        · %s" % action)
    if result["notes"]:
        print("")
        print("  提示")
        for note in result["notes"]:
            print("   · %s" % note)
    if show_ticket:
        print("")
        print(show_ticket)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="netriage", description="宽带故障分诊台：测速 → 根因 → 工单")
    subs = parser.add_subparsers(dest="cmd")

    serve_cmd = subs.add_parser("serve", help="启动 Web 服务")
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8080)

    diag_cmd = subs.add_parser("diagnose", help="对一份测量 JSON 做离线诊断")
    diag_cmd.add_argument("file")
    diag_cmd.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    diag_cmd.add_argument("--ticket", action="store_true", help="同时打印报障工单")

    subs.add_parser("demo", help="用内置样例跑一遍诊断")
    subs.add_parser("version", help="打印版本")

    args = parser.parse_args(argv)
    cmd = args.cmd or "serve"

    if cmd == "version":
        print("netriage %s" % __version__)
        return 0

    if cmd == "serve":
        serve(args.host, args.port)
        return 0

    if cmd == "diagnose":
        payload = _load(args.file)
        result = diagnose(payload)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_report(result, ticket_for(payload, result) if args.ticket else None)
        return 0

    if cmd == "demo":
        from .samples import demo_payload
        payload = demo_payload()
        result = diagnose(payload)
        _print_report(result, ticket_for(payload, result))
        return 0

    parser.print_help()
    return 2


def serve(host, port):
    server = make_server(host, port)
    print("netriage %s 已启动：http://%s:%d/" % (__version__, host, server.server_address[1]))
    print("按 Ctrl-C 停止")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:      # head / 管道提前关闭
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
