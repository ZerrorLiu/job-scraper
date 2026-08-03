"""Stable extension contracts for adapters and application services."""

from job_scraper.ports.channels import JobChannel
from job_scraper.ports.processors import PipelineStep
from job_scraper.ports.repositories import JobRepository
from job_scraper.ports.sinks import JobSink
from job_scraper.ports.sources import JobSource, SourceCapabilities

__all__ = [
    "JobChannel",
    "JobRepository",
    "JobSink",
    "JobSource",
    "PipelineStep",
    "SourceCapabilities",
]
