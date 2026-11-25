#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""趋势判定：把「一列历史数字」翻译成一句能拿去和运营商对质的话。

只做可复现的统计，不猜：
- 晚高峰（默认 19–23 点）与白天（9–18 点）的下行中位数对比；
- 最近 3 次与更早基线对比，判断是否「最近才开始掉」；
- 整体是否长期低于签约带宽；
- 丢包与 bufferbloat 是否持续异常。

输入是 HistoryStore.list() 的摘要行（倒序或正序都行），输出固定结构，
方便 API、CLI 和前端复用。
"""

import datetime

EVENING_HOURS = (19, 23)
DAY_HOURS = (9, 18)


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _median(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _hour(created_at):
    if not created_at:
        return None
    text = str(created_at).replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(text).hour
    except ValueError:
        return None


def _round(value, digits=1):
    return None if value is None else round(value, digits)


def analyze(runs, plan_down=None, evening=EVENING_HOURS, day=DAY_HOURS):
    """返回 {verdict, headline, evidence[], stats{...}, hourly[...]}。"""
    rows = [row for row in (runs or []) if isinstance(row, dict)]
    rows.sort(key=lambda row: (row.get("created_at") or "", row.get("id") or 0))
    down = [_num(row.get("down_mbps")) for row in rows]
    valid_down = [value for value in down if value is not None]

    plan = _num(plan_down) or _median([_num(row.get("plan_down")) for row in rows])
    stats = {
        "count": len(rows),
        "first_at": rows[0].get("created_at") if rows else None,
        "last_at": rows[-1].get("created_at") if rows else None,
        "plan_down": plan,
        "down_median": _median(valid_down),
        "down_last": down[-1] if down else None,
        "health_last": _num(rows[-1].get("health")) if rows else None,
    }

    evidence = []
    verdict = "insufficient"
    headline = "还没有足够的存档，至少测 3 次才能看出来趋势"

    if len(rows) >= 3 and valid_down:
        stats["down_median"] = _median(valid_down)

        # 最近 3 次 vs 更早基线
        recent = valid_down[-3:]
        earlier = valid_down[:-3]
        stats["down_recent3"] = _median(recent)
        stats["down_baseline"] = _median(earlier) if earlier else None
        if earlier and stats["down_baseline"]:
            change = (stats["down_recent3"] - stats["down_baseline"]) / stats["down_baseline"] * 100
            stats["recent_change_pct"] = _round(change)
            if change <= -15:
                verdict = "recent_drop"
                headline = "最近 3 次下行比之前低了 %.0f%%，像是最近才开始变慢" % abs(change)
                evidence.append("最近 3 次下行中位数 %.1f Mbps，之前基线 %.1f Mbps，降幅 %.0f%%。" % (
                    stats["down_recent3"], stats["down_baseline"], abs(change)))

        # 晚高峰 vs 白天
        evening_down, day_down, other_down = [], [], []
        for row in rows:
            hour = _hour(row.get("created_at"))
            value = _num(row.get("down_mbps"))
            if hour is None or value is None:
                continue
            if evening[0] <= hour <= evening[1]:
                evening_down.append(value)
            elif day[0] <= hour <= day[1]:
                day_down.append(value)
            else:
                other_down.append(value)
        stats["evening_samples"] = len(evening_down)
        stats["day_samples"] = len(day_down)
        stats["evening_down_median"] = _median(evening_down)
        stats["day_down_median"] = _median(day_down)
        if len(evening_down) >= 2 and len(day_down) >= 2 and stats["day_down_median"]:
            ratio = stats["evening_down_median"] / stats["day_down_median"]
            stats["evening_day_ratio"] = _round(ratio, 2)
            if ratio <= 0.75:
                verdict = "peak_degradation"
                headline = "晚高峰下行只有白天的 %.0f%%，更像上联拥塞而不是你家设备的问题" % (ratio * 100)
                evidence.append("晚高峰 %d 次下行中位数 %.1f Mbps，白天 %d 次 %.1f Mbps，前者只有后者的 %.0f%%。" % (
                    len(evening_down), stats["evening_down_median"], len(day_down),
                    stats["day_down_median"], ratio * 100))

        # 长期不达标
        if plan and stats["down_median"] is not None:
            ratio_plan = stats["down_median"] / plan
            stats["plan_ratio"] = _round(ratio_plan, 2)
            if ratio_plan < 0.8 and verdict == "insufficient":
                verdict = "sustained_shortfall"
                headline = "整体下行只有签约带宽的 %.0f%%，不是偶发，是长期没跑满" % (ratio_plan * 100)
                evidence.append("%d 次下行中位数 %.1f Mbps，签约 %.1f Mbps，达标率 %.0f%%。" % (
                    len(valid_down), stats["down_median"], plan, ratio_plan * 100))

        # bufferbloat
        bloats = [_num(row.get("bufferbloat_ms")) for row in rows[-5:]]
        stats["bufferbloat_median"] = _median(bloats)
        if stats["bufferbloat_median"] is not None and stats["bufferbloat_median"] >= 150 \
                and verdict in ("insufficient", "stable"):
            verdict = "bufferbloat"
            headline = "满载延迟中位数 %.0f ms，队列在堆积，游戏和视频会先卡" % stats["bufferbloat_median"]
            evidence.append("最近 %d 次满载延迟增量中位数 %.1f ms（阈值 150 ms）。" % (
                len([value for value in bloats if value is not None]), stats["bufferbloat_median"]))

        # 丢包
        losses = [_num(row.get("loss_pct")) for row in rows[-5:]]
        stats["loss_median_recent5"] = _median(losses)
        if stats["loss_median_recent5"] is not None and stats["loss_median_recent5"] >= 2 \
                and verdict in ("insufficient", "stable"):
            verdict = "packet_loss"
            headline = "最近 5 次丢包中位数 %.1f%%，链路本身在丢包" % stats["loss_median_recent5"]
            evidence.append("最近 5 次探测丢包中位数 %.1f%%（阈值 2%%）。" % stats["loss_median_recent5"])

        if verdict == "insufficient":
            verdict = "stable"
            headline = "最近 %d 次没有明显劣化，问题更像偶发或体感误差" % len(rows)
            evidence.append("下行中位数 %.1f Mbps，晚高峰/白天比 %.2f。" % (
                stats["down_median"] or 0, stats.get("evening_day_ratio") or 1))

    hourly = []
    for hour in range(24):
        values = []
        for row in rows:
            if _hour(row.get("created_at")) == hour:
                value = _num(row.get("down_mbps"))
                if value is not None:
                    values.append(value)
        hourly.append({"hour": hour, "count": len(values), "down_median": _median(values)})

    return {
        "verdict": verdict,
        "headline": headline,
        "evidence": evidence,
        "stats": stats,
        "hourly": hourly,
    }
