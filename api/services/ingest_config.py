"""Ingest configuration service for Sprint 11.1.

Manages ingest scanner settings stored in the database config table.
Provides typed access to ingest configuration values and ensures defaults
are set on application startup.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from api.models.ingest import IngestConfig, IngestConfigUpdate
from api.services.database import get_config, set_config

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration Keys (stored in database config table)
# =============================================================================

# Prefix for all ingest-related config keys
INGEST_PREFIX = "ingest."

# Individual config keys
KEY_ENABLED = f"{INGEST_PREFIX}enabled"
KEY_SCAN_INTERVAL_HOURS = f"{INGEST_PREFIX}scan_interval_hours"
KEY_SCAN_TIME = f"{INGEST_PREFIX}scan_time"
KEY_LAST_SCAN_AT = f"{INGEST_PREFIX}last_scan_at"
KEY_LAST_SCAN_SUCCESS = f"{INGEST_PREFIX}last_scan_success"
KEY_SERVER_URL = f"{INGEST_PREFIX}server_url"
KEY_DIRECTORIES = f"{INGEST_PREFIX}directories"
KEY_IGNORE_DIRECTORIES = f"{INGEST_PREFIX}ignore_directories"


# =============================================================================
# Default Values
# =============================================================================

DEFAULT_CONFIG = IngestConfig(
    enabled=True,
    scan_interval_hours=24,
    scan_time="07:00",  # 7 AM daily scan
    last_scan_at=None,
    last_scan_success=None,
    server_url="https://mmingest.pbswi.wisc.edu/",
    # Scan from root to auto-discover all directories (IWP, SCC2SRT, misc, etc.)
    directories=["/"],
    ignore_directories=["/promos/"],
)


# =============================================================================
# Configuration Access Functions
# =============================================================================


async def get_ingest_config() -> IngestConfig:
    """Get the current ingest scanner configuration.

    Reads all ingest-related config values from the database and returns
    them as a typed IngestConfig object. Missing values use defaults.

    Returns:
        IngestConfig: Current configuration with defaults for missing values
    """
    # Fetch each config value (returns None if not set)
    enabled_item = await get_config(KEY_ENABLED)
    interval_item = await get_config(KEY_SCAN_INTERVAL_HOURS)
    time_item = await get_config(KEY_SCAN_TIME)
    last_scan_item = await get_config(KEY_LAST_SCAN_AT)
    last_success_item = await get_config(KEY_LAST_SCAN_SUCCESS)
    server_url_item = await get_config(KEY_SERVER_URL)
    directories_item = await get_config(KEY_DIRECTORIES)
    ignore_dirs_item = await get_config(KEY_IGNORE_DIRECTORIES)

    # Parse values with defaults
    enabled = enabled_item.get_typed_value() if enabled_item else DEFAULT_CONFIG.enabled

    scan_interval_hours = interval_item.get_typed_value() if interval_item else DEFAULT_CONFIG.scan_interval_hours

    scan_time = time_item.value if time_item else DEFAULT_CONFIG.scan_time

    last_scan_at = None
    if last_scan_item and last_scan_item.value:
        try:
            last_scan_at = datetime.fromisoformat(last_scan_item.value)
        except ValueError:
            logger.warning(f"Invalid last_scan_at value: {last_scan_item.value}")

    last_scan_success = None
    if last_success_item:
        last_scan_success = last_success_item.get_typed_value()

    server_url = server_url_item.value if server_url_item else DEFAULT_CONFIG.server_url

    directories = DEFAULT_CONFIG.directories
    if directories_item:
        try:
            directories = json.loads(directories_item.value)
        except json.JSONDecodeError:
            logger.warning(f"Invalid directories JSON: {directories_item.value}")

    ignore_directories = DEFAULT_CONFIG.ignore_directories
    if ignore_dirs_item:
        try:
            ignore_directories = json.loads(ignore_dirs_item.value)
        except json.JSONDecodeError:
            logger.warning(f"Invalid ignore_directories JSON: {ignore_dirs_item.value}")

    return IngestConfig(
        enabled=enabled,
        scan_interval_hours=scan_interval_hours,
        scan_time=scan_time,
        last_scan_at=last_scan_at,
        last_scan_success=last_scan_success,
        server_url=server_url,
        directories=directories,
        ignore_directories=ignore_directories,
    )


async def update_ingest_config(updates: IngestConfigUpdate) -> IngestConfig:
    """Update ingest configuration with provided values.

    Only updates fields that are explicitly provided (not None).

    Args:
        updates: IngestConfigUpdate with optional field values

    Returns:
        IngestConfig: The updated configuration
    """
    if updates.enabled is not None:
        await set_config(
            KEY_ENABLED,
            str(updates.enabled).lower(),
            value_type="bool",
            description="Whether scheduled scanning is enabled",
        )

    if updates.scan_interval_hours is not None:
        await set_config(
            KEY_SCAN_INTERVAL_HOURS,
            str(updates.scan_interval_hours),
            value_type="int",
            description="Hours between scheduled scans",
        )

    if updates.scan_time is not None:
        await set_config(
            KEY_SCAN_TIME,
            updates.scan_time,
            value_type="string",
            description="Time of day to run scheduled scan (HH:MM)",
        )

    return await get_ingest_config()


async def record_scan_result(success: bool) -> None:
    """Record the result of a scan operation.

    Updates last_scan_at timestamp and last_scan_success flag.

    Args:
        success: Whether the scan completed successfully
    """
    now = datetime.utcnow()

    await set_config(KEY_LAST_SCAN_AT, now.isoformat(), value_type="string", description="Timestamp of last scan")

    await set_config(
        KEY_LAST_SCAN_SUCCESS, str(success).lower(), value_type="bool", description="Whether last scan succeeded"
    )


async def ensure_defaults() -> None:
    """Ensure default configuration values exist in the database.

    Called during application startup to initialize config if not present.
    Does not overwrite existing values.
    """
    # Check if any ingest config exists
    existing = await get_config(KEY_ENABLED)
    if existing is not None:
        logger.debug("Ingest config already initialized")
        return

    logger.info("Initializing default ingest configuration")

    # Set all default values
    await set_config(
        KEY_ENABLED,
        str(DEFAULT_CONFIG.enabled).lower(),
        value_type="bool",
        description="Whether scheduled scanning is enabled",
    )

    await set_config(
        KEY_SCAN_INTERVAL_HOURS,
        str(DEFAULT_CONFIG.scan_interval_hours),
        value_type="int",
        description="Hours between scheduled scans",
    )

    await set_config(
        KEY_SCAN_TIME,
        DEFAULT_CONFIG.scan_time,
        value_type="string",
        description="Time of day to run scheduled scan (HH:MM format)",
    )

    await set_config(
        KEY_SERVER_URL,
        DEFAULT_CONFIG.server_url,
        value_type="string",
        description="Base URL of the PBS Wisconsin ingest server",
    )

    await set_config(
        KEY_DIRECTORIES,
        json.dumps(DEFAULT_CONFIG.directories),
        value_type="json",
        description="Directories to scan on ingest server",
    )

    await set_config(
        KEY_IGNORE_DIRECTORIES,
        json.dumps(DEFAULT_CONFIG.ignore_directories),
        value_type="json",
        description="Directories to ignore when scanning",
    )

    logger.info("Ingest configuration defaults initialized")


# =============================================================================
# Scheduler Integration Helpers
# =============================================================================


def parse_scan_time(scan_time: str) -> tuple[int, int]:
    """Parse scan time string into hour and minute.

    Args:
        scan_time: Time string in HH:MM format

    Returns:
        Tuple of (hour, minute)

    Raises:
        ValueError: If format is invalid
    """
    parts = scan_time.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid scan time format: {scan_time}")

    hour = int(parts[0])
    minute = int(parts[1])

    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ValueError(f"Invalid scan time values: {scan_time}")

    return hour, minute


async def get_next_scan_time() -> Optional[datetime]:
    """Calculate when the next scheduled scan should occur.

    Returns:
        datetime: Next scheduled scan time, or None if scanning is disabled
    """
    config = await get_ingest_config()

    if not config.enabled:
        return None

    hour, minute = parse_scan_time(config.scan_time)
    now = datetime.utcnow()

    # Calculate next occurrence
    next_scan = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If that time has passed today, schedule for tomorrow
    if next_scan <= now:
        from datetime import timedelta

        next_scan += timedelta(days=1)

    return next_scan
