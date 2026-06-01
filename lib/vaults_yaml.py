"""Minimal vaults.yaml reader/writer.

`~/.mindframe/vaults.yaml` is the per-operator catalog of vaults their
mindframe deployment can read/write. v1 supports the basics needed for
sharing: list vaults, add a vault, set default, find a vault by name.

Routing rules (project_path / source_match / keyword) are declared in the
file format but not enforced here — the consumer (keeper.py classifier)
will do that as part of Phase A multi-vault work. This module just owns
the file shape.

Schema (top-level):

  default_vault: <name>            # which vault is the fallback target
  vaults:
    - name: <kebab-case-slug>      # required, unique
      path: <absolute-or-tilde>    # required
      storage:
        type: git
        remote: <url or null>
      routes: [...]                # optional, used by future router
      added_at: <iso timestamp>    # written by this module
      added_via: <one of: setup | share-accept | manual>

Migration: if vaults.yaml doesn't exist but
pluginConfigs.mindframe.options.vault_path does, we treat the latter as
"there is one vault, it is the default" — no migration required, just
read settings.json as fallback.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover — yaml is a mindframe dep
    yaml = None  # type: ignore[assignment]


PATH = Path(os.environ.get(
    "MINDFRAME_VAULTS_FILE",
    str(Path.home() / ".mindframe" / "vaults.yaml"),
))
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_raw() -> dict[str, Any]:
    if yaml is None or not PATH.is_file():
        return {}
    try:
        raw = yaml.safe_load(PATH.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_raw(data: dict[str, Any]) -> None:
    if yaml is None:
        raise RuntimeError("PyYAML not available; cannot write vaults.yaml")
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(PATH.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False))
    os.chmod(tmp, 0o600)
    os.replace(tmp, PATH)


def _legacy_single_vault_from_settings() -> dict[str, Any] | None:
    """If vaults.yaml doesn't exist, synthesize a one-vault config from
    pluginConfigs.mindframe.options.vault_path (the v0.6 single-vault key).
    Lets sharing work for operators who haven't migrated yet.
    """
    if not SETTINGS_PATH.is_file():
        return None
    try:
        settings = json.loads(SETTINGS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    vp = (settings.get("pluginConfigs", {})
                  .get("mindframe", {})
                  .get("options", {})
                  .get("vault_path"))
    if not vp:
        return None
    name = os.path.basename(os.path.normpath(os.path.expanduser(vp))) or "default"
    return {
        "default_vault": name,
        "vaults": [{
            "name": name,
            "path": os.path.expanduser(vp),
            "storage": {"type": "git", "remote": None},
            "added_via": "legacy-single-vault",
        }],
    }


def list_vaults() -> list[dict[str, Any]]:
    raw = _load_raw()
    if not raw.get("vaults"):
        legacy = _legacy_single_vault_from_settings()
        if legacy:
            return legacy["vaults"]
        return []
    return [v for v in raw["vaults"] if isinstance(v, dict)]


def default_vault_name() -> str | None:
    raw = _load_raw()
    if raw.get("default_vault"):
        return raw["default_vault"]
    legacy = _legacy_single_vault_from_settings()
    return legacy["default_vault"] if legacy else None


def get_vault(name: str) -> dict[str, Any] | None:
    for v in list_vaults():
        if v.get("name") == name:
            return v
    return None


def vault_exists(name: str) -> bool:
    return get_vault(name) is not None


def add_vault(
    *, name: str, path: str, storage: dict | None = None,
    routes: list | None = None, added_via: str = "manual",
    set_default: bool = False,
) -> None:
    """Add or upsert a vault. Path is stored as expanded absolute."""
    raw = _load_raw()
    if not raw:
        # Materialize the legacy config first so existing single-vault setups
        # don't lose their default when a second vault gets added.
        legacy = _legacy_single_vault_from_settings()
        if legacy:
            raw = legacy
        else:
            raw = {"vaults": []}
    raw.setdefault("vaults", [])

    entry = {
        "name": name,
        "path": str(Path(path).expanduser().resolve()),
        "storage": storage or {"type": "git", "remote": None},
        "added_at": _now_iso(),
        "added_via": added_via,
    }
    if routes:
        entry["routes"] = routes

    # Upsert by name.
    existing_idx = next(
        (i for i, v in enumerate(raw["vaults"]) if v.get("name") == name),
        None,
    )
    if existing_idx is not None:
        raw["vaults"][existing_idx] = entry
    else:
        raw["vaults"].append(entry)

    if set_default or not raw.get("default_vault"):
        raw["default_vault"] = name

    _save_raw(raw)


def remove_vault(name: str) -> bool:
    raw = _load_raw()
    if not raw.get("vaults"):
        return False
    before = len(raw["vaults"])
    raw["vaults"] = [v for v in raw["vaults"] if v.get("name") != name]
    if len(raw["vaults"]) == before:
        return False
    # Reset default if we just removed it.
    if raw.get("default_vault") == name:
        raw["default_vault"] = raw["vaults"][0]["name"] if raw["vaults"] else None
    _save_raw(raw)
    return True


def set_default(name: str) -> bool:
    if not vault_exists(name):
        return False
    raw = _load_raw() or {"vaults": list_vaults()}
    raw["default_vault"] = name
    _save_raw(raw)
    return True
