#!/usr/bin/env python3
"""执行无需平台账号的本地/CI质量检查，并保持成功日志紧凑。"""

import compileall
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_protocol_check():
    result = subprocess.run(
        [sys.executable, "main.py", "ai-help", "--summary"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    payload = json.loads(result.stdout)
    required = {"app_name", "app_version", "schema_version", "commands"}
    missing = sorted(required - set(payload))
    if missing:
        raise RuntimeError("AI协议缺少字段: " + ",".join(missing))


def _run_tests():
    suite = unittest.defaultTestLoader.discover(
        os.path.join(PROJECT_ROOT, "tests"),
        top_level_dir=PROJECT_ROOT,
    )
    report = io.StringIO()
    application_output = io.StringIO()
    with contextlib.redirect_stdout(application_output):
        result = unittest.TextTestRunner(
            stream=report, verbosity=1
        ).run(suite)
    print(report.getvalue().strip())
    if not result.wasSuccessful():
        print(application_output.getvalue())
        raise RuntimeError("测试失败")


def main():
    compiled = compileall.compile_dir(
        PROJECT_ROOT,
        quiet=1,
        rx=re.compile(r"/(?:\.cache|软著申请|\.git)/"),
    )
    if not compiled:
        raise RuntimeError("Python编译检查失败")
    _run_protocol_check()
    _run_tests()
    print("质量检查通过")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"质量检查失败: {error}", file=sys.stderr)
        raise SystemExit(1)
