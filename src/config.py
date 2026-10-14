from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml


class ConfigError(Exception):
    pass


@dataclass
class PingTarget:
    name: str
    host: str


@dataclass
class TcpTarget:
    name: str
    host: str
    port: int
    timeout: int = 5000  # ms


@dataclass
class SuccessIndicator:
    type: str  # url_contains | element_exists | text_contains
    value: str


@dataclass
class DashboardTarget:
    name: str
    url: str
    username: str
    password: str
    success_indicator: SuccessIndicator
    timeout: int = 15000  # ms


@dataclass
class HttpTarget:
    name: str
    url: str
    expected_text: str = ""   # DOWN if missing from the response body
    forbidden_text: str = ""  # DOWN if present in the response body
    timeout: int = 10000  # ms


Target = PingTarget | TcpTarget | DashboardTarget | HttpTarget


def target_host(t: Target) -> str:
    """Address shown under the target name in the web UI."""
    if isinstance(t, (DashboardTarget, HttpTarget)):
        return t.url
    if isinstance(t, TcpTarget):
        return f"{t.host}:{t.port}"
    return t.host


@dataclass
class StorageConfig:
    retention_days: int = 30
    screenshots_dir: str = "screenshots"


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class ChannelConfig:
    type: str
    raw: dict = field(default_factory=dict)


@dataclass
class AlertsConfig:
    channels: list[ChannelConfig] = field(default_factory=list)


@dataclass
class AppConfig:
    check_interval: int
    ping_targets: list[PingTarget]
    tcp_targets: list[TcpTarget] = field(default_factory=list)
    dashboard_targets: list[DashboardTarget] = field(default_factory=list)
    http_targets: list[HttpTarget] = field(default_factory=list)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    web: WebConfig = field(default_factory=WebConfig)
    # Re-check a DOWN target before recording it, to ride out momentary blips
    retries: int = 2
    dashboard_retries: int = 1  # dashboard checks are slow (browser login)
    retry_delay: int = 10  # seconds between attempts


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var = m.group(1)
            if var not in os.environ:
                raise ConfigError(f"Environment variable '{var}' is not set")
            return os.environ[var]
        return re.sub(r"\$\{([^}]+)\}", _replace, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(item) for item in value]
    return value


def load_config(path: str) -> AppConfig:
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in config: {e}")

    raw = _interpolate(raw)

    check_interval = raw.get("check_interval", 60)

    storage_raw = raw.get("storage", {})
    storage = StorageConfig(
        retention_days=storage_raw.get("retention_days", 30),
        screenshots_dir=storage_raw.get("screenshots_dir", "screenshots"),
    )

    web_raw = raw.get("web", {})
    web = WebConfig(
        host=web_raw.get("host", "0.0.0.0"),
        port=web_raw.get("port", 8000),
    )

    targets_raw = raw.get("targets", {})

    ping_targets = [
        PingTarget(name=t["name"], host=t["host"])
        for t in targets_raw.get("ping", [])
    ]

    tcp_targets = [
        TcpTarget(
            name=t["name"],
            host=t["host"],
            port=int(t["port"]),
            timeout=t.get("timeout", 5000),
        )
        for t in targets_raw.get("tcp", [])
    ]

    http_targets = [
        HttpTarget(
            name=t["name"],
            url=t["url"],
            expected_text=t.get("expected_text", ""),
            forbidden_text=t.get("forbidden_text", ""),
            timeout=t.get("timeout", 10000),
        )
        for t in targets_raw.get("http", [])
    ]

    dashboard_targets = []
    for t in targets_raw.get("dashboards", []):
        si_raw = t.get("success_indicator", {})
        si = SuccessIndicator(
            type=si_raw.get("type", "url_contains"),
            value=si_raw.get("value", ""),
        )
        dashboard_targets.append(
            DashboardTarget(
                name=t["name"],
                url=t["url"],
                username=t["username"],
                password=t["password"],
                success_indicator=si,
                timeout=t.get("timeout", 15000),
            )
        )

    channels = []
    for ch in raw.get("alerts", {}).get("channels", []):
        channels.append(ChannelConfig(type=ch["type"], raw=ch))

    alerts = AlertsConfig(channels=channels)

    return AppConfig(
        check_interval=check_interval,
        retries=raw.get("retries", 2),
        dashboard_retries=raw.get("dashboard_retries", 1),
        retry_delay=raw.get("retry_delay", 10),
        ping_targets=ping_targets,
        tcp_targets=tcp_targets,
        dashboard_targets=dashboard_targets,
        http_targets=http_targets,
        alerts=alerts,
        storage=storage,
        web=web,
    )
