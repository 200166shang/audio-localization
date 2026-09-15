"""Start pyVideoTrans with Bailian credentials without persisting the API key."""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path


def _credentials() -> tuple[str, str]:
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    workspace = os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
    if key and workspace:
        return key, workspace

    path = Path(
        os.environ.get(
            "BAILIAN_CREDENTIAL_CSV",
            "/Users/syz/Downloads/默认业务空间-apiKey-7243748.csv",
        )
    ).expanduser()
    try:
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    except OSError as exc:
        raise RuntimeError(
            "set DASHSCOPE_API_KEY and DASHSCOPE_WORKSPACE_ID, or "
            "BAILIAN_CREDENTIAL_CSV"
        ) from exc
    if not rows:
        raise RuntimeError("Bailian credential CSV is empty")
    value_columns = [name for name in rows[0] if name != "id"]
    if len(value_columns) != 1:
        raise RuntimeError("Bailian credential CSV must contain one workspace column")
    values = {row["id"]: (row[value_columns[0]] or "").strip() for row in rows}
    if not values.get("apiKey") or not values.get("workspaceId"):
        raise RuntimeError("Bailian credential CSV lacks apiKey or workspaceId")
    return values["apiKey"], values["workspaceId"]


def main() -> int:
    home = Path(
        os.environ.get("PYVIDEOTRANS_HOME", "/Users/syz/code/pyvideotrans")
    ).expanduser().resolve()
    if not (home / "cli.py").is_file():
        raise RuntimeError(f"pyVideoTrans CLI not found: {home / 'cli.py'}")

    key, workspace = _credentials()
    os.chdir(home)
    sys.path.insert(0, str(home))
    from videotrans.configure import config

    provider_params = {
        "qwenmt_key": key,
        "qwenmt_spaceid": workspace,
        "qwenmt_model": "qwen-mt-turbo",
        "qwentts_key": key,
        "qwentts_spaceid": workspace,
        "qwentts_model": "qwen3-tts-flash",
    }
    # pyVideoTrans changed params from a dict to AppParams.  Keep the
    # bridge compatible with both interfaces so upgrades do not break the
    # localization entry point before the cloud calls begin.
    if hasattr(config.params, "getset_params"):
        config.params.getset_params(provider_params)
    else:
        config.params.update(provider_params)
    config.settings["dubbing_thread"] = 1
    config.settings["dubbing_wait"] = 0
    # These compacted cues can contain close to 50 English words each.  A
    # qwen-mt-turbo may merge or truncate adjacent long SRT cues while still
    # returning success. Translate one compacted cue per request so every
    # source timestamp remains represented exactly once.
    config.settings["aitrans_thread"] = 1
    config.settings["translation_wait"] = 2
    config.settings["retry_nums"] = 3

    import cli

    return cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
