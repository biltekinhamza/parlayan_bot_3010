from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "bot_settings.yaml"
_DEFAULT: dict[str, Any] = {}
_current: dict[str, Any] = {}


def _load_from_disk() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_to_disk(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


class ConfigStore:
    def __init__(self) -> None:
        self._config: dict[str, Any] = _load_from_disk()

    def get(self) -> dict[str, Any]:
        return copy.deepcopy(self._config)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Kısmi güncelleme — sadece verilen anahtarları değiştir."""
        self._deep_merge(self._config, patch)
        _save_to_disk(self._config)
        return self.get()

    def replace(self, new_config: dict[str, Any]) -> dict[str, Any]:
        """Tam config değiştir."""
        self._config = new_config
        _save_to_disk(self._config)
        return self.get()

    @staticmethod
    def _deep_merge(base: dict, patch: dict) -> None:
        for key, val in patch.items():
            if isinstance(val, dict) and isinstance(base.get(key), dict):
                ConfigStore._deep_merge(base[key], val)
            else:
                base[key] = val


config_store = ConfigStore()
