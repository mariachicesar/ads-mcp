from __future__ import annotations

import importlib.util
import json
from pathlib import Path

CLI = Path(__file__).resolve().parents[2] / "scripts" / "onboard-client.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("onboard_client", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_list_empty(capsys, clients_dir, fake_secrets):
    cli = _load_cli()
    rc = cli.main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No clients" in out or out.strip() == "[]"


def test_add_from_file_dry_run(tmp_path, capsys, clients_dir, fake_secrets):
    flat = {"acme": {"customer_account_id": "111", "developer_token": "d",
                     "client_id": "c", "client_secret": "s", "refresh_token": "r"}}
    f = tmp_path / "flat.json"
    f.write_text(json.dumps(flat))
    cli = _load_cli()
    rc = cli.main(["add", "--key", "acme", "--name", "Acme",
                   "--from-file", str(f), "--yes", "--dry-run", "--skip-validate"])
    assert rc == 0
    assert fake_secrets.store == {}
