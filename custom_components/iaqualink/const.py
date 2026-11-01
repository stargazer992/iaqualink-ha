"""Constants for the iaqualink component."""

from datetime import timedelta

DOMAIN = "iaqualink"

UPDATE_INTERVAL_BY_SYSTEM_TYPE: dict[str, timedelta] = {
    "iaqua": timedelta(seconds=15),
    "exo": timedelta(seconds=60),
    "aqualinkd": timedelta(seconds=5),
}
UPDATE_INTERVAL_DEFAULT = timedelta(seconds=30)
# AqualinkD pushes changes over its websocket; poll only as a safety net then.
UPDATE_INTERVAL_AQUALINKD_PUSH = timedelta(seconds=60)
# The cloud is only a fallback once AqualinkD drives every light zone.
UPDATE_INTERVAL_CLOUD_IDLE = timedelta(minutes=5)
AQUALINKD_RETRY_SECONDS = 60
AQUALINKD_ISSUE_FAILURES = 3

CONF_CONNECTION = "connection"
CONF_AQUALINKD_URL = "aqualinkd_url"
CONNECTION_CLOUD = "cloud"
CONNECTION_LOCAL = "local"
