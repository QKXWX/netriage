import json
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from netriage import __version__  # noqa: E402


def run(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "netriage", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30, env=env,
    )


def test_version_command():
    result = run("version")
    assert result.returncode == 0
    assert "netriage " + __version__ in result.stdout


def test_diagnose_example_json():
    result = run("diagnose", "examples/slow-gigabit.json", "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["causes"][0]["id"] == "provisioning"
    assert 0 <= payload["health"] <= 100


def test_diagnose_example_with_ticket():
    result = run("diagnose", "examples/slow-gigabit.json", "--ticket")
    assert result.returncode == 0
    assert "宽带故障报障单" in result.stdout


def test_history_command_empty_db(tmp_path):
    import os
    env = dict(os.environ, NETRIAGE_DB=str(tmp_path / "cli.db"))
    result = run("history", env=env)
    assert result.returncode == 0
    assert "还没有历史存档" in result.stdout


def test_diagnose_html_report(tmp_path):
    out = tmp_path / "report.html"
    result = run("diagnose", "examples/slow-gigabit.json", "--html", str(out))
    assert result.returncode == 0
    text = out.read_text(encoding="utf-8")
    assert "宽带质量诊断报告" in text
    assert "报障工单" in text
