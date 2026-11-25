from netriage.diagnose import diagnose
from netriage.report import render_report
from netriage.samples import demo_payload


def test_report_contains_core_sections():
    payload = demo_payload()
    html = render_report(payload, diagnose(payload))
    assert "宽带质量诊断报告" in html
    assert "套餐与实测" in html
    assert "检查表" in html
    assert "根因判断" in html
    assert "宽带故障报障单" in html
    assert "带宽未达标" in html or "未达标" in html


def test_report_is_self_contained():
    html = render_report(demo_payload(), diagnose(demo_payload()))
    assert "<style>" in html
    assert "http" not in html.split("<body>")[0] or "stylesheet" not in html
    # 不允许外部脚本 / 外链样式
    assert "<script" not in html
    assert 'rel="stylesheet"' not in html


def test_report_escapes_user_input():
    payload = demo_payload()
    payload["address"] = '"><script>alert(1)</script>'
    payload["contact"] = "<img src=x onerror=alert(1)>"
    html = render_report(payload, diagnose(payload))
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html


def test_report_shows_health_and_checks():
    payload = demo_payload()
    result = diagnose(payload)
    html = render_report(payload, result)
    assert str(result["health"]) in html
    for item in result["checks"][:3]:
        assert item["name"] in html
