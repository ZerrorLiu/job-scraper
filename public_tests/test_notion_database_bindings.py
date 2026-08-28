from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from job_scraper.adapters.sinks.notion_daily import NotionDailySink
from job_scraper.adapters.sinks.notion_workflow import configured_data_source_ids
from job_scraper.adapters.storage.notion_bindings import (
    NotionDatabaseBinding,
    NotionDatabaseBindingStore,
)
from job_scraper.cli.bootstrap import BootstrapRequest, initialize_profile
from job_scraper.cli.database import show_status
from job_scraper.config import NotionConfig
from job_scraper.integrations.notion import NotionClient, NotionNotFoundError
from job_scraper.ports.sinks import PublishContext
from job_scraper.storage.db import Database

_BOUND_DATABASE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_RECOVERED_DATABASE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_CONFIGURED_DATABASE_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _database(database_id: str, data_source_id: str, title: str = "Fictional Jobs") -> dict:
    return {
        "id": database_id,
        "title": [{"plain_text": title}],
        "data_sources": [{"id": data_source_id}],
    }


def test_binding_store_keeps_a_separate_entry_per_profile(tmp_path: Path) -> None:
    store = NotionDatabaseBindingStore(tmp_path / "data" / "notion_database_bindings.json")

    assert store.load("profile_one") is None
    assert not store.path.exists()

    store.save("profile_one", NotionDatabaseBinding("database-one", "source-one"))
    store.save("profile_two", NotionDatabaseBinding("database-two", "source-two"))

    assert store.load("profile_one") == NotionDatabaseBinding("database-one", "source-one")
    assert store.load("profile_two") == NotionDatabaseBinding("database-two", "source-two")
    assert not list(store.path.parent.glob("*.tmp"))


def test_concurrent_profile_saves_preserve_every_binding(tmp_path: Path) -> None:
    path = tmp_path / "data" / "notion_database_bindings.json"
    barrier = Barrier(4)

    def save(index: int) -> None:
        barrier.wait()
        NotionDatabaseBindingStore(path).save(
            f"profile_{index}",
            NotionDatabaseBinding(f"database-{index}", f"source-{index}"),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(save, range(4)))

    store = NotionDatabaseBindingStore(path)
    assert [store.load(f"profile_{index}") for index in range(4)] == [
        NotionDatabaseBinding(f"database-{index}", f"source-{index}") for index in range(4)
    ]


class _Repository:
    def get_job_history(self, *_args: object) -> object:
        raise AssertionError("no jobs should query history")

    def upsert_notion_state(self, *_args: object) -> None:
        raise AssertionError("no jobs should write state")


class _BoundClient:
    config = SimpleNamespace(database_id="", parent_page_id="")

    def __init__(self) -> None:
        self.bound_database_ids: list[str] = []

    def enabled(self) -> bool:
        return True

    def ensure_daily_database(self, _title: str, **kwargs: object) -> dict:
        self.bound_database_ids.append(str(kwargs["bound_database_id"]))
        return _database(_BOUND_DATABASE_ID, "fictional-source")

    def ensure_job_views(self, *_args: object) -> None:
        return None

    def get_data_source_property_types(self, _data_source_id: str) -> dict[str, str]:
        return {}

    def list_data_source_pages(self, _data_source_id: str) -> list[dict]:
        return []


def test_sink_uses_the_profile_binding_and_persists_the_resolved_ids(tmp_path: Path) -> None:
    store = NotionDatabaseBindingStore(tmp_path / "notion_database_bindings.json")
    store.save("profile_one", NotionDatabaseBinding(_BOUND_DATABASE_ID, "old-source"))
    client = _BoundClient()
    sink = NotionDailySink(
        _Repository(),  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        timezone_name="UTC",
        table_prefix="Fictional",
        track_label="Fictional",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        binding_store=store,
        profile_id="profile_one",
    )

    result = sink.publish([], PublishContext(run_id="fictional-run", profile_id="profile_one"))

    assert result.errors == ()
    assert client.bound_database_ids == [_BOUND_DATABASE_ID]
    # A valid database ID remains authoritative; a stale displayed data-source
    # value alone does not cause local state to be rewritten on every run.
    assert store.load("profile_one") == NotionDatabaseBinding(_BOUND_DATABASE_ID, "old-source")


def test_missing_bound_database_recovers_by_title_without_a_write(monkeypatch) -> None:
    client = NotionClient(
        NotionConfig(
            enabled=True,
            token="fictional-internal-token",
            parent_page_id=_BOUND_DATABASE_ID,
        )
    )
    calls: list[tuple[str, str]] = []

    def request(endpoint: str, method: str, body: object = None) -> dict:
        del body
        calls.append((method, endpoint))
        if endpoint.endswith(_BOUND_DATABASE_ID) and method == "GET":
            raise NotionNotFoundError("Notion API 404: fictional")
        if "/children" in endpoint:
            return {
                "results": [
                    {
                        "id": _RECOVERED_DATABASE_ID,
                        "type": "child_database",
                        "child_database": {"title": "Fictional Jobs"},
                    }
                ],
                "has_more": False,
            }
        if endpoint.endswith(_RECOVERED_DATABASE_ID) and method == "GET":
            return _database(_RECOVERED_DATABASE_ID, "recovered-source")
        raise AssertionError(f"unexpected request: {method} {endpoint}")

    monkeypatch.setattr(client, "request", request)

    database = client.find_daily_database(
        "Fictional Jobs",
        bound_database_id=_BOUND_DATABASE_ID,
        parent_page_id=_BOUND_DATABASE_ID,
        is_inline=False,
    )

    assert database == _database(_RECOVERED_DATABASE_ID, "recovered-source")
    assert all(method == "GET" for method, _endpoint in calls)


def test_stale_binding_recovers_by_title_before_an_explicit_database_id(monkeypatch) -> None:
    client = NotionClient(
        NotionConfig(
            enabled=True,
            token="fictional-internal-token",
            database_id=_CONFIGURED_DATABASE_ID,
            parent_page_id=_BOUND_DATABASE_ID,
        )
    )

    def request(endpoint: str, method: str, body: object = None) -> dict:
        del body
        if endpoint.endswith(_BOUND_DATABASE_ID) and method == "GET":
            raise NotionNotFoundError("Notion API 404: fictional")
        if endpoint.endswith(_CONFIGURED_DATABASE_ID):
            raise AssertionError("configured database must not override 404 recovery")
        if "/children" in endpoint:
            return {
                "results": [
                    {
                        "id": _RECOVERED_DATABASE_ID,
                        "type": "child_database",
                        "child_database": {"title": "Fictional Jobs"},
                    }
                ],
                "has_more": False,
            }
        if endpoint.endswith(_RECOVERED_DATABASE_ID) and method == "GET":
            return _database(_RECOVERED_DATABASE_ID, "recovered-source")
        raise AssertionError(f"unexpected request: {method} {endpoint}")

    monkeypatch.setattr(client, "request", request)

    assert client.find_daily_database(
        "Fictional Jobs",
        bound_database_id=_BOUND_DATABASE_ID,
        parent_page_id=_BOUND_DATABASE_ID,
        is_inline=False,
    ) == _database(_RECOVERED_DATABASE_ID, "recovered-source")


def test_status_import_uses_the_stored_database_id_without_title_enumeration(
    tmp_path: Path,
) -> None:
    store = NotionDatabaseBindingStore(tmp_path / "notion_database_bindings.json")
    store.save("profile_one", NotionDatabaseBinding(_BOUND_DATABASE_ID, "old-source"))
    calls: list[dict[str, object]] = []

    class Client:
        config = SimpleNamespace(parent_page_id="fictional-parent")

        def find_daily_database(self, _title: str, **kwargs: object) -> dict:
            calls.append(dict(kwargs))
            return _database(_BOUND_DATABASE_ID, "fictional-source")

    assert configured_data_source_ids(
        Client(),  # type: ignore[arg-type]
        table_title="Fictional Jobs",
        binding_store=store,
        profile_id="profile_one",
    ) == ["fictional-source"]
    assert calls == [
        {
            "bound_database_id": _BOUND_DATABASE_ID,
            "parent_page_id": "fictional-parent",
            "is_inline": False,
        }
    ]
    assert store.load("profile_one") == NotionDatabaseBinding(_BOUND_DATABASE_ID, "old-source")


def test_status_import_replaces_a_binding_only_when_resolution_changes(tmp_path: Path) -> None:
    store = NotionDatabaseBindingStore(tmp_path / "notion_database_bindings.json")
    store.save("profile_one", NotionDatabaseBinding(_BOUND_DATABASE_ID, "old-source"))

    class Client:
        config = SimpleNamespace(parent_page_id="fictional-parent")

        def find_daily_database(self, _title: str, **_kwargs: object) -> dict:
            return _database(_RECOVERED_DATABASE_ID, "recovered-source")

    assert configured_data_source_ids(
        Client(),  # type: ignore[arg-type]
        table_title="Fictional Jobs",
        binding_store=store,
        profile_id="profile_one",
    ) == ["recovered-source"]
    assert store.load("profile_one") == NotionDatabaseBinding(
        _RECOVERED_DATABASE_ID,
        "recovered-source",
    )


def test_bound_database_title_is_updated_from_the_configured_title(monkeypatch) -> None:
    client = NotionClient(NotionConfig(enabled=True, token="fictional-internal-token"))
    updates: list[dict] = []

    def request(endpoint: str, method: str, body: dict | None = None) -> dict:
        if method == "GET":
            return _database(_BOUND_DATABASE_ID, "fictional-source", "Renamed by an operator")
        if method == "PATCH":
            updates.append(body or {})
            return {}
        raise AssertionError(f"unexpected request: {method} {endpoint}")

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(client, "sync_daily_schema", lambda _data_source_id: {})

    client.ensure_daily_database("Fictional Jobs", bound_database_id=_BOUND_DATABASE_ID)

    assert updates == [{"title": [{"type": "text", "text": {"content": "Fictional Jobs"}}]}]


def test_db_status_reports_a_binding_without_creating_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_root = tmp_path / "private-workspace"
    initialize_profile(
        BootstrapRequest(
            config_root=config_root,
            profile_id="profile_one",
            label="Profile One",
            queries=("Role One",),
            locations=("Region One",),
            countries=("US",),
            keywords=("signal",),
            sources=("linkedin_direct",),
            sinks=("csv",),
        )
    )
    monkeypatch.setenv("JOB_SCRAPER_CONFIG_DIR", str(config_root))
    workspace_path = config_root / "data" / "workspace.db"
    Database(config_root / "data" / "profile_one.db").initialize()
    from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase

    WorkspaceDatabase(workspace_path).initialize()
    store = NotionDatabaseBindingStore(config_root / "data" / "notion_database_bindings.json")

    assert show_status(workspace_path, ["profile_one"]) == 0
    assert "notion binding profile_one: unbound" in capsys.readouterr().out
    assert not store.path.exists()

    store.save("profile_one", NotionDatabaseBinding("database-one", "source-one"))

    assert show_status(workspace_path, ["profile_one"]) == 0

    output = capsys.readouterr().out
    assert (
        "notion binding profile_one: database_id=database-one, data_source_id=source-one" in output
    )

    assert show_status(config_root / "data" / "missing.db", ["profile_one"]) == 1
    missing_output = capsys.readouterr().out
    assert "Workspace database does not exist" in missing_output
    assert (
        "notion binding profile_one: database_id=database-one, data_source_id=source-one"
        in missing_output
    )


def test_a_held_destination_is_retried_rather_than_lost(monkeypatch, tmp_path: Path) -> None:
    """A transient holder must not cost a binding write.

    On Windows any open handle on the destination -- including one a scanner
    takes on a file the moment it is created -- fails `os.replace`. Our own
    code holds neither file by then, so the obstruction is external and brief,
    and the move is idempotent.
    """
    from job_scraper.adapters.storage import notion_bindings

    path = tmp_path / "data" / "notion_database_bindings.json"
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(notion_bindings.os, "replace", flaky_replace)
    monkeypatch.setattr(notion_bindings, "REPLACE_BACKOFF_SECONDS", 0)

    NotionDatabaseBindingStore(path).save("fictional", NotionDatabaseBinding("db-1", "src-1"))

    assert calls["n"] == 3
    assert NotionDatabaseBindingStore(path).load("fictional") == NotionDatabaseBinding(
        "db-1", "src-1"
    )


def test_a_holder_that_outlasts_the_backoff_still_raises(monkeypatch, tmp_path: Path) -> None:
    """A permission error that survives every attempt is a real one."""
    from job_scraper.adapters.storage import notion_bindings

    def always_denied(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(notion_bindings.os, "replace", always_denied)
    monkeypatch.setattr(notion_bindings, "REPLACE_BACKOFF_SECONDS", 0)

    with pytest.raises(PermissionError):
        NotionDatabaseBindingStore(tmp_path / "b.json").save(
            "fictional", NotionDatabaseBinding("db-1", "src-1")
        )


def test_an_unwritable_binding_does_not_cost_the_publish(tmp_path: Path) -> None:
    """The binding is a cache; a whole track's postings are not.

    Recording it runs *before* any row is written, so letting the failure
    propagate discarded everything the run had acquired in order to protect a
    shortcut. The warning names what the lost cache costs -- the next run
    resolves by table title, which is the path that can create a second table
    if a title or prefix changed.
    """

    class _UnwritableStore(NotionDatabaseBindingStore):
        def save(self, profile_id: str, binding: NotionDatabaseBinding) -> None:
            raise PermissionError(5, "Access is denied")

    logged: list[str] = []
    sink = NotionDailySink(
        _Repository(),  # type: ignore[arg-type]
        _BoundClient(),  # type: ignore[arg-type]
        timezone_name="UTC",
        table_prefix="Fictional",
        track_label="Fictional",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        binding_store=_UnwritableStore(tmp_path / "notion_database_bindings.json"),
        profile_id="profile_one",
        logger=logged.append,
    )

    result = sink.publish([], PublishContext(run_id="fictional-run", profile_id="profile_one"))

    assert result.errors == ()
    warning = next(line for line in logged if "Warning" in line)
    assert "Could not record the database binding" in warning
    assert "resolves this table by title" in warning
