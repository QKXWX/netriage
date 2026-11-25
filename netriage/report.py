#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一次诊断渲染成单文件 HTML 报告：能直接发给运营商、贴进工单系统、或存档。

报告不引用任何外部资源（无字体、无图片、无 JS），断网也能打开，
打印出来是正常白底黑字。
"""

import datetime
import html

from . import __version__
from .diagnose import ticket_for


def _esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def _num(value, digits=1, unit=""):
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _esc(value)
    text = ("%%.%df" % digits) % number
    return text + (" " + unit if unit else "")


def _ratio(value, plan):
    if value is None or not plan:
        return None
    return value / plan * 100


_STYLE = """
:root{--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--ok:#0f766e;--warn:#b45309;--crit:#b91c1c;--accent:#0d9488}
*{box-sizing:border-box}
body{margin:0;background:#f6f7f8;color:var(--ink);font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.page{max-width:880px;margin:0 auto;padding:40px 28px 56px;background:#fff;min-height:100vh}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;border-bottom:2px solid var(--ink);padding-bottom:14px}
.brand{font-size:13px;letter-spacing:.18em;color:var(--muted);text-transform:uppercase}
h1{font-size:26px;margin:6px 0 0;letter-spacing:-.01em}
.meta{text-align:right;font-size:12.5px;color:var(--muted);font-family:ui-monospace,Consolas,monospace}
.hero{display:flex;gap:24px;align-items:center;margin:26px 0 8px;padding:20px;border:1px solid var(--line);border-radius:14px;background:#fafafa}
.score{min-width:120px;text-align:center}
.score b{display:block;font-size:42px;line-height:1;font-family:ui-monospace,Consolas,monospace}
.score small{color:var(--muted);font-size:12px}
.verdict h2{margin:0 0 6px;font-size:17px}
.verdict p{margin:0;color:#374151;font-size:14px}
h3{margin:34px 0 10px;font-size:16px;display:flex;align-items:center;gap:10px}
h3 span{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#fff;background:var(--accent);border-radius:999px;padding:2px 9px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;background:#fbfbfb}
td.val{font-family:ui-monospace,Consolas,monospace}
.tag{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:999px;border:1px solid}
.tag.ok{color:var(--ok);border-color:#99f6e4;background:#f0fdfa}
.tag.warn{color:var(--warn);border-color:#fde68a;background:#fffbeb}
.tag.crit{color:var(--crit);border-color:#fecaca;background:#fef2f2}
.tag.unknown{color:var(--muted);border-color:var(--line);background:#f9fafb}
.cause{border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:14px 16px;margin:10px 0;background:#fff}
.cause.high{border-left-color:var(--crit)}
.cause.mid{border-left-color:var(--warn)}
.cause-top{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.cause-top strong{font-size:15px}
.cause-top span{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--muted)}
.cause ul.ev{margin:10px 0 0;padding-left:20px;font-size:13.5px;color:#374151}
.cause .act-label{margin:12px 0 2px;font-size:11px;letter-spacing:.1em;color:var(--muted)}
.cause ol.acts{margin:0;padding-left:22px;font-size:13.5px;color:#111827}
.cause ol.acts li{margin:3px 0}
pre.ticket{white-space:pre-wrap;word-break:break-all;background:#0b141a;color:#d7e3e8;border-radius:12px;padding:16px 18px;font:12.5px/1.7 ui-monospace,Consolas,monospace}
.note{margin-top:26px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:12.5px}
.note li{margin:2px 0}
.info{display:grid;grid-template-columns:repeat(2,1fr);gap:6px 24px;font-size:13px;margin-top:8px}
.info div{color:#374151}
.info i{color:var(--muted);font-style:normal;display:inline-block;min-width:72px}
@media print{body{background:#fff}.page{max-width:none;padding:0}.hero{background:#fff}}
"""


def render_report(payload, result, generated_at=None):
    """返回完整 HTML 字符串。payload 为原始输入，result 为 diagnose() 的输出。"""
    payload = payload or {}
    result = result or {}
    created = generated_at or datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    stamp = created.replace("T", " ")[:19]

    health = result.get("health", 0)
    tone = "var(--ok)" if health >= 85 else ("var(--warn)" if health >= 60 else "var(--crit)")
    verdict = result.get("verdict") or "未得出结论"
    summary = result.get("summary") or ""

    metrics = result.get("metrics") or {}
    plan_down = metrics.get("plan_down")
    plan_up = metrics.get("plan_up")
    down = metrics.get("down_mbps")
    up = metrics.get("up_mbps")
    down_ratio = _ratio(down, plan_down)
    up_ratio = _ratio(up, plan_up)

    overview_rows = [
        ("下行吞吐", _num(down, 1, "Mbps"), _num(plan_down, 0, "Mbps"), _num(down_ratio, 1, "%")),
        ("上行吞吐", _num(up, 1, "Mbps"), _num(plan_up, 0, "Mbps"), _num(up_ratio, 1, "%")),
        ("单流下行", _num(metrics.get("down_single_mbps"), 1, "Mbps"),
         _num(metrics.get("down_multi_mbps"), 1, "Mbps（多流）"), "—"),
        ("空闲延迟", _num(metrics.get("latency_idle_ms"), 1, "ms"), "—", "—"),
        ("满载延迟", _num(metrics.get("latency_loaded_ms"), 1, "ms"), "—", "—"),
        ("满载延迟增量", _num(metrics.get("bufferbloat_ms"), 0, "ms"), "—", "—"),
        ("抖动", _num(metrics.get("jitter_ms"), 1, "ms"), "—", "—"),
        ("探测丢包", _num(metrics.get("loss_pct"), 2, "%"), "—", "—"),
        ("多流 / 单流", _num(metrics.get("stream_ratio"), 2, "x"), "—", "—"),
    ]
    overview_html = "".join(
        "<tr><td>%s</td><td class=\"val\">%s</td><td class=\"val\">%s</td><td class=\"val\">%s</td></tr>"
        % (name, actual, plan, extra) for name, actual, plan, extra in overview_rows
    )

    checks_html = "".join(
        "<tr><td>%s</td><td class=\"val\">%s</td><td><span class=\"tag %s\">%s</span></td></tr>"
        % (
            _esc(item.get("name")),
            "未测" if item.get("value") is None else "%s %s" % (_num(item.get("value"), 2), _esc(item.get("unit"))),
            _esc(item.get("status")),
            {"ok": "正常", "warn": "提示", "crit": "严重", "unknown": "未测"}.get(item.get("status"), item.get("status")),
        )
        for item in (result.get("checks") or [])
    )

    causes = result.get("causes") or []
    if causes:
        blocks = []
        for cause in causes:
            level = "high" if cause.get("confidence", 0) >= 0.6 else ("mid" if cause.get("confidence", 0) >= 0.4 else "low")
            evidence = "".join("<li>%s</li>" % _esc(text) for text in (cause.get("evidence") or []))
            actions = "".join("<li>%s</li>" % _esc(text) for text in (cause.get("actions") or []))
            blocks.append(
                "<div class=\"cause %s\"><div class=\"cause-top\"><strong>%s</strong>"
                "<span>%s · 置信 %d%%</span></div>"
                "<ul class=\"ev\">%s</ul>"
                "<p class=\"act-label\">处置动作</p><ol class=\"acts\">%s</ol></div>"
                % (
                    level,
                    _esc(cause.get("name")),
                    _esc(cause.get("layer")),
                    int(round(float(cause.get("confidence") or 0) * 100)),
                    evidence,
                    actions,
                )
            )
        causes_html = "".join(blocks)
    else:
        causes_html = "<p style=\"color:#6b7280\">没有命中任何根因规则，指标看起来正常。</p>"

    notes = result.get("notes") or []
    notes_html = "".join("<li>%s</li>" % _esc(note) for note in notes) if notes else ""

    info_bits = []
    for label, key in (("宽带账号", "account"), ("运营商", "isp"), ("安装地址", "address"), ("联系人", "contact")):
        if payload.get(key):
            info_bits.append("<div><i>%s</i>%s</div>" % (label, _esc(payload.get(key))))
    info_html = "<div class=\"info\">%s</div>" % "".join(info_bits) if info_bits else ""

    ticket = ticket_for(payload, result)

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>netriage 诊断报告 · %(stamp)s</title>
<style>%(style)s</style>
</head>
<body>
<div class="page">
  <div class="top">
    <div>
      <div class="brand">netriage · 宽带故障分诊台</div>
      <h1>宽带质量诊断报告</h1>
    </div>
    <div class="meta">生成时间<br>%(stamp)s<br>netriage v%(version)s</div>
  </div>

  <div class="hero">
    <div class="score"><b style="color:%(tone)s">%(health)s</b><small>健康分 / 100</small></div>
    <div class="verdict"><h2>%(verdict)s</h2><p>%(summary)s</p></div>
  </div>
  %(info)s

  <h3><span>01</span>套餐与实测</h3>
  <table>
    <thead><tr><th>项目</th><th>实测</th><th>签约 / 对照</th><th>达标率</th></tr></thead>
    <tbody>%(overview)s</tbody>
  </table>

  <h3><span>02</span>检查表</h3>
  <table>
    <thead><tr><th>检查项</th><th>实测</th><th>判定</th></tr></thead>
    <tbody>%(checks)s</tbody>
  </table>

  <h3><span>03</span>根因判断</h3>
  %(causes)s
  %(notes)s

  <h3><span>04</span>报障工单</h3>
  <pre class="ticket">%(ticket)s</pre>

  <div class="note">
    <strong>测量口径</strong>
    <ul>
      <li>测的是「浏览器 ↔ 本服务所在机器」这一段 TCP/HTTP 吞吐，不等于运营商官方到户测速。</li>
      <li>丢包为 HTTP 探测失败率估算（10 次样本），不是 ICMP 丢包；抖动为相邻 RTT 差值均值。</li>
      <li>满载延迟增量 = 下载跑满时的延迟 − 空闲延迟，用来识别队列堆积（bufferbloat）。</li>
      <li>诊断由确定性加权规则给出，每条结论都附着具体数值或日志证据，可复现、可复核。</li>
    </ul>
    由 <a href="https://github.com/QKXWX/netriage">netriage</a> 生成 · MIT License
  </div>
</div>
</body>
</html>
""" % {
        "style": _STYLE,
        "stamp": _esc(stamp),
        "version": _esc(__version__),
        "tone": tone,
        "health": _esc(health),
        "verdict": _esc(verdict),
        "summary": _esc(summary),
        "info": info_html,
        "overview": overview_html,
        "checks": checks_html,
        "causes": causes_html,
        "notes": ("<ul class=\"note\">%s</ul>" % notes_html) if notes_html else "",
        "ticket": _esc(ticket),
    }
