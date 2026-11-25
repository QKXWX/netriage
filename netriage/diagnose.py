#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断引擎：把测量数据 + 拨号日志 + 无线信号，折算成可解释的根因排序。

设计原则：
1. 每条结论都必须挂证据（具体数字或日志行），不允许“可能网络不好”这种空话；
2. 打分是确定性的加权证据，同一份输入永远得到同一份输出，便于复现和写测试；
3. 证据不足时明确说“数据不足”，不硬凑结论。
"""

import datetime

from . import parsers

# 根因定义：名称、所属层、处置动作。动作按“先做哪一步”排序。
CAUSES = {
    "provisioning": {
        "name": "运营商端口 / 套餐带宽未达标",
        "layer": "运营商侧",
        "actions": [
            "用网线直连光猫 LAN1 口复测一次，排除路由与 WiFi 的干扰",
            "在 20:00–23:00 高峰时段再测一次，记录早/中/晚三组数据",
            "拨打运营商报障，要求出具「线路测速单」；实测长期低于签约速率可要求整改或换端口",
        ],
    },
    "qos": {
        "name": "路由 QoS / 单流限速",
        "layer": "配置",
        "actions": [
            "检查路由的 QoS、智能限速、带宽分配是否开启，单流限速常把下载压到套餐的 1/3",
            "临时关闭 OpenClash、广告过滤、流控插件后复测，确认是否插件造成",
            "用多线程下载器（aria2 -x16）对比单线程结果：单流低、多流正常即为限速特征",
        ],
    },
    "congestion": {
        "name": "上联 / 城域网拥塞",
        "layer": "运营商侧",
        "actions": [
            "换时段复测：高峰慢、凌晨正常，基本可判定为上联拥塞",
            "路由开启 SQM（fq_codel 或 cake），上行限到套餐的 90%，压制缓冲膨胀",
            "排查内网大流量设备：NAS 备份、BT 做种、监控回传都会长期占满上行",
        ],
    },
    "line_quality": {
        "name": "线路质量差 / 丢包",
        "layer": "线路",
        "actions": [
            "分别 ping 网关和公网地址：网关也丢包查内网与光猫，只有公网丢包查线路",
            "更换网线、换光猫另一个 LAN 口，排除物理层问题",
            "光猫状态页读光功率与误码率，异常直接报障，不要自行处理光纤",
        ],
    },
    "wifi": {
        "name": "无线干扰 / 覆盖不足",
        "layer": "无线",
        "actions": [
            "优先 5GHz，信道固定 149/157，带宽 80MHz；2.4GHz 只用 1/6/11 三个互不重叠信道",
            "靠近 AP 再测一次：明显变好说明是覆盖问题，加 AP 或调整摆放位置",
            "逐个关闭 MLO、802.11r、beamforming 等试验特性，观察重传率是否下降",
        ],
    },
    "optical": {
        "name": "光衰超标 / 光纤链路异常",
        "layer": "线路",
        "actions": [
            "清洁光纤端面与冷接子，检查尾纤是否被弯折、压住",
            "光猫状态页读 Rx Power：低于 -27dBm 直接报障光衰，不要自己熔接",
            "报障时说明「光功率偏低」，要求装维上门测光并更换尾纤/分光器端口",
        ],
    },
    "dialing": {
        "name": "拨号 / 认证异常",
        "layer": "运营商侧",
        "actions": [
            "核对宽带账号密码，确认没有欠费停机或账号被绑定到旧端口",
            "把 PPPoE MTU 调到 1492（部分局端要求 1480），避免大包被丢弃",
            "重启光猫与路由，等 2 分钟再拨号；仍报 691 联系运营商解绑或重置密码",
        ],
    },
    "device": {
        "name": "路由 / 终端性能瓶颈",
        "layer": "设备",
        "actions": [
            "查看路由 CPU 与内存占用，关掉不必要的插件与日志服务",
            "换一台终端直连复测，排除单台设备网卡或省电策略的影响",
            "无线协商速率明显低于套餐时，确认没跑在 2.4GHz 或老标准上",
        ],
    },
}

CAUSE_ORDER = list(CAUSES)


def _num(source, *keys, **kwargs):
    """从 dict 里按多个候选键取数字，取不到返回 default。"""
    default = kwargs.get("default")
    if not isinstance(source, dict):
        return default
    for key in keys:
        if key not in source:
            continue
        value = source[key]
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip().replace("%", "").replace("ms", "").replace("Mbps", "")
            try:
                return float(text)
            except ValueError:
                continue
    return default


def _round(value, digits=1):
    if value is None:
        return None
    return round(float(value), digits)


def normalize(payload):
    """把前端 / CLI 的原始 payload 归一成引擎内部结构。"""
    payload = payload if isinstance(payload, dict) else {}
    plan_raw = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    measure = payload.get("measure") if isinstance(payload.get("measure"), dict) else {}
    wifi_raw = payload.get("wifi") if isinstance(payload.get("wifi"), dict) else {}
    lqc_raw = payload.get("lqc")

    plan_down = _num(plan_raw, "down", "down_mbps", "download")
    plan_up = _num(plan_raw, "up", "up_mbps", "upload")

    down = _num(measure, "down_mbps", "download_mbps", "down")
    up = _num(measure, "up_mbps", "upload_mbps", "up")
    down_single = _num(measure, "down_single_mbps", "single_mbps")
    down_multi = _num(measure, "down_multi_mbps", "multi_mbps")
    up_multi = _num(measure, "up_multi_mbps", "up_mbps")

    latency_idle = _num(measure, "latency_idle_ms", "latency_ms", "idle_ms")
    latency_loaded = _num(measure, "latency_loaded_ms", "loaded_ms")
    jitter = _num(measure, "jitter_ms", "jitter")
    loss = _num(measure, "loss_pct", "loss")
    bufferbloat = _num(measure, "bufferbloat_ms", "bloat_ms")
    if bufferbloat is None and latency_idle is not None and latency_loaded is not None:
        bufferbloat = max(0.0, latency_loaded - latency_idle)

    wifi = {
        "rssi": _num(wifi_raw, "rssi"),
        "snr": _num(wifi_raw, "snr", "sinr"),
        "retry_pct": _num(wifi_raw, "retry_pct", "retry", "retry_rate"),
        "channel_util": _num(wifi_raw, "channel_util", "util"),
        "tx_rate": _num(wifi_raw, "tx_rate", "rate"),
        "band": wifi_raw.get("band") if isinstance(wifi_raw.get("band"), str) else None,
    }

    lqc = {"metrics": {}, "raw": {}, "source": "empty"}
    if lqc_raw:
        parsed = parsers.parse_lqc(lqc_raw)
        lqc = parsed
        # LQC 里的无线指标可以补充手填字段
        merge_map = {"rssi": "rssi", "rsrp": "rssi", "snr": "snr", "sinr": "snr", "tx_rate": "tx_rate"}
        for src, dst in merge_map.items():
            value = parsed["metrics"].get(src)
            if value is not None and wifi.get(dst) is None:
                wifi[dst] = value

    dial_text = payload.get("dial_log")
    if isinstance(dial_text, (dict, list)):
        dial_text = "\n".join(str(item) for item in dial_text) if isinstance(dial_text, list) else str(dial_text)
    dial = parsers.parse_dial_log(dial_text or "")

    system_raw = payload.get("system") if isinstance(payload.get("system"), dict) else {}
    if not system_raw and isinstance(payload.get("router"), dict):
        system_raw = payload["router"]
    system = {
        "model": system_raw.get("model") if isinstance(system_raw.get("model"), str) else None,
        "firmware": system_raw.get("firmware") if isinstance(system_raw.get("firmware"), str) else None,
        "uptime_s": _num(system_raw, "uptime_s", "uptime"),
        "load1": _num(system_raw, "load1", "load_1"),
        "load5": _num(system_raw, "load5", "load_5"),
        "cores": _num(system_raw, "cores", "cpu_cores"),
        "mem_total_mb": _num(system_raw, "mem_total_mb", "mem_total"),
        "mem_free_mb": _num(system_raw, "mem_free_mb", "mem_available_mb", "mem_free"),
        "wan_if": system_raw.get("wan_if") if isinstance(system_raw.get("wan_if"), str) else None,
        "rx_errors": _num(system_raw, "rx_errors"),
        "tx_errors": _num(system_raw, "tx_errors"),
        "rx_dropped": _num(system_raw, "rx_dropped"),
        "tx_dropped": _num(system_raw, "tx_dropped"),
    }

    return {
        "plan_down": plan_down,
        "plan_up": plan_up,
        "down": down,
        "up": up,
        "down_single": down_single,
        "down_multi": down_multi,
        "up_multi": up_multi,
        "latency_idle": latency_idle,
        "latency_loaded": latency_loaded,
        "jitter": jitter,
        "loss": loss,
        "bufferbloat": bufferbloat,
        "wifi": wifi,
        "lqc": lqc,
        "dial": dial,
        "system": system,
        "meta": {
            "account": (payload.get("account") or "").strip() if isinstance(payload.get("account"), str) else "",
            "address": (payload.get("address") or "").strip() if isinstance(payload.get("address"), str) else "",
            "contact": (payload.get("contact") or "").strip() if isinstance(payload.get("contact"), str) else "",
            "isp": (payload.get("isp") or "").strip() if isinstance(payload.get("isp"), str) else "",
            "plan_type": (plan_raw.get("type") or "").strip() if isinstance(plan_raw.get("type"), str) else "",
            "note": (payload.get("note") or "").strip() if isinstance(payload.get("note"), str) else "",
        },
    }


def _check(checks, key, name, value, unit, good, warn, lower_is_better=False):
    """记录一条可展示的检查项。

    good / warn 是边界值；lower_is_better 表示数值越小越好。
    """
    if value is None:
        checks.append({"key": key, "name": name, "value": None, "unit": unit, "status": "unknown"})
        return
    if lower_is_better:
        status = "ok" if value <= good else ("warn" if value <= warn else "crit")
    else:
        status = "ok" if value >= good else ("warn" if value >= warn else "crit")
    checks.append({"key": key, "name": name, "value": _round(value, 2), "unit": unit, "status": status})


def diagnose(payload):
    """主入口：输入 payload，输出根因排序 + 检查表 + 摘要 + 工单。"""
    data = normalize(payload)
    evidence = []
    checks = []
    notes = []

    def add(cause, weight, text):
        evidence.append({"cause": cause, "weight": round(float(weight), 2), "text": text})

    plan_down, plan_up = data["plan_down"], data["plan_up"]
    down, up = data["down"], data["up"]
    down_single, down_multi = data["down_single"], data["down_multi"]
    wifi = data["wifi"]
    dial = data["dial"]

    # ---------- 吞吐与达标率 ----------
    ratio_down = (down / plan_down) if (down is not None and plan_down) else None
    ratio_up = (up / plan_up) if (up is not None and plan_up) else None

    _check(checks, "down_ratio", "下行达标率", (ratio_down * 100) if ratio_down is not None else None, "%", 90, 70)
    if ratio_down is not None:
        if ratio_down < 0.5:
            add("provisioning", 4.0, "下行实测 %.1f Mbps，仅签约 %.0f Mbps 的 %.0f%%" % (down, plan_down, ratio_down * 100))
        elif ratio_down < 0.85:
            add("provisioning", 2.2, "下行 %.1f Mbps，为签约速率的 %.0f%%，低于达标线" % (down, ratio_down * 100))
        if ratio_down < 0.5 and down_multi and plan_down and down_multi < plan_down * 0.85:
            add("provisioning", 2.0, "多流合计也只有 %.0f Mbps（签约 %.0f），不是单流限速，更像是端口或线路总限速"
                % (down_multi, plan_down))
        elif ratio_down >= 0.95 and down:
            notes.append("下行 %.1f Mbps，达到签约速率的 %.0f%%" % (down, ratio_down * 100))

    _check(checks, "up_ratio", "上行达标率", (ratio_up * 100) if ratio_up is not None else None, "%", 90, 70)
    if ratio_up is not None:
        if ratio_up < 0.4:
            add("provisioning", 2.6, "上行实测 %.1f Mbps，仅为签约 %.0f Mbps 的 %.0f%%" % (up, plan_up, ratio_up * 100))
        elif ratio_up < 0.85:
            add("provisioning", 1.4, "上行 %.1f Mbps，为签约的 %.0f%%" % (up, ratio_up * 100))

    if plan_down is None:
        notes.append("未填写套餐下行速率，达标率类判断已跳过；补上套餐后结论会更准")

    # ---------- 单流 vs 多流 ----------
    stream_ratio = None
    if down_single and down_multi and down_single > 0:
        stream_ratio = down_multi / down_single
        stream_status = "ok" if stream_ratio < 1.2 else ("warn" if stream_ratio < 1.6 else "crit")
        checks.append({"key": "stream_ratio", "name": "多流 / 单流倍率", "value": _round(stream_ratio, 2),
                       "unit": "x", "status": stream_status})
        if stream_ratio >= 1.6:
            add("qos", 3.6, "单流只有 %.1f Mbps，四流合计 %.1f Mbps（%.1f 倍），典型单流限速特征"
                % (down_single, down_multi, stream_ratio))
        elif stream_ratio < 1.1 and ratio_down is not None and ratio_down < 0.85:
            add("provisioning", 1.5, "单流 %.1f Mbps、多流 %.1f Mbps 都没有提升，不是单流限速，更像端口或线路限制"
                % (down_single, down_multi))
        if ratio_down is not None and ratio_down >= 0.85 and stream_ratio < 1.1:
            notes.append("多流与单流基本一致（%.1f 倍），说明不是单线程限速" % stream_ratio)

    # ---------- 延迟 / 抖动 / 丢包 ----------
    if data["latency_idle"] is not None:
        _check(checks, "latency", "空闲延迟", data["latency_idle"], "ms", 20, 50, lower_is_better=True)
        if data["latency_idle"] > 120:
            add("line_quality", 2.4, "空闲延迟 %.0f ms，明显偏高" % data["latency_idle"])
        elif data["latency_idle"] > 50:
            add("line_quality", 1.2, "空闲延迟 %.0f ms，高于 50 ms" % data["latency_idle"])

    if data["jitter"] is not None:
        _check(checks, "jitter", "抖动", data["jitter"], "ms", 6, 15, lower_is_better=True)
        if data["jitter"] >= 15:
            add("line_quality", 2.2, "抖动 %.1f ms，链路不稳定" % data["jitter"])
        elif data["jitter"] >= 8:
            add("line_quality", 0.9, "抖动 %.1f ms，略高" % data["jitter"])

    if data["loss"] is not None:
        _check(checks, "loss", "丢包率", data["loss"], "%", 0.5, 2.0, lower_is_better=True)
        if data["loss"] >= 2:
            add("line_quality", 3.4, "探测丢包率 %.1f%%，超过 2%%" % data["loss"])
        elif data["loss"] >= 0.5:
            add("line_quality", 1.6, "探测丢包率 %.1f%%" % data["loss"])

    # ---------- 缓冲膨胀 ----------
    if data["bufferbloat"] is not None:
        _check(checks, "bufferbloat", "满载延迟增量", data["bufferbloat"], "ms", 50, 150, lower_is_better=True)
        if data["bufferbloat"] >= 200:
            add("congestion", 3.8, "下载跑满时延迟增加 %.0f ms，队列严重堆积" % data["bufferbloat"])
            add("qos", 1.2, "缓冲膨胀常与路由未开 SQM、队列无主动管理有关")
        elif data["bufferbloat"] >= 100:
            add("congestion", 2.0, "满载延迟增加 %.0f ms，存在排队" % data["bufferbloat"])
    elif data["latency_loaded"] is not None:
        add("congestion", 1.0, "满载延迟 %.0f ms 已记录，但缺少空闲基线，增量无法判定" % data["latency_loaded"])

    # ---------- 无线 ----------
    rssi, snr = wifi.get("rssi"), wifi.get("snr")
    if rssi is not None:
        _check(checks, "rssi", "信号强度", rssi, "dBm", -65, -75)
        if rssi <= -78:
            add("wifi", 3.0, "WiFi 信号 %.0f dBm，属于弱覆盖区" % rssi)
        elif rssi <= -70:
            add("wifi", 1.8, "WiFi 信号 %.0f dBm，偏弱" % rssi)
    if snr is not None:
        _check(checks, "snr", "信噪比", snr, "dB", 25, 15)
        if snr <= 15:
            add("wifi", 2.4, "信噪比 %.0f dB，干扰明显" % snr)
        elif snr <= 22:
            add("wifi", 1.2, "信噪比 %.0f dB，一般" % snr)

    retry = wifi.get("retry_pct")
    if retry is not None:
        _check(checks, "retry", "无线重传率", retry, "%", 8, 20, lower_is_better=True)
        if retry >= 20:
            add("wifi", 2.8, "重传率 %.0f%%，空口质量差" % retry)
        elif retry >= 10:
            add("wifi", 1.4, "重传率 %.0f%%，偏高" % retry)

    channel_util = wifi.get("channel_util")
    if channel_util is not None:
        _check(checks, "channel_util", "信道占用", channel_util, "%", 60, 75, lower_is_better=True)
        if channel_util >= 75:
            add("wifi", 2.0, "信道占用 %.0f%%，同频竞争激烈" % channel_util)

    if wifi.get("band") == "2.4G" and plan_down and plan_down >= 200:
        add("wifi", 1.2, "当前跑在 2.4GHz，而套餐下行 %.0f Mbps，物理上限先被卡住" % plan_down)

    tx_rate = wifi.get("tx_rate")
    if tx_rate is not None and plan_down:
        _check(checks, "tx_rate", "无线协商速率", tx_rate, "Mbps", plan_down, plan_down * 0.6)
        if tx_rate < plan_down * 0.6:
            add("device", 1.8, "无线协商速率 %.0f Mbps，低于套餐 %.0f Mbps 的 60%%" % (tx_rate, plan_down))

    # ---------- 光功率 ----------
    rx_power = data["lqc"]["metrics"].get("rx_power")
    if rx_power is not None:
        _check(checks, "rx_power", "光功率", rx_power, "dBm", -25, -27)
        if rx_power <= -27:
            add("optical", 4.4, "光功率 %.1f dBm，低于 -27 dBm 告警线" % rx_power)
        elif rx_power <= -25:
            add("optical", 2.2, "光功率 %.1f dBm，接近临界" % rx_power)

    # ---------- 路由自身状态（OpenWrt 采集器导入） ----------
    system = data["system"]
    uptime_s = system["uptime_s"]
    if uptime_s is not None:
        _check(checks, "uptime_h", "路由运行时长", uptime_s / 3600.0, "h", 24, 1)
        if uptime_s < 3600:
            add("device", 1.4, "路由运行不足 1 小时（%.0f 分钟），刚重启/掉电会让拨号和 WiFi 一起不稳"
                % (uptime_s / 60.0))
        elif uptime_s < 86400:
            notes.append("路由已运行 %.1f 小时，暂未发现长时间运行特征" % (uptime_s / 3600.0))

    load1 = system["load1"]
    if load1 is not None:
        cores = system["cores"] or 1.0
        load_ratio = load1 / cores if cores else load1
        _check(checks, "load_ratio", "路由 CPU 负载", load_ratio, "x", 0.7, 1.0, lower_is_better=True)
        if load_ratio >= 1.0:
            add("device", 2.4, "路由 1 分钟负载 %.2f（%d 核），CPU 已跑满，NAT/测速会先撞设备上限"
                % (load1, int(cores)))
        elif load_ratio >= 0.7:
            add("device", 1.0, "路由 1 分钟负载 %.2f（%d 核），偏高" % (load1, int(cores)))

    mem_total, mem_free = system["mem_total_mb"], system["mem_free_mb"]
    if mem_total and mem_free is not None:
        free_pct = mem_free / mem_total * 100
        _check(checks, "mem_free_pct", "路由可用内存", free_pct, "%", 20, 10)
        if free_pct < 10:
            add("device", 1.8, "路由可用内存仅 %.0f MB（%.0f%%），可能触发 OOM 或频繁回收" % (mem_free, free_pct))
        elif free_pct < 20:
            add("device", 0.8, "路由可用内存 %.0f MB（%.0f%%），偏紧" % (mem_free, free_pct))

    errors = (system["rx_errors"] or 0) + (system["tx_errors"] or 0)
    if errors and uptime_s:
        per_hour = errors / max(uptime_s / 3600.0, 0.1)
        _check(checks, "wan_err_rate", "WAN 接口错误", per_hour, "个/小时", 1, 20, lower_is_better=True)
        if per_hour >= 20:
            add("line_quality", 2.2, "WAN 接口累计 %d 个错误，约 %.0f 个/小时，先查网线/光路/接口协商"
                % (errors, per_hour))
        elif per_hour >= 1:
            add("line_quality", 0.8, "WAN 接口约 %.0f 个错误/小时，虽不致命但值得留意" % per_hour)

    # ---------- 拨号日志 ----------
    if dial["lines"]:
        if dial["auth_failures"]:
            add("dialing", 4.0, "拨号日志出现 %d 次认证失败" % dial["auth_failures"])
        for item in dial["error_codes"]:
            if item["code"] in ("678", "651"):
                add("dialing", 3.0, "拨号错误 %s（%s），出现 %d 次" % (item["code"], item["meaning"], item["count"]))
            elif item["code"] in ("676", "718", "720", "769"):
                add("dialing", 1.6, "拨号错误 %s（%s），出现 %d 次" % (item["code"], item["meaning"], item["count"]))
        if dial["drops"] >= 3:
            add("line_quality", 2.0, "日志记录 %d 次掉线 / 链路中断" % dial["drops"])
        elif dial["drops"] >= 1:
            add("line_quality", 0.8, "日志记录 %d 次掉线" % dial["drops"])
        if dial["lcp_timeouts"]:
            add("dialing", 2.0, "日志出现 %d 次 LCP 超时 / 无响应" % dial["lcp_timeouts"])
        if dial["dial_attempts"] >= 5 and not dial["got_ip"]:
            add("dialing", 1.4, "日志里有 %d 次 PADI 拨号请求但没有拿到 IP" % dial["dial_attempts"])
        if dial["got_ip"] and not dial["drops"] and not dial["error_codes"]:
            notes.append("拨号日志未见异常，共 %d 次成功获取地址" % dial["got_ip"])
    else:
        notes.append("未提供拨号日志，认证与掉线类结论只能靠吞吐和延迟推断")

    if not wifi.get("rssi") and not wifi.get("snr") and data["lqc"]["source"] == "empty":
        notes.append("未提供无线信号数据，无法区分「运营商慢」和「WiFi 慢」；建议补一次 LQC 或信号截图")

    # ---------- 汇总打分 ----------
    scores = {}
    for item in evidence:
        scores[item["cause"]] = scores.get(item["cause"], 0.0) + item["weight"]

    ranked = []
    for cause_id, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        confidence = score / (score + 4.0)
        if confidence < 0.2:
            continue
        cause = CAUSES[cause_id]
        ranked.append({
            "id": cause_id,
            "name": cause["name"],
            "layer": cause["layer"],
            "score": round(score, 1),
            "confidence": round(min(confidence, 0.93), 3),
            "evidence": [e["text"] for e in evidence if e["cause"] == cause_id],
            "actions": cause["actions"],
        })

    total_evidence = sum(item["weight"] for item in evidence)
    if ranked:
        top = ranked[0]
        if top["confidence"] >= 0.5:
            verdict = "高度怀疑：" + top["name"]
        elif top["confidence"] >= 0.3:
            verdict = "可疑：" + top["name"]
        else:
            verdict = "存在异常，但证据不足以锁定单一根因"
        summary = "%s（置信 %d%%）。主要依据：%s。" % (
            top["name"], int(top["confidence"] * 100), top["evidence"][0]
        )
    else:
        verdict = "未发现明确异常"
        summary = "吞吐、延迟、抖动、丢包都在阈值内。若体感仍然差，补一次无线信号与拨号日志再诊断。"

    # 健康分：按检查表的严重度占比折算，严重算 1 份、提示算 0.5 份
    crit = sum(1 for item in checks if item["status"] == "crit")
    warn = sum(1 for item in checks if item["status"] == "warn")
    measured = sum(1 for item in checks if item["status"] != "unknown")
    if measured:
        bad = (crit + warn * 0.5) / measured
        health = int(max(0, min(100, round(100 - bad * 100))))
    else:
        health = 0

    result = {
        "verdict": verdict,
        "summary": summary,
        "health": health,
        "causes": ranked[:3],
        "checks": checks,
        "notes": notes,
        "metrics": {
            "plan_down": plan_down,
            "plan_up": plan_up,
            "down_mbps": down,
            "up_mbps": up,
            "down_single_mbps": down_single,
            "down_multi_mbps": down_multi,
            "latency_idle_ms": data["latency_idle"],
            "latency_loaded_ms": data["latency_loaded"],
            "bufferbloat_ms": data["bufferbloat"],
            "jitter_ms": data["jitter"],
            "loss_pct": data["loss"],
            "stream_ratio": _round(stream_ratio, 2),
        },
        "wifi": {k: v for k, v in wifi.items() if v is not None},
        "dial": dial,
        "evidence_total": round(total_evidence, 1),
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return result


def build_ticket(payload, result):
    """生成可直接发运营商的书面报障单（纯文本，方便复制/落单）。"""
    data = normalize(payload)
    meta = data["meta"]
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append("宽带故障报障单")
    lines.append("=" * 46)
    lines.append("报障时间：%s" % now)
    lines.append("宽带账号：%s" % (meta["account"] or "（未填写）"))
    lines.append("安装地址：%s" % (meta["address"] or "（未填写）"))
    lines.append("运营商：%s" % (meta["isp"] or "（未填写）"))
    lines.append("套餐速率：%s" % ("下行 %s / 上行 %s Mbps" % (
        ("%g" % data["plan_down"]) if data["plan_down"] else "未知",
        ("%g" % data["plan_up"]) if data["plan_up"] else "未知")))
    lines.append("")
    lines.append("一、实测数据")
    lines.append("  下行速率：%s Mbps" % (_fmt(data["down"])))
    lines.append("  上行速率：%s Mbps" % (_fmt(data["up"])))
    if data["down_single"] and data["down_multi"]:
        lines.append("  单流下行：%s Mbps；四流合计：%s Mbps" % (_fmt(data["down_single"]), _fmt(data["down_multi"])))
    lines.append("  空闲延迟：%s ms；抖动：%s ms；探测丢包：%s%%" % (
        _fmt(data["latency_idle"], 0), _fmt(data["jitter"], 1), _fmt(data["loss"], 2)))
    if data["bufferbloat"] is not None:
        lines.append("  满载延迟增量：%s ms" % _fmt(data["bufferbloat"], 0))
    lines.append("")
    lines.append("二、诊断结论")
    lines.append("  %s" % result.get("verdict", ""))
    for index, cause in enumerate(result.get("causes", [])[:2], 1):
        lines.append("  %d. %s（置信 %d%%）" % (index, cause["name"], int(cause["confidence"] * 100)))
        for ev in cause["evidence"][:2]:
            lines.append("     - %s" % ev)
    lines.append("")
    lines.append("三、已做排查（未做的条目请删除）")
    lines.append("  1. 已用网线直连光猫复测，排除家庭路由与 WiFi 影响")
    lines.append("  2. 已更换网线与光猫端口")
    lines.append("  3. 已记录不同时段多组测速数据")
    lines.append("")
    lines.append("四、诉求")
    lines.append("  1. 请核查本账号端口速率配置是否与套餐一致")
    lines.append("  2. 请安排装维上门检测线路质量与光功率")
    lines.append("  3. 如线路正常，请出具书面测速单并说明限速原因")
    if meta["contact"]:
        lines.append("")
        lines.append("联系人：%s" % meta["contact"])
    if meta["note"]:
        lines.append("补充说明：%s" % meta["note"])
    lines.append("")
    lines.append("（本单由 netriage 自动汇总实测数据生成，数值可在页面上复核）")
    return "\n".join(lines)


def _fmt(value, digits=1):
    if value is None:
        return "未测"
    return ("%." + str(digits) + "f") % float(value)


def ticket_for(payload, result=None):
    return build_ticket(payload, result or diagnose(payload))


def health_label(health):
    if health >= 85:
        return "良好"
    if health >= 70:
        return "一般"
    if health >= 50:
        return "较差"
    return "严重"
