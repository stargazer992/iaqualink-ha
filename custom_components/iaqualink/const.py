"""Constants for the iaqualink component."""

from datetime import timedelta

DOMAIN = "iaqualink"

UPDATE_INTERVAL_BY_SYSTEM_TYPE: dict[str, timedelta] = {
    "iaqua": timedelta(seconds=15),
    "exo": timedelta(seconds=60),
    "aqualinkd": timedelta(seconds=5),
}
UPDATE_INTERVAL_DEFAULT = timedelta(seconds=30)

CONF_CONNECTION = "connection"
CONF_AQUALINKD_URL = "aqualinkd_url"
CONNECTION_CLOUD = "cloud"
CONNECTION_LOCAL = "local"
