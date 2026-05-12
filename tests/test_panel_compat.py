import base64
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def load_app_module():
    module_name = "cpa_codex_panel_app_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


app = load_app_module()


class ManagementSnapshotCompatibilityTests(unittest.TestCase):
    def test_snapshot_tolerates_missing_usage_and_latest_version_endpoints(self):
        client = app.ManagementClient("http://example.com", "secret", 12)

        responses = {
            "/get-auth-status": {"status": "ok"},
            "/auth-files": {
                "files": [
                    {
                        "name": "legacy_disabled.json",
                        "account": "legacy@example.com",
                        "disabled": True,
                        "auth_index": "auth-1",
                        "id_token": {"chatgpt_account_id": "chatgpt-1"},
                    }
                ]
            },
            "/config": {"usage-statistics-enabled": True, "auth-auto-refresh-workers": 0},
        }
        failures = {
            "/usage": RuntimeError("Management API /usage 返回 404: 404 page not found"),
            "/latest-version": RuntimeError("Management API /latest-version 返回 502: Bad Gateway"),
        }

        def fake_request(method, path, payload=None):
            if path in responses:
                return responses[path]
            if path in failures:
                raise failures[path]
            raise AssertionError(f"unexpected request: {method} {path} {payload}")

        client.request = fake_request

        snapshot = client.snapshot()

        self.assertTrue(snapshot["connected"])
        self.assertEqual(snapshot["auth_status"], "ok")
        self.assertEqual(snapshot["auth_files"][0]["name"], "legacy_disabled.json")
        self.assertEqual(snapshot["config"]["auth-auto-refresh-workers"], 0)
        self.assertEqual(snapshot["usage"], {})
        self.assertIsNone(snapshot["latest_version"])


class ManualDisabledRefreshCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.auth_dir = self.root / "auth"
        self.deactivated_dir = self.root / "deactivated"
        self.data_dir = self.root / "data"
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.deactivated_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def make_settings(self):
        return app.Settings(
            host="127.0.0.1",
            port=18660,
            admin_token="token",
            generated_token=False,
            session_ttl_minutes=420,
            cache_seconds=0,
            auth_dir=self.auth_dir,
            deactivated_dir=self.deactivated_dir,
            cliproxy_config_path=self.root / "config.yaml",
            meta_path=self.data_dir / "account_meta.json",
            session_store_path=self.data_dir / "sessions.json",
            management_base_url="http://127.0.0.1:18317",
            management_key="secret",
            management_timeout_seconds=12,
            manual_disabled_refresh_enabled=True,
            manual_disabled_refresh_interval_seconds=900,
            manual_disabled_refresh_batch_size=50,
            manual_disabled_refresh_startup_delay_seconds=0,
        )

    def write_account(self, name, email, *, disabled=True):
        payload = {
            "email": email,
            "disabled": disabled,
            "expired": "2099-05-02T08:33:39+00:00",
            "last_refresh": "2026-04-22T08:33:40+00:00",
            "refresh_token": "[REDACTED]",
        }
        (self.auth_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    def make_repo(self, remote_files):
        repo = app.AccountRepository(self.make_settings())
        repo._management_snapshot = lambda: {
            "connected": True,
            "base_url": "http://127.0.0.1:18317",
            "auth_status": "ok",
            "auth_files": remote_files,
            "config": {"usage-statistics-enabled": True},
            "usage": {},
            "latest_version": None,
        }
        return repo

    def test_falls_back_to_legacy_disabled_accounts_when_no_manual_tags_exist(self):
        self.write_account("legacy_disabled.json", "legacy@example.com", disabled=True)
        repo = self.make_repo(
            [
                {
                    "name": "legacy_disabled.json",
                    "account": "legacy@example.com",
                    "disabled": True,
                    "auth_index": "auth-1",
                    "id_token": {"chatgpt_account_id": "chatgpt-1"},
                }
            ]
        )

        candidates, manual_disabled_total, eligible_disabled_total, selection_mode = repo._manual_disabled_refresh_candidates(force=True, limit=0)

        self.assertEqual(selection_mode, "legacy_disabled_fallback")
        self.assertEqual(manual_disabled_total, 0)
        self.assertEqual(eligible_disabled_total, 1)
        self.assertEqual([item["email"] for item in candidates], ["legacy@example.com"])
        self.assertTrue(candidates[0]["quota_refresh_supported"])
        self.assertFalse(candidates[0]["manual_disabled"])

    def test_prefers_manual_disabled_tagged_accounts_when_present(self):
        self.write_account("tagged.json", "tagged@example.com", disabled=True)
        self.write_account("legacy.json", "legacy@example.com", disabled=True)
        meta_path = self.data_dir / "account_meta.json"
        meta_path.write_text(
            json.dumps(
                {
                    "accounts": {
                        "tagged@example.com": {
                            "manual_disabled": True,
                            "manual_disabled_at": "2026-05-05T00:00:00+00:00",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        repo = self.make_repo(
            [
                {
                    "name": "tagged.json",
                    "account": "tagged@example.com",
                    "disabled": True,
                    "auth_index": "auth-tagged",
                    "id_token": {"chatgpt_account_id": "chatgpt-tagged"},
                },
                {
                    "name": "legacy.json",
                    "account": "legacy@example.com",
                    "disabled": True,
                    "auth_index": "auth-legacy",
                    "id_token": {"chatgpt_account_id": "chatgpt-legacy"},
                },
            ]
        )

        candidates, manual_disabled_total, eligible_disabled_total, selection_mode = repo._manual_disabled_refresh_candidates(force=True, limit=0)

        self.assertEqual(selection_mode, "manual_tagged_only")
        self.assertEqual(manual_disabled_total, 1)
        self.assertEqual(eligible_disabled_total, 2)
        self.assertEqual([item["email"] for item in candidates], ["tagged@example.com"])
        self.assertTrue(candidates[0]["manual_disabled"])

    def test_refresh_manual_disabled_accounts_does_not_auto_enable_legacy_fallback_accounts(self):
        self.write_account("legacy_disabled.json", "legacy@example.com", disabled=True)
        repo = self.make_repo(
            [
                {
                    "name": "legacy_disabled.json",
                    "account": "legacy@example.com",
                    "disabled": True,
                    "auth_index": "auth-1",
                    "id_token": {"chatgpt_account_id": "chatgpt-1"},
                }
            ]
        )

        repo._refresh_codex_quota_for_account = lambda account: {"ok": True, "type": None, "message": "额度刷新成功"}
        toggled = []
        repo._set_account_status = lambda account, disabled: toggled.append((account["email"], disabled))

        result = repo.refresh_manual_disabled_accounts(limit=10)

        self.assertEqual(result["selection_mode"], "legacy_disabled_fallback")
        self.assertEqual(result["manual_disabled_total"], 0)
        self.assertEqual(result["eligible_disabled_total"], 1)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["recovered_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["skipped"][0]["reason"], "仅允许自动恢复带 manual_disabled 标记的账号")
        self.assertEqual(toggled, [])


class BatchTokenRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.auth_dir = self.root / "auth"
        self.deactivated_dir = self.root / "deactivated"
        self.data_dir = self.root / "data"
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.deactivated_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def make_settings(self):
        return app.Settings(
            host="127.0.0.1",
            port=18660,
            admin_token="token",
            generated_token=False,
            session_ttl_minutes=420,
            cache_seconds=0,
            auth_dir=self.auth_dir,
            deactivated_dir=self.deactivated_dir,
            cliproxy_config_path=self.root / "config.yaml",
            meta_path=self.data_dir / "account_meta.json",
            session_store_path=self.data_dir / "sessions.json",
            management_base_url="http://127.0.0.1:18317",
            management_key="secret",
            management_timeout_seconds=12,
            manual_disabled_refresh_enabled=True,
            manual_disabled_refresh_interval_seconds=900,
            manual_disabled_refresh_batch_size=50,
            manual_disabled_refresh_startup_delay_seconds=0,
        )

    @staticmethod
    def make_jwt(payload):
        def enc(obj):
            raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc(payload)}.sig"

    def make_repo(self, remote_files):
        repo = app.AccountRepository(self.make_settings())
        repo._management_snapshot = lambda: {
            "connected": True,
            "base_url": "http://127.0.0.1:18317",
            "auth_status": "ok",
            "auth_files": remote_files,
            "config": {"usage-statistics-enabled": True},
            "usage": {},
            "latest_version": None,
        }
        return repo

    def test_batch_refresh_tokens_updates_codex_auth_file_fields_like_cliproxyapi(self):
        account_path = self.auth_dir / "codex.json"
        account_path.write_text(
            json.dumps(
                {
                    "email": "old@example.com",
                    "disabled": False,
                    "expired": "2026-01-01T00:00:00+00:00",
                    "last_refresh": "2026-01-01T00:00:00+00:00",
                    "refresh_token": "rt-old",
                    "access_token": "at-old",
                    "id_token": "id-old",
                    "type": "codex",
                }
            ),
            encoding="utf-8",
        )
        repo = self.make_repo(
            [
                {
                    "name": "codex.json",
                    "account": "old@example.com",
                    "disabled": False,
                    "auth_index": "auth-1",
                    "id_token": {"chatgpt_account_id": "chatgpt-old"},
                }
            ]
        )
        refreshed_id_token = self.make_jwt(
            {
                "email": "new@example.com",
                "https://api.openai.com/auth": {"chatgpt_account_id": "chatgpt-new"},
            }
        )
        repo._request_codex_token_refresh = lambda refresh_token: {
            "access_token": "at-new",
            "refresh_token": "rt-new",
            "id_token": refreshed_id_token,
            "expires_in": 3600,
        }

        result = repo.batch_refresh_tokens(["old@example.com"])

        self.assertEqual(result["refreshed_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        updated = json.loads(account_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["access_token"], "at-new")
        self.assertEqual(updated["refresh_token"], "rt-new")
        self.assertEqual(updated["id_token"], refreshed_id_token)
        self.assertEqual(updated["account_id"], "chatgpt-new")
        self.assertEqual(updated["email"], "new@example.com")
        self.assertEqual(updated["type"], "codex")
        self.assertGreater(datetime.fromisoformat(updated["expired"]).astimezone(timezone.utc), datetime.now(timezone.utc))
        self.assertGreater(datetime.fromisoformat(updated["last_refresh"]).astimezone(timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_batch_refresh_tokens_disables_account_when_refresh_fails(self):
        account_path = self.auth_dir / "broken.json"
        account_path.write_text(
            json.dumps(
                {
                    "email": "broken@example.com",
                    "disabled": False,
                    "expired": "2026-01-01T00:00:00+00:00",
                    "last_refresh": "2026-01-01T00:00:00+00:00",
                    "refresh_token": "rt-broken",
                    "access_token": "at-old",
                    "type": "codex",
                }
            ),
            encoding="utf-8",
        )
        repo = self.make_repo(
            [
                {
                    "name": "broken.json",
                    "account": "broken@example.com",
                    "disabled": False,
                    "auth_index": "auth-broken",
                    "id_token": {"chatgpt_account_id": "chatgpt-broken"},
                }
            ]
        )
        repo._request_codex_token_refresh = lambda refresh_token: (_ for _ in ()).throw(RuntimeError("refresh_token_reused"))
        toggled = []
        repo._set_account_status = lambda account, disabled: toggled.append((account["email"], disabled))
        repo._set_manual_disabled_marker = lambda account, disabled: None
        repo._set_auto_recover_success_streak = lambda account, streak: None

        result = repo.batch_refresh_tokens(["broken@example.com"])

        self.assertEqual(result["refreshed_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["failed"][0]["type"], "refresh_failed")
        self.assertEqual(result["failed"][0]["auto_disabled"], True)
        self.assertEqual(toggled, [("broken@example.com", True)])


class ManualDisabledRefreshWorkerObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.auth_dir = self.root / "auth"
        self.deactivated_dir = self.root / "deactivated"
        self.data_dir = self.root / "data"
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.deactivated_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def make_settings(self):
        return app.Settings(
            host="127.0.0.1",
            port=18660,
            admin_token="token",
            generated_token=False,
            session_ttl_minutes=420,
            cache_seconds=0,
            auth_dir=self.auth_dir,
            deactivated_dir=self.deactivated_dir,
            cliproxy_config_path=self.root / "config.yaml",
            meta_path=self.data_dir / "account_meta.json",
            session_store_path=self.data_dir / "sessions.json",
            management_base_url="http://127.0.0.1:18317",
            management_key="secret",
            management_timeout_seconds=12,
            manual_disabled_refresh_enabled=True,
            manual_disabled_refresh_interval_seconds=900,
            manual_disabled_refresh_batch_size=50,
            manual_disabled_refresh_startup_delay_seconds=0,
        )

    def test_snapshot_includes_result_summary_and_blocked_type_breakdown(self):
        class FakeRepo:
            def refresh_manual_disabled_accounts(self, limit=0):
                raise AssertionError("should not be called")

        worker = app.ManualDisabledRefreshWorker(FakeRepo(), self.make_settings())
        worker._state["last_result"] = {
            "selection_mode": "legacy_disabled_fallback",
            "manual_disabled_total": 0,
            "eligible_disabled_total": 100,
            "selected_count": 50,
            "checked_count": 50,
            "recovered_count": 0,
            "limited_count": 1,
            "blocked_count": 3,
            "skipped_count": 2,
            "failed_count": 1,
            "blocked": [
                {"email": "alpha@example.com", "type": "token_expired", "message": "expired"},
                {"email": "beta@example.com", "type": "token_expired", "message": "expired"},
                {"email": "gamma@example.com", "type": "usage_limit_reached", "message": "limited"}
            ],
            "skipped": [
                {"email": "manual@example.com", "reason": "仅允许自动恢复带 manual_disabled 标记的账号"},
                {"email": "missing@example.com", "reason": "缺少 refresh token"}
            ],
            "failed": [
                {"email": "broken@example.com", "reason": "Management API timeout"}
            ],
            "trigger": "scheduled"
        }

        snapshot = worker.snapshot()

        self.assertIn("summary", snapshot)
        self.assertEqual(snapshot["summary"]["selection_mode"], "legacy_disabled_fallback")
        self.assertEqual(snapshot["summary"]["manual_disabled_total"], 0)
        self.assertEqual(snapshot["summary"]["eligible_disabled_total"], 100)
        self.assertEqual(snapshot["summary"]["selected_count"], 50)
        self.assertEqual(snapshot["summary"]["blocked_count"], 3)
        self.assertEqual(snapshot["summary"]["blocked_by_type"], {"token_expired": 2, "usage_limit_reached": 1})
        self.assertEqual(
            snapshot["summary"]["blocked_samples"],
            [
                {"email": "alpha@example.com", "type": "token_expired", "message": "expired"},
                {"email": "beta@example.com", "type": "token_expired", "message": "expired"},
                {"email": "gamma@example.com", "type": "usage_limit_reached", "message": "limited"},
            ],
        )
        self.assertEqual(
            snapshot["summary"]["skipped_samples"],
            [
                {"email": "manual@example.com", "reason": "仅允许自动恢复带 manual_disabled 标记的账号"},
                {"email": "missing@example.com", "reason": "缺少 refresh token"},
            ],
        )
        self.assertEqual(
            snapshot["summary"]["failed_samples"],
            [{"email": "broken@example.com", "reason": "Management API timeout"}],
        )

    def test_run_now_returns_updated_snapshot_with_manual_api_trigger(self):
        class FakeRepo:
            def __init__(self, result):
                self.result = result
                self.calls = []

            def refresh_manual_disabled_accounts(self, limit=0):
                self.calls.append(limit)
                return copy.deepcopy(self.result)

        repo = FakeRepo(
            {
                "requested_count": 1,
                "eligible_count": 1,
                "checked_count": 1,
                "recovered_count": 0,
                "limited_count": 0,
                "blocked_count": 1,
                "skipped_count": 0,
                "failed_count": 0,
                "recovered": [],
                "limited": [],
                "blocked": [
                    {
                        "email": "legacy@example.com",
                        "type": "token_expired",
                        "message": "Provided authentication token is expired."
                    }
                ],
                "skipped": [],
                "failed": [],
                "manual_disabled_total": 0,
                "eligible_disabled_total": 1,
                "selected_count": 1,
                "batch_size_limit": 50,
                "selection_mode": "legacy_disabled_fallback",
                "auto_recover_success_threshold": 2
            }
        )
        worker = app.ManualDisabledRefreshWorker(repo, self.make_settings())

        snapshot = worker.run_now(trigger="manual_api")

        self.assertEqual(repo.calls, [50])
        self.assertEqual(snapshot["last_result"]["trigger"], "manual_api")
        self.assertEqual(snapshot["summary"]["blocked_count"], 1)
        self.assertEqual(snapshot["summary"]["blocked_by_type"], {"token_expired": 1})


class ManualDisabledRefreshHandlerTests(unittest.TestCase):
    def test_handler_exposes_manual_disabled_refresh_run_endpoint(self):
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn('if parsed.path == "/api/disabled-refresh/run":', source)
        self.assertIn('snapshot = worker.run_now(trigger="manual_api")', source)

    def test_handler_exposes_latest_result_export_endpoint(self):
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn('if parsed.path == "/api/disabled-refresh/latest-result":', source)
        self.assertIn('content_type="application/json"', source)
        self.assertIn('filename="disabled-refresh-latest-result.json"', source)


class AccountListPayloadCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.auth_dir = self.root / "auth"
        self.deactivated_dir = self.root / "deactivated"
        self.data_dir = self.root / "data"
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.deactivated_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def make_settings(self):
        return app.Settings(
            host="127.0.0.1",
            port=18660,
            admin_token="token",
            generated_token=False,
            session_ttl_minutes=420,
            cache_seconds=0,
            auth_dir=self.auth_dir,
            deactivated_dir=self.deactivated_dir,
            cliproxy_config_path=self.root / "config.yaml",
            meta_path=self.data_dir / "account_meta.json",
            session_store_path=self.data_dir / "sessions.json",
            management_base_url="",
            management_key="",
            management_timeout_seconds=12,
            manual_disabled_refresh_enabled=True,
            manual_disabled_refresh_interval_seconds=900,
            manual_disabled_refresh_batch_size=50,
            manual_disabled_refresh_startup_delay_seconds=0,
        )

    def test_accounts_list_response_is_trimmed_for_table_view(self):
        payload = {
            "email": "trim@example.com",
            "name": "Trim",
            "account_id": "acc-trim",
            "chatgpt_account_id": "chatgpt-trim",
            "disabled": False,
            "expired": "2099-05-02T08:33:39+00:00",
            "last_refresh": "2026-04-22T08:33:40+00:00",
            "refresh_token": "***",
            "plan_type": "pro",
        }
        (self.auth_dir / "trim.json").write_text(json.dumps(payload), encoding="utf-8")
        repo = app.AccountRepository(self.make_settings())

        result = repo.accounts(page=1, page_size=50)
        item = result["items"][0]

        self.assertEqual(item["email"], "trim@example.com")
        self.assertIn("quota_refresh_supported", item)
        self.assertIn("starred", item)
        self.assertNotIn("account_id", item)
        self.assertNotIn("auth_index", item)
        self.assertNotIn("chatgpt_account_id", item)
        self.assertNotIn("request_count", item)
        self.assertNotIn("failure_count", item)
        self.assertNotIn("total_tokens", item)
        self.assertNotIn("last_request_at", item)
        self.assertNotIn("models", item)
        self.assertNotIn("source_file", item)
        self.assertNotIn("file_display_name", item)
        self.assertNotIn("owner", item)
        self.assertNotIn("note", item)
        self.assertNotIn("updated_at", item)


class PanelConfigSplitTests(unittest.TestCase):
    def test_app_supports_runtime_data_root_and_derived_storage_paths(self):
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn('data_dir=Path(os.getenv("CPA_PANEL_DATA_DIR", str(DATA_DIR)))', source)
        self.assertIn('meta_path=Path(os.getenv("CPA_PANEL_META_PATH", str(data_dir / "account_meta.json")))', source)
        self.assertIn('session_store_path=Path(os.getenv("CPA_PANEL_SESSION_STORE_PATH", str(data_dir / "sessions.json")))', source)

    def test_env_example_groups_panel_cpa_data_backup_settings(self):
        env_example = (APP_PATH.parent / ".env.example").read_text(encoding="utf-8")
        self.assertIn('# 面板自身访问配置', env_example)
        self.assertIn('# 上游 CPA / CLIProxyAPI 依赖配置', env_example)
        self.assertIn('# 面板运行数据存储', env_example)
        self.assertIn('CPA_PANEL_DATA_DIR=', env_example)
        self.assertIn('CPA_PANEL_SESSION_STORE_PATH=', env_example)
        self.assertIn('# disabled refresh 自动任务', env_example)
        self.assertIn('# 备份任务配置', env_example)

    def test_repo_has_publish_ready_scaffolding_files(self):
        root = APP_PATH.parent
        self.assertTrue((root / '.gitignore').exists(), 'missing .gitignore')
        self.assertTrue((root / 'requirements.txt').exists(), 'missing requirements.txt')
        self.assertTrue((root / 'deploy' / 'systemd' / 'cpa-codex-panel.service.example').exists(), 'missing systemd example')
        self.assertTrue((root / 'deploy' / 'systemd' / 'cpa-file-backup.service.example').exists(), 'missing backup service example')
        self.assertTrue((root / 'deploy' / 'systemd' / 'cpa-file-backup.timer.example').exists(), 'missing backup timer example')

    def test_readme_describes_panel_as_cpa_dependent_sibling_project(self):
        readme = (APP_PATH.parent / 'README.md').read_text(encoding='utf-8')
        self.assertIn('它依赖底层 CPA / CLIProxyAPI 服务', readme)
        self.assertIn('上游依赖配置', readme)
        self.assertIn('面板自身配置', readme)
        self.assertIn('运行数据与备份', readme)
        self.assertIn('CPA_PANEL_DATA_DIR', readme)

    def test_runtime_supports_external_env_file_override(self):
        app_source = APP_PATH.read_text(encoding='utf-8')
        backup_source = (APP_PATH.parent / 'backup_service_data.py').read_text(encoding='utf-8')
        self.assertIn('CPA_PANEL_ENV_FILE', app_source)
        self.assertIn('CPA_BACKUP_ENV_FILE', backup_source)


class DisabledRefreshUiWiringTests(unittest.TestCase):
    def test_index_and_js_include_disabled_refresh_panel_and_run_button(self):
        index_source = (APP_PATH.parent / "static" / "index.html").read_text(encoding="utf-8")
        js_source = (APP_PATH.parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="disabledRefreshCard"', index_source)
        self.assertIn('id="runDisabledRefreshBtn"', index_source)
        self.assertIn('id="downloadDisabledRefreshResultBtn"', index_source)
        self.assertIn('id="disabledRefreshMeta"', index_source)
        self.assertIn('id="disabledRefreshDetails"', index_source)
        self.assertIn('const disabledRefreshCard = document.getElementById("disabledRefreshCard");', js_source)
        self.assertIn('const runDisabledRefreshBtn = document.getElementById("runDisabledRefreshBtn");', js_source)
        self.assertIn('const downloadDisabledRefreshResultBtn = document.getElementById("downloadDisabledRefreshResultBtn");', js_source)
        self.assertIn('await apiFetch("/api/disabled-refresh/run"', js_source)
        self.assertIn('window.open("/api/disabled-refresh/latest-result", "_blank")', js_source)
        self.assertIn('function renderDisabledRefresh()', js_source)

    def test_js_renders_recent_blocked_skipped_failed_samples(self):
        js_source = (APP_PATH.parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('summary.blocked_samples', js_source)
        self.assertIn('summary.skipped_samples', js_source)
        self.assertIn('summary.failed_samples', js_source)
        self.assertIn('最近 blocked 样本', js_source)
        self.assertIn('最近 skipped 样本', js_source)
        self.assertIn('最近 failed 样本', js_source)

    def test_index_and_js_trim_unused_columns_and_use_grouped_labels(self):
        index_source = (APP_PATH.parent / "static" / "index.html").read_text(encoding="utf-8")
        js_source = (APP_PATH.parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("<th>账号</th>", index_source)
        self.assertNotIn("<th>请求数</th>", index_source)
        self.assertNotIn("<th>总 Token</th>", index_source)
        self.assertNotIn("<th>最近请求</th>", index_source)
        self.assertNotIn("<th>数据源</th>", index_source)
        self.assertNotIn("<th>星标</th>", index_source)
        self.assertIn("手动禁用标记", js_source)
        self.assertIn("最近 blocked", js_source)
        self.assertIn("最近恢复", js_source)
        self.assertIn("基础信息", js_source)
        self.assertIn("生命周期", js_source)
        self.assertIn("运营信息", js_source)
        self.assertNotIn('["管理侧更新时间"', js_source)
        self.assertNotIn('["额度更新时间"', js_source)

    def test_account_rows_keep_multiline_content_inside_inner_wrapper_not_td(self):
        js_source = (APP_PATH.parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('<td class="cell-with-subtag">', js_source)
        self.assertIn('<div class="cell-with-subtag">', js_source)
        self.assertIn('item.group || "未分组"', js_source)
        self.assertIn('tagsText || "无标签"', js_source)

    def test_index_and_js_use_compact_header_tabs_inline_summary_and_row_actions(self):
        index_source = (APP_PATH.parent / "static" / "index.html").read_text(encoding="utf-8")
        js_source = (APP_PATH.parent / "static" / "app.js").read_text(encoding="utf-8")
        css_source = (APP_PATH.parent / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn('id="summaryBar"', index_source)
        self.assertNotIn('id="summaryGrid"', index_source)
        self.assertIn('id="scanMeta"', index_source)
        self.assertIn('class="page-header-main page-header-main-inline"', index_source)
        self.assertIn('id="accountListTab"', index_source)
        self.assertIn('id="disabledRefreshTab"', index_source)
        self.assertIn('id="accountListPanel"', index_source)
        self.assertIn('id="disabledRefreshPanel"', index_source)
        self.assertNotIn('CLIProxyAPI 上层控制台', index_source)
        self.assertNotIn('<aside class="detail-card">', index_source)
        self.assertIn('activeTab: "accounts"', js_source)
        self.assertIn('function setActiveTab(tab)', js_source)
        self.assertIn('scanMeta.textContent = `${lastScanText} · ${durationText} · ${interfaceStatus}`;', js_source)
        self.assertNotIn('summaryBar.innerHTML =', js_source)
        self.assertNotIn('总请求数', js_source)
        self.assertNotIn('总 Token', js_source)
        self.assertIn('row-action-btn row-action-settings', js_source)
        self.assertIn('row-action-btn row-action-refresh', js_source)
        self.assertIn('row-action-btn row-action-toggle', js_source)
        self.assertIn('row-action-btn row-action-delete', js_source)
        self.assertIn('.page-header-main-inline {', css_source)
        self.assertIn('.scan-meta-line {', css_source)


if __name__ == "__main__":
    unittest.main()
