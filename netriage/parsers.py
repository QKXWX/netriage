#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志与信号解析：把路由器 / 光猫 / 无线侧的非结构化数据变成可判定字段。

只做解析，不做判断；判断在 diagnose.py。所有函数都是纯函数，输入文本，输出 dict，
解析失败不抛异常，返回尽量完整的空结构。
"""

import json
import re

# PPPoE / 宽带拨号常见错误码。含义按运营商侧的实际语义整理。
DIAL_ERRORS = {
    "691": "认证失败：账号或密码错误、欠费停机、账号被绑定到其它端口",
    "678": "远端无响应：线路断、光猫未桥接通、局端 BRAS 无应答",
    "651": "光猫/调制解调器报错：多为线路或设备故障",
    "676": "端口占线：上一次 PPPoE 会话没有正常释放",
    "769": "无法到达目标：网卡或拨号适配器状态异常",
    "718": "PPP 协商超时：链路层握手没有完成",
    "720": "PPP 协议协商失败：MTU/MRU 或对端参数不匹配",
}

_RE_ERROR_WORD = re.compile(r"(?:error|code|错误(?:代码)?)\s*[:=]?\s*(\d{3})", re.IGNORECASE)
_RE_AUTH_FAIL = re.compile(
    r"authentication\s+failed|CHAP\s+authentication\s+failed|PAP\s+authentication\s+failed"
    r"|认证失败|Remote message:.*(?:fail|denied|invalid|错误|失败)",
    re.IGNORECASE,
)
_RE_PADI = re.compile(r"sent\s*\[PADI", re.IGNORECASE)
_RE_PADO = re.compile(r"rcvd\s*\[PADO", re.IGNORECASE)
_RE_LCP_TIMEOUT = re.compile(
    r"LCP:?\s*timeout|No response to \d+ echo|LCP\s+terminated|timeout sending Config-Requests",
    re.IGNORECASE,
)
_RE_DROP = re.compile(
    r"Modem hangup|modem\s+hangup|link is down|Connection terminated|LCP terminated|carrier lost",
    re.IGNORECASE,
)
_RE_GOT_IP = re.compile(r"local\s+IP\s+address|inet\s+addr|得到.*IP|IP address.*assigned", re.IGNORECASE)


def parse_dial_log(text):
    """解析 PPPoE / 宽带拨号日志。

    支持 pppd 原始日志、OpenWrt logread 片段、通用中文报错文本。
    返回：
      lines / dial_attempts / pado / drops / auth_failures / lcp_timeouts /
      error_codes[{code,meaning,count}] / got_ip
    """
    text = text or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]

    codes = {}
    for match in _RE_ERROR_WORD.finditer(text):
        code = match.group(1)
        codes[code] = codes.get(code, 0) + 1

    # “错误 691” 这种中文写法也归一到同一个 key
    for match in re.finditer(r"错误\s*[:：]?\s*(\d{3})", text):
        code = match.group(1)
        codes[code] = codes.get(code, 0) + 1

    auth_failures = len(_RE_AUTH_FAIL.findall(text))
    # 691 出现一次就至少是一次认证失败，避免重复计数时把同一行算两遍
    auth_failures = max(auth_failures, codes.get("691", 0))

    return {
        "lines": len(lines),
        "dial_attempts": len(_RE_PADI.findall(text)),
        "pado": len(_RE_PADO.findall(text)),
        "drops": len(_RE_DROP.findall(text)),
        "auth_failures": auth_failures,
        "lcp_timeouts": len(_RE_LCP_TIMEOUT.findall(text)),
        "got_ip": len(_RE_GOT_IP.findall(text)),
        "error_codes": [
            {"code": code, "meaning": DIAL_ERRORS.get(code, "未收录的错误码"), "count": count}
            for code, count in sorted(codes.items())
        ],
    }


_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

# 常见别名 -> 标准字段。键会先做 lower + 去空格/下划线/减号处理。
_LQC_ALIASES = {
    "rssi": "rssi",
    "rsrp": "rsrp",
    "lteRsrp": "rsrp",
    "nrRsrp": "rsrp",
    "rsrq": "rsrq",
    "lteRsrq": "rsrq",
    "nrRsrq": "rsrq",
    "sinr": "sinr",
    "snr": "snr",
    "lteSinr": "sinr",
    "nrSinr": "sinr",
    "band": "band",
    "lteband": "band",
    "nrband": "band",
    "channel": "channel",
    "channelnum": "channel",
    "pci": "pci",
    "cellid": "cell_id",
    "cellidentity": "cell_id",
    "nbiotcellid": "cell_id",
    "txpower": "tx_power",
    "txpwr": "tx_power",
    "rxpower": "rx_power",
    "opticalrxpower": "rx_power",
    "optrxpower": "rx_power",
    "rxtxpower": "rx_power",
    "txrate": "tx_rate",
    "txrateMbps": "tx_rate",
    "phyrateself": "tx_rate",
    "plmn": "plmn",
    "uptime": "uptime",
    "temperature": "temperature",
    "retry": "retry_pct",
    "retryrate": "retry_pct",
    "信道占用": "channel_util",
    "channelutilization": "channel_util",
    "noise": "noise",
    "底噪": "noise",
    "信噪比": "snr",
    "信号强度": "rssi",
    "重传率": "retry_pct",
    "重传": "retry_pct",
    "协商速率": "tx_rate",
    "光功率": "rx_power",
    "频段": "band",
    "PLMN": "plmn",
}

_LQC_KEYS = list(_LQC_ALIASES.keys())


def _norm_key(key):
    key = (key or "").strip().lower()
    return re.sub(r"[\s_\-]+", "", key)


def _to_number(value):
    """把 'RSRP: -95dBm' / '<-140' / '18.5 %' 这类值变成 float。"""
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("n/a", "na", "-", "null", "none", "unknown"):
        return None
    negate = text.startswith("<")
    match = _NUM_RE.search(text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    if negate and number > 0:
        number = -abs(number)
    return number


def parse_lqc(text):
    """解析无线信号（LQC / 光猫状态页 / 路由 JSON 导出）。

    输入可以是 JSON 字符串、JSON 对象，或 `RSRP=-95 RSRQ=-9 SINR=18` 这类键值文本。
    输出：{"metrics": {标准字段: 数值}, "raw": {原始键: 原值}, "source": "json|kv|empty"}
    """
    metrics = {}
    raw = {}
    source = "empty"

    data = None
    if isinstance(text, dict):
        data = text
    else:
        body = (text or "").strip()
        if not body:
            return {"metrics": {}, "raw": {}, "source": source}
        if body.startswith("{") or body.startswith("["):
            try:
                data = json.loads(body)
            except Exception:
                data = None

    if isinstance(data, dict):
        source = "json"
        # 有些导出是 {"data": {...}} 或 {"result": {...}}
        for wrapper in ("data", "result", "metrics", "signal"):
            if isinstance(data.get(wrapper), dict):
                data = data[wrapper]
                break
        items = list(data.items())
    else:
        source = "kv" if text else "empty"
        items = re.findall(
            r"([A-Za-z_\u4e00-\u9fa5][A-Za-z0-9_\-\u4e00-\u9fa5]*)\s*[=:：]\s*([^,;，；\s]+)",
            text or "",
        )

    for key, value in items:
        if isinstance(value, (dict, list)):
            continue
        raw[str(key)] = value
        norm = _norm_key(key)
        field = None
        if norm in _LQC_ALIASES:
            field = _LQC_ALIASES[norm]
        else:
            for alias, name in _LQC_ALIASES.items():
                alias_norm = _norm_key(alias)
                if alias_norm and alias_norm in norm:
                    field = name
                    break
        if not field:
            continue
        if field == "band":
            band_text = str(value).strip().upper()
            if "5G" in band_text or "NR" in band_text:
                metrics["band"] = "5G"
            elif "2.4" in band_text or "BGN" in band_text:
                metrics["band"] = "2.4G"
            else:
                metrics["band"] = band_text
            continue
        number = _to_number(value)
        if number is not None:
            metrics[field] = number

    return {"metrics": metrics, "raw": raw, "source": source}


_RE_SSID = re.compile(r"(?:ssid|wifi\s*名称)\s*[:=]\s*([^\s,;]+)", re.IGNORECASE)


def parse_wifi_report(text):
    """解析一份简短的无线自检文本：SSID / RSSI / SNR / 重传率 / 信道占用。

    适合把手机 WiFi 分析 App 或路由 status 页的几行复制进来。
    """
    result = {"ssid": None, "metrics": {}}
    body = text or ""
    match = _RE_SSID.search(body)
    if match:
        result["ssid"] = match.group(1).strip()

    keys = {
        "rssi": ("rssi", "信号强度", "signal"),
        "snr": ("snr", "信噪比"),
        "retry_pct": ("retry", "重传", "重传率"),
        "channel_util": ("channelutil", "channel_util", "信道占用", "util"),
        "tx_rate": ("txrate", "协商速率", "linkrate"),
        "noise": ("noise", "底噪"),
    }
    for field, aliases in keys.items():
        for alias in aliases:
            pattern = re.compile(r"%s\s*[:=]\s*([-+]?\d+(?:\.\d+)?)" % re.escape(alias), re.IGNORECASE)
            found = pattern.search(body)
            if found:
                result["metrics"][field] = float(found.group(1))
                break
    return result
