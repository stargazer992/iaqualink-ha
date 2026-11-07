"""Spot where Jandy Bridge (local) and the iAquaLink cloud disagree."""

from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .cloud import RPM_SENSORS
from .local import LocalSensor, LocalSwitch, LocalThermostat

# The cloud refreshes every 5 min while local runs: a difference has to outlast
# a few of its polls (and any command still settling) before it counts.
MISMATCH_AFTER = timedelta(minutes=15)
RPM_TOLERANCE = 50


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _onoff(on: bool) -> str:
    return "on" if on else "off"


def differences(devices: dict[str, Any]) -> dict[str, str]:
    """Return {device name: description} for each local device the cloud disagrees with."""
    found: dict[str, str] = {}
    for name, dev in devices.items():
        cloud = getattr(dev, "fallback", None)
        if (
            cloud is None
            or dev.system.online is not True
            or getattr(cloud.system, "online", None) is not True
        ):
            continue
        if isinstance(dev, LocalThermostat):
            local_sp, cloud_sp = _num(dev.data.get("spvalue")), _num(cloud.state)
            if None not in (local_sp, cloud_sp) and local_sp != cloud_sp:
                found[name] = (
                    f"{dev.label} setpoint: Jandy Bridge {local_sp:g}, cloud {cloud_sp:g}"
                )
        elif isinstance(dev, LocalSwitch):
            if str(getattr(cloud, "state", "")) == "":
                continue
            local_on, cloud_on = dev.data.get("state") == "on", bool(cloud.is_on)
            if local_on != cloud_on:
                found[name] = (
                    f"{dev.label}: Jandy Bridge {_onoff(local_on)}, cloud {_onoff(cloud_on)}"
                )
        elif isinstance(dev, LocalSensor) and name in RPM_SENSORS.values():
            local_rpm = _num(dev.data.get("value"))
            cloud_rpm = _num(cloud.state)
            if (
                None not in (local_rpm, cloud_rpm)
                and abs(local_rpm - cloud_rpm) > RPM_TOLERANCE  # type: ignore[operator]
            ):
                found[name] = (
                    f"{dev.label}: Jandy Bridge {local_rpm:g}, cloud {cloud_rpm:g}"
                )
    return found


class MismatchTracker:
    """Keep the differences that have lasted MISMATCH_AFTER."""

    def __init__(self) -> None:
        """Start with nothing seen."""
        self.first_seen: dict[str, datetime] = {}
        self.current: dict[str, str] = {}

    def update(self, devices: dict[str, Any]) -> list[str]:
        """Compare again; return the lasting differences, sorted."""
        now = dt_util.utcnow()
        self.current = differences(devices)
        self.first_seen = {
            name: self.first_seen.get(name, now) for name in self.current
        }
        return sorted(
            text
            for name, text in self.current.items()
            if now - self.first_seen[name] >= MISMATCH_AFTER
        )
