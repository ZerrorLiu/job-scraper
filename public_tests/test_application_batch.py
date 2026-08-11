from pathlib import Path

import pytest

from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase


def test_application_batch_limits_are_bounded(tmp_path: Path) -> None:
    database = WorkspaceDatabase(tmp_path / "workspace.db")

    with pytest.raises(ValueError, match="between 1 and 20"):
        database.get_accepted_application_jobs(limit=21)

    with pytest.raises(ValueError, match="between 1 and 20"):
        database.get_accepted_application_jobs(limit=0)

    with pytest.raises(ValueError, match="must not be negative"):
        database.get_accepted_application_jobs(offset=-1)
