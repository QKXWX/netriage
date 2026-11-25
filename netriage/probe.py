#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行侧主动探测：不经浏览器，直接向 netriage 服务端打流并算指标。

为什么需要它：
- 浏览器测速只能人工点一次，间歇性故障（晚上八点掉速、丢包）根本抓不住；
- 放到路由器 / 小主机上配合 cron，每 10 分钟测一次并入库，才有可对比的证据链。

测的是「本机 → netriage 服务」这一段，和服务端网页测速口径一致：
下行单流、上行单流、空闲 RTT、抖动、丢包，以及下载满载时的延迟（bufferbloat）。
"""

import datetime
import json
import statistics
import threading
import time
import urllib.error
import urllib.request

USER_AGENT = "netriage-probe"


def _opener():
    # 直连：探测目标通常在同一局域网，走系统代理会污染时延
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _url(server, path):
    return server.rstrip("/") + path


def _request(server, path, data=None, timeout=10.0, method=None):
    request = urllib.request.Request(_url(server, path), data=data, method=method)
    request.add_header("User-Agent", USER_AGENT)
    if data is not None:
        request.add_header("Content-Type", "application/octet-stream")
    return _opener().open(request, timeout=timeout)


def _rtt_once(server, timeout):
    started = time.perf_counter()
    try:
        with _request(server, "/api/ping", timeout=timeout) as response:
            response.read()
    except (urllib.error.URLError, OSError, urllib.error.HTTPError):
        return None
    return (time.perf_counter() - started) * 1000.0


def ping(server, count=10, timeout=3.0, interval=0.15):
    """连续打 count 次空请求，返回 RTT 列表（失败为 None）。"""
    samples = []
    for index in range(max(1, int(count))):
        samples.append(_rtt_once(server, timeout))
        if index + 1 < count:
            time.sleep(max(0.0, interval))
    return samples


def summarize_ping(samples):
    ok = [value for value in samples if value is not None]
    lost = len(samples) - len(ok)
    if not ok:
        return {"avg_ms": None, "min_ms": None, "max_ms": None, "jitter_ms": None,
                "loss_pct": round(lost / len(samples) * 100.0, 2), "samples": samples}
    jitter = 0.0
    if len(ok) >= 2:
        jitter = statistics.mean(abs(ok[i] - ok[i - 1]) for i in range(1, len(ok)))
    return {
        "avg_ms": round(statistics.mean(ok), 2),
        "min_ms": round(min(ok), 2),
        "max_ms": round(max(ok), 2),
        "jitter_ms": round(jitter, 2),
        "loss_pct": round(lost / len(samples) * 100.0, 2),
        "samples": [None if value is None else round(value, 2) for value in samples],
    }


def _fetch_bytes(server, size, timeout, chunk=64 * 1024):
    received = 0
    with _request(server, "/api/download?mb=%.3f" % (size / 1024 / 1024), timeout=timeout) as response:
        while True:
            block = response.read(chunk)
            if not block:
                break
            received += len(block)
    return received


def download(server, megabytes=8.0, timeout=30.0, chunk=64 * 1024):
    """单流下载指定 MB，返回客户端实测速率。"""
    size = max(1, int(float(megabytes) * 1024 * 1024))
    started = time.perf_counter()
    received = _fetch_bytes(server, size, timeout, chunk)
    elapsed = max(time.perf_counter() - started, 1e-6)
    return {
        "bytes": received,
        "seconds": round(elapsed, 3),
        "mbps": round(received * 8 / 1e6 / elapsed, 2),
    }


def download_multi(server, megabytes=8.0, streams=4, timeout=30.0):
    """四流并发下载同样的总字节数，用来识别「单流限速」。"""
    total = max(1, int(float(megabytes) * 1024 * 1024))
    per_stream = max(1, total // max(1, int(streams)))
    results = [0] * streams
    errors = []

    def worker(index):
        try:
            results[index] = _fetch_bytes(server, per_stream, timeout)
        except Exception as exc:                        # 线程里的异常带回主线程
            errors.append(exc)

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(streams)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout + 5)
    elapsed = max(time.perf_counter() - started, 1e-6)
    if errors:
        raise errors[0]
    received = sum(results)
    return {
        "bytes": received,
        "streams": streams,
        "seconds": round(elapsed, 3),
        "mbps": round(received * 8 / 1e6 / elapsed, 2),
    }


def upload(server, megabytes=4.0, timeout=30.0):
    """单流上传指定 MB，返回客户端实测速率（与服务端统计互相印证）。"""
    size = max(1, int(float(megabytes) * 1024 * 1024))
    payload = b"x" * size
    started = time.perf_counter()
    with _request(server, "/api/upload", data=payload, timeout=timeout) as response:
        body = response.read()
    elapsed = max(time.perf_counter() - started, 1e-6)
    server_side = None
    try:
        server_side = json.loads(body.decode("utf-8")).get("mbps")
    except Exception:
        pass
    return {
        "bytes": size,
        "seconds": round(elapsed, 3),
        "mbps": round(size * 8 / 1e6 / elapsed, 2),
        "server_mbps": server_side,
    }


def _loaded_ping(server, stop_event, samples, timeout=3.0, interval=0.12):
    while not stop_event.is_set():
        value = _rtt_once(server, timeout)
        if value is not None:
            samples.append(value)
        stop_event.wait(interval)


def probe(server, download_mb=8.0, upload_mb=4.0, ping_count=10, timeout=30.0,
          load_cap_mb=128.0):
    """跑一轮完整探测，返回可直接喂给 diagnose 的 measure 字典。

    四流负载按实测速率放大到约 1.2 秒（上限 load_cap_mb），让满载延迟有意义；
    如果负载不足 0.8 秒、或四流反而明显低于单流（客户端 CPU 到顶），
    就不采信多流数据，宁可留空也不给诊断喂假证据。
    """
    idle_samples = ping(server, count=ping_count, timeout=min(timeout, 5.0))
    idle = summarize_ping(idle_samples)

    single = download(server, download_mb, timeout=timeout)
    multi_mb = min(float(load_cap_mb), max(2.0, float(download_mb) * 2, single["mbps"] * 1.2 / 8))

    loaded_samples = []
    stop_event = threading.Event()
    watcher = threading.Thread(target=_loaded_ping, args=(server, stop_event, loaded_samples), daemon=True)
    watcher.start()
    try:
        multi = download_multi(server, multi_mb, streams=4, timeout=timeout)
    finally:
        stop_event.set()
        watcher.join(timeout=5)
    loaded = summarize_ping(loaded_samples) if loaded_samples else None

    up = upload(server, upload_mb, timeout=timeout)

    bufferbloat = None
    valid_load = multi["seconds"] >= 0.8
    multi_valid = valid_load and multi["mbps"] >= single["mbps"] * 0.85
    if valid_load and loaded and loaded["avg_ms"] is not None and idle["avg_ms"] is not None:
        bufferbloat = round(max(0.0, loaded["avg_ms"] - idle["avg_ms"]), 2)

    return {
        "down_mbps": multi["mbps"] if multi_valid else single["mbps"],
        "up_mbps": up["mbps"],
        "down_single_mbps": single["mbps"],
        "down_multi_mbps": multi["mbps"] if multi_valid else None,
        "multi_valid": multi_valid,
        "latency_idle_ms": idle["avg_ms"],
        "latency_loaded_ms": loaded["avg_ms"] if valid_load and loaded else None,
        "latency_loaded_max_ms": loaded["max_ms"] if valid_load and loaded else None,
        "bufferbloat_ms": bufferbloat,
        "bufferbloat_valid": valid_load,
        "load_seconds": multi["seconds"],
        "jitter_ms": idle["jitter_ms"],
        "loss_pct": idle["loss_pct"],
        "ping_min_ms": idle["min_ms"],
        "ping_max_ms": idle["max_ms"],
        "down_seconds": single["seconds"],
        "up_seconds": up["seconds"],
        "down_mb": round(single["bytes"] / 1024 / 1024, 2),
        "load_mb": round(multi["bytes"] / 1024 / 1024, 2),
        "up_mb": round(up["bytes"] / 1024 / 1024, 2),
        "up_server_mbps": up["server_mbps"],
        "measured_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "target": server,
    }


def probe_payload(server, plan=None, **kwargs):
    """探测 + 组装成 payload（plan 可选，用于算达标率）。"""
    payload = {"measure": probe(server, **kwargs), "source": "netriage-probe"}
    if plan:
        payload["plan"] = plan
    return payload


def diagnose_remote(server, payload, timeout=30.0, save=True):
    """把 payload 交给服务端诊断；save=True 时由服务端入库。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path = "/api/diagnose" + ("" if save else "?save=0")
    request = urllib.request.Request(_url(server, path), data=body, method="POST")
    request.add_header("Content-Type", "application/json; charset=utf-8")
    request.add_header("User-Agent", USER_AGENT)
    with _opener().open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def watch(server, interval=600, count=0, plan=None, download_mb=4.0, upload_mb=2.0,
          ping_count=8, load_cap_mb=16.0, out=None, sleep=None):
    """定时复测。count=0 表示一直跑到 Ctrl-C。返回每轮结果列表。"""
    sleep = sleep or time.sleep
    rounds = []
    finished = 0
    try:
        while True:
            started = time.time()
            try:
                payload = probe_payload(server, plan=plan, download_mb=download_mb,
                                        upload_mb=upload_mb, ping_count=ping_count,
                                        load_cap_mb=load_cap_mb)
                answer = diagnose_remote(server, payload)
            except Exception as exc:                       # 单轮失败不能终止整个看守
                message = "第 %d 轮探测失败：%s" % (finished + 1, exc)
                print(message, flush=True)
                if out:
                    out.write(json.dumps({"error": str(exc), "at": datetime.datetime.now().isoformat()},
                                         ensure_ascii=False) + "\n")
                    out.flush()
            else:
                result = answer["result"]
                measure = payload["measure"]
                rounds.append({"id": answer.get("id"), "payload": payload, "result": result})
                multi_note = "" if measure["multi_valid"] else "(四流未采信)"
                line = "[%s] #%s 健康 %d  ↓%s↑%s Mbps%s  空闲 %sms 满载 %s 抖动 %sms 丢包 %s%%  %s" % (
                    datetime.datetime.now().strftime("%H:%M:%S"),
                    answer.get("id") if answer.get("id") is not None else "-",
                    result["health"],
                    measure["down_mbps"], measure["up_mbps"], multi_note,
                    measure["latency_idle_ms"],
                    "未测" if measure["latency_loaded_ms"] is None else "%sms" % measure["latency_loaded_ms"],
                    measure["jitter_ms"], measure["loss_pct"],
                    result["verdict"],
                )
                print(line, flush=True)
                if out:
                    out.write(json.dumps({"id": answer.get("id"), "result": result, "measure": measure},
                                         ensure_ascii=False) + "\n")
                    out.flush()
            finished += 1
            if count and finished >= count:
                break
            elapsed = time.time() - started
            sleep(max(0.0, interval - elapsed))
    except KeyboardInterrupt:
        pass
    return rounds
