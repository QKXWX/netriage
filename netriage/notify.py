#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""告警外发：把一次劣化诊断推到聊天机器人或自动化平台。

只依赖标准库。默认发通用 JSON；如果 URL 能认出飞书 / 钉钉 / Slack 的
自定义机器人，就自动套它们要的包体，省得用户再写适配器。
"""

import datetime
import json
import urllib.request

FEISHU_HOST = "open.feishu.cn"
DINGTALK_HOST = "oapi.dingtalk.com"
SLACK_HOST = "hooks.slack.com"


def summarize(result, measure=None):
    causes = result.get("causes") or []
    top = causes[0]["name"] if causes else result.get("verdict") or "未知"
    text = "[netriage] 宽带诊断 %s 分 · %s" % (result.get("health"), top)
    if measure:
        text += "\n下行 %s Mbps / 上行 %s Mbps · 空闲 %s ms · 抖动 %s ms · 丢包 %s%%" % (
            measure.get("down_mbps"), measure.get("up_mbps"), measure.get("latency_idle_ms"),
            measure.get("jitter_ms"), measure.get("loss_pct"))
    for cause in causes[:3]:
        text += "\n· %s（置信度 %.0f%%）" % (cause["name"], cause["confidence"] * 100)
    return text


def build_data(result, measure=None, at=None):
    causes = result.get("causes") or []
    return {
        "source": "netriage",
        "event": "degradation",
        "created_at": at or datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "health": result.get("health"),
        "verdict": result.get("verdict"),
        "causes": [{"id": item.get("id"), "name": item.get("name"),
                    "confidence": item.get("confidence")} for item in causes[:3]],
        "measure": {key: (measure or {}).get(key) for key in
                    ("down_mbps", "up_mbps", "latency_idle_ms", "latency_loaded_ms",
                     "bufferbloat_ms", "jitter_ms", "loss_pct")},
    }


def _envelope(url, text, data):
    target = (url or "").lower()
    if FEISHU_HOST in target or "larksuite.com" in target:
        return {"msg_type": "text", "content": {"text": text}}
    if DINGTALK_HOST in target:
        return {"msgtype": "text", "text": {"content": text}}
    if SLACK_HOST in target:
        return {"text": text}
    payload = dict(data or {})
    payload["text"] = text
    return payload


def send(url, result, measure=None, timeout=8, opener=None):
    """发送一条劣化告警，返回 HTTP 状态码。异常交给调用方决定是否吞掉。"""
    text = summarize(result, measure)
    body = json.dumps(_envelope(url, text, build_data(result, measure)), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8",
                                              "User-Agent": "netriage-alert/1.0"})
    response = (opener or urllib.request.urlopen)(request, timeout=timeout)
    status = getattr(response, "status", None) or response.getcode()
    try:
        response.read()
    except Exception:
        pass
    finally:
        try:
            response.close()
        except Exception:
            pass
    return status
