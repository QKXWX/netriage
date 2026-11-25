#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内置样例：用于 demo 和测试，模拟一条「千兆套餐只跑出三分之一」的典型故障。"""

DIAL_DEMO = """Nov 25 19:02:11 OpenWrt daemon.notice pppd[2841]: Connect: pppoe-wan <--> eth0
Nov 25 19:02:11 OpenWrt daemon.info pppd[2841]: sent [PADI Host-Uniq 0x0b2a]
Nov 25 19:02:12 OpenWrt daemon.info pppd[2841]: rcvd [PADO]
Nov 25 19:02:12 OpenWrt daemon.notice pppd[2841]: local  IP address 100.64.18.77
Nov 25 20:41:07 OpenWrt daemon.notice pppd[2841]: Modem hangup
Nov 25 20:41:07 OpenWrt daemon.notice pppd[2841]: Connection terminated.
Nov 25 20:41:09 OpenWrt daemon.info pppd[2841]: sent [PADI Host-Uniq 0x0b2a]
Nov 25 20:41:14 OpenWrt daemon.err pppd[2841]: LCP: timeout sending Config-Requests
Nov 25 21:15:02 OpenWrt daemon.notice pppd[2841]: Modem hangup
"""


def demo_payload():
    return {
        "plan": {"down": 1000, "up": 100, "type": "FTTH"},
        "measure": {
            "down_mbps": 318.4,
            "up_mbps": 61.2,
            "down_single_mbps": 88.6,
            "down_multi_mbps": 318.4,
            "latency_idle_ms": 18.4,
            "latency_loaded_ms": 322.0,
            "jitter_ms": 9.2,
            "loss_pct": 0.8,
        },
        "wifi": {"rssi": -71, "snr": 19, "retry_pct": 12, "channel_util": 58, "band": "5G", "tx_rate": 780},
        "lqc": "RSRP=-96 RSRQ=-11 SINR=17 band=5G rx_power=-24.6",
        "dial_log": DIAL_DEMO,
        "account": "0791****8823",
        "address": "呼和浩特市新城区某小区 3 号楼",
        "isp": "中国电信",
        "contact": "王先生",
    }
