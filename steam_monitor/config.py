"""配置加载、校验与默认值合并（DESIGN.md §4）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "BUILTIN_CONTENT",
    "BUILTIN_TITLE",
    "Config",
    "ConfigError",
    "Channel",
    "DEFAULT_CHECKPOINTS",
    "DEFAULT_INTERVAL_HOURS",
    "load_config",
]


class ConfigError(Exception):
    """配置错误：文件缺失、YAML 解析失败或字段不合法。"""


DEFAULT_CHECKPOINTS = [14, 7, -3]
DEFAULT_INTERVAL_HOURS = 6.0
DEFAULT_REPORT_DIR = "reports"

#: 内置默认模板（§8.2 模板回退链的最后一环）
BUILTIN_TITLE = "{game_name}"
BUILTIN_CONTENT = "{stage}\n{store_url}"

#: 渠道配置中不作为 onepush 参数透传的保留字段
_CHANNEL_META_KEYS = ("provider", "title", "content")


@dataclass
class Channel:
    """单个通知渠道（对应 config.notify.channels 中的一项）。"""

    provider: str
    params: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    content: str | None = None


@dataclass
class Config:
    """规范化后的完整配置。"""

    publishers: list[str] = field(default_factory=list)
    games: list[str] = field(default_factory=list)
    checkpoints: list[int] = field(default_factory=lambda: list(DEFAULT_CHECKPOINTS))
    interval_hours: float = DEFAULT_INTERVAL_HOURS
    default_template: dict[str, str] | None = None
    channels: list[Channel] = field(default_factory=list)
    report_dir: Path = Path(DEFAULT_REPORT_DIR)
    source_path: str = "config.yaml"

    @property
    def template_title(self) -> str:
        """全局模板 title，缺失时回退到内置默认。"""
        if self.default_template and self.default_template.get("title"):
            return str(self.default_template["title"])
        return BUILTIN_TITLE

    @property
    def template_content(self) -> str:
        """全局模板 content，缺失时回退到内置默认。"""
        if self.default_template and self.default_template.get("content"):
            return str(self.default_template["content"])
        return BUILTIN_CONTENT


def load_config(path: str | Path) -> Config:
    """加载并校验 YAML 配置，合并默认值。"""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"配置文件不存在：{p}")
    try:
        with open(p, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败：{exc}") from exc
    except OSError as exc:
        raise ConfigError(f"读取配置文件失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是一个映射（key: value）")
    return _build_config(raw, p)


def _build_config(raw: dict[str, Any], path: Path) -> Config:
    publishers = _string_list(raw.get("publishers"), "publishers")
    games = _string_list(raw.get("games"), "games")
    checkpoints = _checkpoints(raw.get("checkpoints"))
    interval = _interval_hours(raw.get("interval_hours"))
    default_template, channels = _parse_notify(raw.get("notify"))
    report_dir = Path(str(raw.get("report_dir") or DEFAULT_REPORT_DIR))
    return Config(
        publishers=publishers,
        games=games,
        checkpoints=checkpoints,
        interval_hours=interval,
        default_template=default_template,
        channels=channels,
        report_dir=report_dir,
        source_path=str(path),
    )


def _string_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{name} 必须是字符串列表")
    result: list[str] = []
    for item in value:
        # YAML 中 `- 1245620` 会被解析为 int，按字符串处理
        if isinstance(item, str):
            result.append(item.strip())
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            result.append(str(item).strip())
        else:
            raise ConfigError(f"{name} 必须是字符串列表")
    return [x for x in result if x]


def _checkpoints(value: Any) -> list[int]:
    if value is None:
        return list(DEFAULT_CHECKPOINTS)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(x, int) and not isinstance(x, bool) for x in value)
    ):
        raise ConfigError("checkpoints 必须是整数列表，如 [+14, +7, -3]")
    return [int(x) for x in value]


def _interval_hours(value: Any) -> float:
    if value is None:
        return DEFAULT_INTERVAL_HOURS
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError("interval_hours 必须是大于 0 的数字")
    return float(value)


def _parse_notify(value: Any) -> tuple[dict[str, str] | None, list[Channel]]:
    """解析 notify 段：支持两种形态。

    - 列表：仅渠道列表，无全局默认模板。
    - 映射：``default`` 为全局模板，``channels`` 为渠道列表。
    """
    if value is None:
        return None, []
    if isinstance(value, list):
        channels_raw = value
        default_template = None
    elif isinstance(value, dict):
        default_template = _template_map(value.get("default"))
        channels_raw = value.get("channels", [])
        if not isinstance(channels_raw, list):
            raise ConfigError("notify.channels 必须是渠道列表")
    else:
        raise ConfigError("notify 必须是列表，或包含 default / channels 的映射")

    channels: list[Channel] = []
    for item in channels_raw:
        if not isinstance(item, dict):
            raise ConfigError(f"通知渠道配置必须是映射，得到：{item!r}")
        provider = item.get("provider")
        if not isinstance(provider, str) or not provider.strip():
            raise ConfigError("每个通知渠道必须包含非空的 provider 字段")
        params = {k: v for k, v in item.items() if k not in _CHANNEL_META_KEYS}
        channels.append(
            Channel(
                provider=provider.strip(),
                params=params,
                title=item.get("title"),
                content=item.get("content"),
            )
        )
    return default_template, channels


def _template_map(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError("notify.default 必须是包含 title/content 的映射")
    return {str(k): str(v) for k, v in value.items() if k in ("title", "content")}
