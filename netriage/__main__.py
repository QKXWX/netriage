#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口：serve / diagnose / probe / watch / history / demo / version。"""

import argparse
import json
import os
import sys

from . import __version__
from .diagnose import diagnose, health_label, ticket_for
from .probe import diagnose_remote, probe_payload, watch
from .report import render_report
from .server import serve
from .storage import HistoryStore, default_db_path


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


def _print_measure(measure, server):
    print("")
    print("  探测目标 %s" % server)
    multi_text = "%s Mbps" % measure["down_multi_mbps"] if measure.get("multi_valid") else "未采信"
    print("  下行 单流 %s Mbps / 四流 %s（负载 %s MB）" % (
        measure["down_single_mbps"], multi_text, measure.get("load_mb")))
    if measure["latency_loaded_ms"] is None:
        loaded_text = "未采信（负载仅 %ss，太短）" % measure.get("load_seconds")
    else:
        loaded_text = "%s ms · bufferbloat %s ms" % (measure["latency_loaded_ms"], measure["bufferbloat_ms"])
    print("  空闲 %s ms · 满载 %s" % (measure["latency_idle_ms"], loaded_text))
    print("  上行 %s Mbps（%s MB / %ss，服务端统计 %s）" % (
        measure["up_mbps"], measure.get("up_mb"), measure["up_seconds"], measure["up_server_mbps"]))
    print("  抖动 %s ms · 丢包 %s%%" % (measure["jitter_ms"], measure["loss_pct"]))
    print("")


def _plan_from_args(args):
    plan = {}
    if getattr(args, "plan_down", None):
        plan["down"] = args.plan_down
    if getattr(args, "plan_up", None):
        plan["up"] = args.plan_up
    return plan or None


def _watch_parser(subs, name, help_text):
    cmd = subs.add_parser(name, help=help_text)
    cmd.add_argument("--server", default="http://127.0.0.1:8080", help="netriage 服务地址")
    cmd.add_argument("--download", type=float, default=4.0, help="每轮下载 MB")
    cmd.add_argument("--upload", type=float, default=2.0, help="每轮上传 MB")
    cmd.add_argument("--ping", type=int, default=8, help="每轮 ping 次数")
    cmd.add_argument("--plan-down", type=float, default=None, help="签约下行 Mbps")
    cmd.add_argument("--plan-up", type=float, default=None, help="签约上行 Mbps")
    return cmd


def main(argv=None):
    parser = argparse.ArgumentParser(prog="netriage", description="宽带故障分诊台：测速 → 根因 → 工单")
    subs = parser.add_subparsers(dest="cmd")

    serve_cmd = subs.add_parser("serve", help="启动 Web 服务")
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8080)
    serve_cmd.add_argument("--db", default=None, help="历史存档 SQLite 路径（默认 data/netriage.db）")

    diag_cmd = subs.add_parser("diagnose", help="对一份测量 JSON 做离线诊断")
    diag_cmd.add_argument("file")
    diag_cmd.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    diag_cmd.add_argument("--ticket", action="store_true", help="同时打印报障工单")
    diag_cmd.add_argument("--html", metavar="PATH", help="把诊断写成单文件 HTML 报告")

    probe_cmd = _watch_parser(subs, "probe", "向服务端打一轮真实流量并输出测量指标")
    probe_cmd.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    probe_cmd.add_argument("--out", metavar="PATH", help="把测量 payload 写到文件，可直接再喂给 diagnose")
    probe_cmd.add_argument("--diagnose", action="store_true", help="探测后立刻让服务端诊断并入库存档")
    probe_cmd.add_argument("--load-cap", type=float, default=128.0, help="四流负载最大 MB，默认 128")

    watch_cmd = _watch_parser(subs, "watch", "定时复测：每隔 N 秒测一次并入库，留下趋势证据")
    watch_cmd.add_argument("--interval", type=int, default=600, help="两轮间隔秒数，默认 600")
    watch_cmd.add_argument("--count", type=int, default=0, help="测多少轮，0 表示一直跑")
    watch_cmd.add_argument("--jsonl", metavar="PATH", help="把每轮结果追加写成 JSONL")
    watch_cmd.add_argument("--load-cap", type=float, default=16.0, help="每轮四流负载最大 MB，默认 16")

    history_cmd = subs.add_parser("history", help="查看历史测量存档")
    history_cmd.add_argument("--limit", type=int, default=20)
    history_cmd.add_argument("--json", action="store_true")
    history_cmd.add_argument("--db", default=None)

    subs.add_parser("demo", help="用内置样例跑一遍诊断")
    subs.add_parser("version", help="打印版本")

    args = parser.parse_args(argv)
    cmd = args.cmd or "serve"

    if cmd == "version":
        print("netriage %s" % __version__)
        return 0

    if cmd == "serve":
        serve(args.host, args.port, args.db)
        return 0

    if cmd == "diagnose":
        payload = _load(args.file)
        result = diagnose(payload)
        if args.html:
            with open(args.html, "w", encoding="utf-8") as handle:
                handle.write(render_report(payload, result))
            print("报告已写入 %s" % args.html)
            return 0
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_report(result, ticket_for(payload, result) if args.ticket else None)
        return 0

    if cmd in ("probe", "watch"):
        plan = _plan_from_args(args)
        if cmd == "probe":
            payload = probe_payload(args.server, plan=plan, download_mb=args.download,
                                    upload_mb=args.upload, ping_count=args.ping,
                                    load_cap_mb=args.load_cap)
            measure = payload["measure"]
            answer = None
            if args.diagnose:
                answer = diagnose_remote(args.server, payload)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                print("测量 payload 已写入 %s" % args.out, flush=True)
            if args.json:
                print(json.dumps({"measure": measure,
                                  "diagnosis": answer["result"] if answer else None},
                                 ensure_ascii=False, indent=2))
            else:
                _print_measure(measure, args.server)
            if answer:
                _print_report(answer["result"], ticket_for(payload, answer["result"]))
            return 0

        handle = open(args.jsonl, "a", encoding="utf-8") if args.jsonl else None
        print("开始定时复测：每 %d 秒一轮，Ctrl-C 停止" % args.interval, flush=True)
        try:
            watch(args.server, interval=args.interval, count=args.count, plan=plan,
                  download_mb=args.download, upload_mb=args.upload, ping_count=args.ping,
                  load_cap_mb=args.load_cap, out=handle)
        finally:
            if handle:
                handle.close()
        return 0

    if cmd == "history":
        db_path = args.db or default_db_path()
        if not os.path.exists(db_path):
            print("还没有历史存档：%s" % db_path)
            return 0
        store = HistoryStore(db_path)
        runs = store.list(limit=args.limit)
        if args.json:
            print(json.dumps({"db": db_path, "count": store.count(), "runs": runs},
                             ensure_ascii=False, indent=2))
        else:
            print("存档 %s · 共 %d 条 · 最近 %d 条" % (db_path, store.count(), len(runs)))
            for run in runs:
                print("  #%d  %s  健康 %s  %s  ↓%s↑%s  延迟 %sms  %s" % (
                    run["id"], run["created_at"][:19].replace("T", " "),
                    run["health"], "Mbps", run["down_mbps"], run["up_mbps"],
                    run["latency_idle_ms"], run["verdict"]))
        store.close()
        return 0

    if cmd == "demo":
        from .samples import demo_payload
        payload = demo_payload()
        result = diagnose(payload)
        _print_report(result, ticket_for(payload, result))
        return 0

    parser.print_help()
    return 2


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
