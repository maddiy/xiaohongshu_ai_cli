import argparse
import contextlib
import html
import io
import json
import os
import subprocess
import tempfile
import unittest
import stat
from unittest.mock import MagicMock, patch

import main
from config import (
    AI_SCHEMA_VERSION,
    APP_VERSION,
    CACHE_DIR,
    PROJECT_ROOT,
    XHS_CLI_VERSION,
)
from lib.replier import Replier
from lib.scanner import CommentScanner
from lib.analyzer import _classify_sentiment
from lib.state_io import file_lock, StateLockTimeout
from lib.state_db import StateDB
from lib.xhs_client import XHSClient
from lib import cli_ai
from lib.cli_admin import _release_consistency
from lib.cli_release import _git_worktree_state
from lib.cli_parser import (
    COMMAND_EFFECTS,
    COMMAND_NAMES,
    build_command_contract,
    build_parser,
)
from lib.cli_support import (
    build_comment_display_groups,
    save_comment_archive,
    TERMINAL_SEND_STATUSES,
)


def _valid_review(fact_verdict="not_applicable", sources=None):
    return {
        "logic": {
            "verdict": "partly_sound",
            "reason": "观点有可讨论部分，但论据不足",
        },
        "fact_check": {
            "verdict": fact_verdict,
            "reason": "没有独立可核查的外部事实主张",
            "sources": list(sources or []),
        },
        "boast_check": {
            "verdict": "none",
            "reason": "未发现自我夸大或无法核实的成就陈述",
        },
    }

__all__ = [name for name in globals() if not name.startswith("__")]
