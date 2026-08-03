"""版本、文档、公开身份和Git发布状态检查。"""

import ast
import os
import re
import subprocess


_FIXED_AUTHOR_ID_PATTERN = re.compile(
    r"(?i)(?:AUTHOR_USER_ID|author_user_id|user_id)"
    r"[^\n]{0,80}?[\"']([0-9a-f]{24})[\"']"
)


def _extract_release_values(config_source):
    app_match = re.search(
        r'^APP_VERSION\s*=\s*[\"\']([^\"\']+)[\"\']',
        config_source,
        flags=re.MULTILINE,
    )
    schema_match = re.search(
        r'^AI_SCHEMA_VERSION\s*=\s*[\"\']([^\"\']+)[\"\']',
        config_source,
        flags=re.MULTILINE,
    )
    return (
        app_match.group(1) if app_match else "",
        schema_match.group(1) if schema_match else "",
    )


def _git_output(project_root, *arguments):
    """执行只读Git查询；没有仓库或命令失败时返回空字符串。"""
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    # porcelain首列中的前导空格是Git状态码的一部分，不能使用strip()。
    return result.stdout.rstrip() if result.returncode == 0 else ""


def _git_worktree_state(project_root):
    """返回工作区和本地跟踪分支状态，避免把脏工作区误报为已发布。"""
    porcelain = _git_output(
        project_root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    dirty_entries = [line for line in porcelain.splitlines() if line]
    upstream = _git_output(
        project_root,
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
    )
    ahead = 0
    behind = 0
    if upstream:
        counts = _git_output(
            project_root,
            "rev-list", "--left-right", "--count", "@{upstream}...HEAD",
        ).split()
        if len(counts) == 2 and all(item.isdigit() for item in counts):
            behind, ahead = map(int, counts)
    return {
        "dirty": bool(dirty_entries),
        "dirty_count": len(dirty_entries),
        "dirty_paths": [line[3:] for line in dirty_entries[:50]],
        "dirty_paths_truncated": len(dirty_entries) > 50,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
    }
def _release_consistency(project_root):
    """检查当前文档、公开账号信息和Git发布快照是否一致。"""
    from config import AI_SCHEMA_VERSION, APP_VERSION, XHS_CLI_VERSION

    expectations = {
        "README.md": (
            f"# 小红书AI智能运营系统 v{APP_VERSION}",
            f"schema_version: {AI_SCHEMA_VERSION}",
        ),
        "AGENTS.md": (
            f"发布版本为`{APP_VERSION}`",
            f"schema`{AI_SCHEMA_VERSION}`",
        ),
        "SKILL.md": (
            f"发布为`{APP_VERSION}`",
            f"schema为`{AI_SCHEMA_VERSION}`",
        ),
        "references/commands.md": (
            f"应用版本为 `{APP_VERSION}`",
            f"协议版本为`{AI_SCHEMA_VERSION}`",
        ),
        "pyproject.toml": (
            f'version = "{APP_VERSION}"',
            f'"xiaohongshu-cli=={XHS_CLI_VERSION}"',
        ),
        "requirements.txt": (
            f"xiaohongshu-cli=={XHS_CLI_VERSION}",
        ),
    }
    version_errors = []
    identity_findings = []
    public_files = []
    for relative_path in expectations:
        absolute = os.path.join(project_root, relative_path)
        try:
            with open(absolute, encoding="utf-8") as file:
                source = file.read()
        except OSError as error:
            version_errors.append(f"{relative_path}: {error}")
            continue
        public_files.append((relative_path, source))
        for expected in expectations[relative_path]:
            if expected not in source:
                version_errors.append(
                    f"{relative_path}缺少当前声明: {expected}"
                )

    source_candidates = ["config.py", "main.py"]
    lib_dir = os.path.join(project_root, "lib")
    if os.path.isdir(lib_dir):
        source_candidates.extend(
            f"lib/{name}" for name in sorted(os.listdir(lib_dir))
            if name.endswith(".py")
        )
    tests_dir = os.path.join(project_root, "tests")
    if os.path.isdir(tests_dir):
        source_candidates.extend(
            f"tests/{name}" for name in sorted(os.listdir(tests_dir))
            if name.endswith(".py")
            and os.path.isfile(os.path.join(tests_dir, name))
        )
    for relative_path in source_candidates:
        absolute = os.path.join(project_root, relative_path)
        try:
            with open(absolute, encoding="utf-8") as file:
                source = file.read()
        except OSError:
            continue
        public_files.append((relative_path, source))
    for relative_path, source in public_files:
        for match in _FIXED_AUTHOR_ID_PATTERN.finditer(source):
            identity_findings.append({
                "file": relative_path,
                "line": source.count("\n", 0, match.start()) + 1,
                "rule": "固定24位账号ID",
            })
    config_source = next(
        (source for path, source in public_files if path == "config.py"), ""
    )
    if re.search(r"^AUTHOR_USER_ID\s*=", config_source, re.MULTILINE):
        identity_findings.append({
            "file": "config.py",
            "line": 0,
            "rule": "公开配置包含AUTHOR_USER_ID赋值",
        })

    repository = {
        "available": False,
        "ok": True,
        "status": "not_git_checkout",
    }
    if os.path.isdir(os.path.join(project_root, ".git")):
        head_config = _git_output(project_root, "show", "HEAD:config.py")
        if head_config:
            head_app, head_schema = _extract_release_values(head_config)
            head_has_fixed_id = bool(
                re.search(r"^AUTHOR_USER_ID\s*=", head_config, re.MULTILINE)
                or _FIXED_AUTHOR_ID_PATTERN.search(head_config)
            )
            worktree = _git_worktree_state(project_root)
            head_ok = (
                head_app == APP_VERSION
                and head_schema == AI_SCHEMA_VERSION
                and not head_has_fixed_id
            )
            if not head_ok:
                status = "working_tree_not_published"
                next_step = "提交当前版本和隐私修复后再发布"
            elif worktree["dirty"]:
                status = "working_tree_dirty"
                next_step = "检查、提交并推送当前工作区修改"
            elif worktree["ahead"]:
                status = "commits_not_pushed"
                next_step = "推送当前分支中尚未发布的提交"
            elif worktree["behind"]:
                status = "upstream_out_of_sync"
                next_step = "检查并同步本地分支与上游分支"
            elif worktree["upstream"]:
                status = "synced"
                next_step = ""
            else:
                status = "local_head_verified"
                next_step = "未配置上游分支，无法断言远端已经同步"
            repository = {
                "available": True,
                "ok": head_ok and not worktree["dirty"]
                and not worktree["ahead"] and not worktree["behind"],
                "status": status,
                "head_app_version": head_app,
                "head_schema_version": head_schema,
                "head_contains_fixed_author_id": head_has_fixed_id,
                "working_app_version": APP_VERSION,
                "working_schema_version": AI_SCHEMA_VERSION,
                **worktree,
                "next": next_step,
            }
    return {
        "versions": {
            "ok": not version_errors,
            "app_version": APP_VERSION,
            "schema_version": AI_SCHEMA_VERSION,
            "errors": version_errors,
        },
        "public_identity": {
            "ok": not identity_findings,
            "findings": identity_findings,
            "identity_source": "xhs whoami --json → 本地SQLite metadata",
        },
        "repository": repository,
    }


def _build_test_inventory(project_root, test_files):
    """从当前测试源码提取unittest方法；只描述当前快照，不推断历史变化。"""
    tests = []
    for relative_path in test_files:
        absolute_path = os.path.join(project_root, relative_path)
        try:
            with open(absolute_path, encoding="utf-8") as file:
                tree = ast.parse(file.read(), filename=relative_path)
        except (OSError, SyntaxError):
            continue
        module_name = os.path.splitext(os.path.basename(relative_path))[0]
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name.startswith("test_")
                ):
                    tests.append(f"{module_name}.{node.name}.{item.name}")
    return {
        "count": len(tests),
        "files": test_files,
        "tests": tests,
        "scope": "当前工作区快照中的测试方法",
        "history_rule": (
            "此清单不能证明哪些测试是新增、删除或修改；比较历史必须有"
            "明确的旧版本快照或版本控制差异"
        ),
    }
