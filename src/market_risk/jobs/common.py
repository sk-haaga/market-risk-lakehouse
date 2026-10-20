"""Helpers shared by the job entry points."""

from __future__ import annotations

import logging
from datetime import UTC, datetime


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    return logging.getLogger(name)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0, tzinfo=None)


def get_spark():
    """Return the active Spark session (Databricks) or create a local one."""
    from pyspark.sql import SparkSession

    return SparkSession.builder.getOrCreate()


def fqn(catalog: str | None, schema: str | None, table: str) -> str:
    """Fully qualified table name; unqualified when catalog/schema are not given (local runs)."""
    parts = [p for p in (catalog, schema) if p]
    return ".".join([*parts, table])


def volume_path(catalog: str, schema: str, volume: str) -> str:
    return f"/Volumes/{catalog}/{schema}/{volume}"
