"""版本、隐私、SQLite和账号身份测试。"""

from tests.support import *


class ConfigStateTests(unittest.TestCase):
    def test_public_versions_and_runtime_versions_match(self):
        with open(
            os.path.join(PROJECT_ROOT, "README.md"), encoding="utf-8"
        ) as file:
            readme = file.read()
        with open(
            os.path.join(PROJECT_ROOT, "references", "commands.md"),
            encoding="utf-8",
        ) as file:
            commands = file.read()
        self.assertIn(f"v{APP_VERSION}", readme)
        self.assertIn(f"schema_version: {AI_SCHEMA_VERSION}", readme)
        self.assertIn(f"`{APP_VERSION}`", commands)
        self.assertIn(f"`{AI_SCHEMA_VERSION}`", commands)

    def test_public_config_contains_no_fixed_author_id(self):
        import config

        self.assertFalse(hasattr(config, "AUTHOR_USER_ID"))

    def test_dependency_manifests_pin_verified_xhs_cli_version(self):
        expected = f"xiaohongshu-cli=={XHS_CLI_VERSION}"
        for relative_path in ("requirements.txt", "pyproject.toml"):
            with open(
                os.path.join(PROJECT_ROOT, relative_path), encoding="utf-8"
            ) as file:
                self.assertIn(expected, file.read())
        with open(
            os.path.join(PROJECT_ROOT, "pyproject.toml"), encoding="utf-8"
        ) as file:
            self.assertIn(f'version = "{APP_VERSION}"', file.read())

    def test_release_consistency_checks_versions_and_identity(self):
        report = _release_consistency(PROJECT_ROOT)
        self.assertTrue(report["versions"]["ok"])
        self.assertEqual(report["versions"]["app_version"], APP_VERSION)
        self.assertEqual(
            report["versions"]["schema_version"], AI_SCHEMA_VERSION
        )
        self.assertTrue(report["public_identity"]["ok"])
        self.assertIn("status", report["repository"])

    @patch("lib.cli_release._git_output")
    def test_git_worktree_state_reports_dirty_and_unpushed_changes(
        self, git_output
    ):
        git_output.side_effect = [
            " M README.md\n?? tests/new_test.py",
            "origin/main",
            "0 2",
        ]
        state = _git_worktree_state(PROJECT_ROOT)
        self.assertTrue(state["dirty"])
        self.assertEqual(state["dirty_count"], 2)
        self.assertEqual(state["dirty_paths"], [
            "README.md", "tests/new_test.py",
        ])
        self.assertEqual(state["upstream"], "origin/main")
        self.assertEqual(state["ahead"], 2)
        self.assertEqual(state["behind"], 0)

    def test_sqlite_state_round_trip_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.sqlite3")
            database = StateDB(path)
            database.put_document("workflow/n1/scan", {"count": 2})
            database.set_setting("author_user_id", "author-user")
            self.assertEqual(
                database.get_document("workflow/n1/scan"), {"count": 2}
            )
            self.assertEqual(
                database.get_setting("author_user_id"), "author-user"
            )
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_program_state_repairs_divergent_snapshot_from_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "drafts.json")
            database = StateDB(os.path.join(temp_dir, "state.sqlite3"))
            canonical = {
                "drafts": [{
                    "comment_id": "c1",
                    "review": {"logic": {"verdict": "sound", "reason": "成立"}},
                }],
            }
            with patch("lib.state_io.state_db", return_value=database), patch(
                "lib.state_io.state_key_for_path",
                return_value="workflows/n1/drafts.json",
            ):
                atomic_write_json(canonical, path, indent=2)
                with open(path, "w", encoding="utf-8") as file:
                    json.dump({"drafts": []}, file)
                loaded = read_workflow_state(path, role="program_state")
                self.assertEqual(loaded, canonical)
                self.assertEqual(
                    cli_ai.preview_hash(loaded["drafts"]),
                    cli_ai.preview_hash(canonical["drafts"]),
                )
                with open(path, encoding="utf-8") as file:
                    self.assertEqual(json.load(file), canonical)

    def test_ai_input_snapshot_updates_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "reply_map.json")
            database = StateDB(os.path.join(temp_dir, "state.sqlite3"))
            database.put_document("workflows/n1/reply_map.json", {"old": 1})
            with open(path, "w", encoding="utf-8") as file:
                json.dump({"new": 2}, file)
            with patch("lib.state_io.state_db", return_value=database), patch(
                "lib.state_io.state_key_for_path",
                return_value="workflows/n1/reply_map.json",
            ):
                loaded = read_workflow_state(path, role="ai_input")
            self.assertEqual(loaded, {"new": 2})
            self.assertEqual(
                database.get_document("workflows/n1/reply_map.json"),
                {"new": 2},
            )

    def test_sqlite_migrates_legacy_json_without_overwriting_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy = os.path.join(temp_dir, "comments.json")
            with open(legacy, "w", encoding="utf-8") as file:
                json.dump({"comments": 3}, file)
            database = StateDB(os.path.join(temp_dir, "state.sqlite3"))
            first = database.migrate_legacy_json(temp_dir)
            self.assertEqual(first["imported"], 1)
            self.assertEqual(first["deleted"], 1)
            self.assertFalse(os.path.exists(legacy))
            self.assertEqual(
                database.get_document("comments.json"), {"comments": 3}
            )
            with open(legacy, "w", encoding="utf-8") as file:
                json.dump({"comments": 99}, file)
            second = database.migrate_legacy_json(temp_dir)
            self.assertEqual(second["skipped"], 1)
            self.assertEqual(second["deleted"], 0)
            self.assertTrue(os.path.exists(legacy))
            self.assertEqual(
                database.get_document("comments.json"), {"comments": 3}
            )

    def test_sqlite_migration_preserves_current_compatibility_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = StateDB(os.path.join(temp_dir, "state.sqlite3"))
            database.put_document("comments.json", {"comments": 3})
            snapshot = os.path.join(temp_dir, "comments.json")
            with open(snapshot, "w", encoding="utf-8") as file:
                json.dump({"comments": 3}, file)
            result = database.migrate_legacy_json(temp_dir)
            self.assertEqual(result["imported"], 0)
            self.assertEqual(result["deleted"], 0)
            self.assertEqual(result["skipped"], 1)
            self.assertTrue(os.path.exists(snapshot))

    def test_author_identity_is_discovered_once_and_saved(self):
        database = MagicMock()
        database.get_setting.return_value = ""
        XHSClient._author_user_id_cache = ""
        payload = {
            "ok": True,
            "data": {"user": {"id": "author-user"}},
        }
        with patch("lib.xhs_client.state_db", return_value=database), patch(
            "lib.xhs_client.XHSClient._run_xhs", return_value=payload
        ) as run_xhs:
            self.assertEqual(
                XHSClient.get_author_user_id(), "author-user"
            )
            self.assertEqual(
                XHSClient.get_author_user_id(), "author-user"
            )
        run_xhs.assert_called_once_with(
            ["xhs", "whoami", "--json"], timeout=30
        )
        database.set_setting.assert_called_once_with(
            "author_user_id", "author-user"
        )
        XHSClient._author_user_id_cache = ""

    def test_author_identity_accepts_private_environment_override(self):
        database = MagicMock()
        XHSClient._author_user_id_cache = ""
        with patch.dict(
            os.environ, {"XHS_AUTHOR_USER_ID": "environment-author"}
        ), patch(
            "lib.xhs_client.state_db", return_value=database
        ), patch("lib.xhs_client.XHSClient._run_xhs") as run_xhs:
            self.assertEqual(
                XHSClient.get_author_user_id(), "environment-author"
            )
        run_xhs.assert_not_called()
        database.set_setting.assert_called_once_with(
            "author_user_id", "environment-author"
        )
        XHSClient._author_user_id_cache = ""
