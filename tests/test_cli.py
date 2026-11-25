import json
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "netriage", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
    )


def test_version_command():
    result = run("version")
    assert result.returncode == 0
    assert "netriage 0.1.0" in result.stdout


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
