from job_scraper.collectors.data_integration_adapter import IndeedBrightDataCollector
from job_scraper.ports.sources import SourceCapabilities


class BrightDataIndeedSource(IndeedBrightDataCollector):
    """Indeed mapper backed by the shared Bright Data transport."""

    capabilities = SourceCapabilities(
        acquisition_mode="managed_dataset",
        platform="indeed",
        supports_pagination=True,
        supports_upstream_freshness=True,
        requires_credentials=True,
        is_metered=True,
    )
