#!/usr/bin/env python3
"""Onboard or update a marketing client with any subset of the five platforms.

Examples:
    python scripts/onboard-client.py add --key acme --name "Acme Co" --tenant 12
    python scripts/onboard-client.py add --key acme --name "Acme" --from-file local-dev-config.json --yes
    python scripts/onboard-client.py show --key acme
    python scripts/onboard-client.py list
    python scripts/onboard-client.py enable --key acme --platform gbp
    python scripts/onboard-client.py validate --key acme
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _onboard_core as core  # noqa: E402
from shared import manifest as manifest_mod  # noqa: E402
from shared.errors import AdsMcpError  # noqa: E402
from shared.models import ClientManifest  # noqa: E402

PLATFORMS = core.KNOWN_PLATFORMS if hasattr(core, "KNOWN_PLATFORMS") else manifest_mod.KNOWN_PLATFORMS


def _prompt(label: str, *, secret: bool = False, default: str = "") -> str:
    import getpass

    suffix = f" [{default}]" if default else ""
    raw = (getpass.getpass if secret else input)(f"{label}{suffix}: ").strip()
    return raw or default


def _collect_interactive(existing: ClientManifest | None) -> list[core.PlatformInput]:
    inputs: list[core.PlatformInput] = []
    for platform in manifest_mod.KNOWN_PLATFORMS:
        cur = existing.platforms.get(platform) if existing else None
        default_enabled = "y" if (cur and cur.enabled) else "n"
        ans = _prompt(f"Enable {platform}? (y/n)", default=default_enabled).lower()
        enabled = ans.startswith("y")
        if not enabled:
            inputs.append(core.PlatformInput(platform, False, {}, {}))
            continue
        metadata = {}
        for key in core.METADATA_KEYS_BY_PLATFORM.get(platform, ()):
            metadata[key] = _prompt(f"  {platform}.{key}")
        credentials = {}
        for key in core.CREDENTIAL_KEYS_BY_PLATFORM.get(platform, ()):
            credentials[key] = _prompt(f"  {platform}.{key}", secret=True)
        inputs.append(core.PlatformInput(platform, True, metadata, credentials))
    return inputs


def _region_from_args(args) -> None:
    if getattr(args, "region", None):
        os.environ["AWS_REGION"] = args.region
        from shared.config import get_settings

        get_settings.cache_clear()


def cmd_add(args) -> int:
    _region_from_args(args)
    existing = manifest_mod.load_client_manifest(args.key)
    if args.from_file:
        flat_all = json.loads(Path(args.from_file).read_text())
        flat = flat_all.get(args.key, flat_all)
        inputs = core.derive_platform_inputs_from_flat(flat)
    elif args.yes:
        print("--yes requires --from-file", file=sys.stderr)
        return 2
    else:
        inputs = _collect_interactive(existing)

    manifest = core.build_manifest(
        args.key, args.name or (existing.displayName if existing else args.key),
        tenant_id=args.tenant, industry=args.industry,
        platform_inputs=inputs, existing=existing,
    )
    result = core.apply_onboarding(manifest, inputs, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))

    rc = 0
    if not args.dry_run and not args.skip_validate:
        for pi in inputs:
            if pi.enabled:
                ok, detail = core.validate_platform(args.key, pi.platform)
                print(f"  validate {pi.platform}: {'OK' if ok else 'FAIL'} — {detail}")
                rc = rc or (0 if ok else 1)

    _print_checklist(args.key)
    return rc


def cmd_update(args) -> int:
    return cmd_add(args)


def cmd_show(args) -> int:
    _region_from_args(args)
    report = manifest_mod.client_readiness(args.key)
    if report is None:
        print(f"No manifest for '{args.key}'.", file=sys.stderr)
        return 1
    print(json.dumps(report.model_dump(), indent=2))
    return 0


def cmd_list(args) -> int:
    _region_from_args(args)
    manifests = manifest_mod.list_client_manifests(include_inactive=True)
    if not manifests:
        print("No clients configured.")
        return 0
    for m in manifests:
        enabled = [p for p, c in m.platforms.items() if c.enabled]
        print(f"{m.businessKey:24} {m.status:9} {', '.join(enabled) or '(no platforms)'}")
    return 0


def cmd_enable(args) -> int:
    return _toggle(args, enabled=True)


def cmd_disable(args) -> int:
    return _toggle(args, enabled=False)


def _toggle(args, *, enabled: bool) -> int:
    _region_from_args(args)
    metadata = {}
    credentials = {}
    if enabled:
        for key in core.METADATA_KEYS_BY_PLATFORM.get(args.platform, ()):
            metadata[key] = _prompt(f"{args.platform}.{key}")
        for key in core.CREDENTIAL_KEYS_BY_PLATFORM.get(args.platform, ()):
            credentials[key] = _prompt(f"{args.platform}.{key}", secret=True)
    try:
        manifest_mod.set_platform_config(
            args.key, args.platform, enabled=enabled, metadata=metadata, dry_run=args.dry_run
        )
    except AdsMcpError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    if enabled and credentials and not args.dry_run:
        from shared.config import get_settings
        from shared import secrets as secrets_mod

        settings = get_settings()
        sid = manifest_mod.credential_secret_id(args.key, args.platform)
        cur = secrets_mod.get_secret(sid, settings)
        merged = dict(cur) if isinstance(cur, dict) else {}
        merged.update({k: v for k, v in {**credentials, **metadata}.items() if v})
        secrets_mod.put_secret(sid, merged, settings)
    print(f"{args.platform} {'enabled' if enabled else 'disabled'} for {args.key}.")
    return 0


def cmd_validate(args) -> int:
    _region_from_args(args)
    manifest = manifest_mod.load_client_manifest(args.key)
    if manifest is None:
        print(f"No manifest for '{args.key}'.", file=sys.stderr)
        return 1
    rc = 0
    targets = [args.platform] if args.platform else [
        p for p, c in manifest.platforms.items() if c.enabled
    ]
    for platform in targets:
        ok, detail = core.validate_platform(args.key, platform)
        print(f"{platform}: {'OK' if ok else 'FAIL'} — {detail}")
        rc = rc or (0 if ok else 1)
    return rc


def _print_checklist(key: str) -> None:
    print(
        f"\nNext steps for '{key}':\n"
        f"  1. If this client needs protected campaigns / geo locks / approval gates,\n"
        f"     add an entry to shared/rules.py GOOGLE_ADS_RULES['{key}'].\n"
        f"  2. If the content agent will write for this client, create\n"
        f"     servers/content-agent/brands/{key}.md from brands/_TEMPLATE.md.\n"
        f"  3. Add a business section to CLAUDE.md.\n"
        f"  4. Deploy:  bash scripts/deploy.sh\n"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--key", required=True)
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--region")

    a = sub.add_parser("add"); common(a)
    a.add_argument("--name"); a.add_argument("--tenant", type=int)
    a.add_argument("--industry"); a.add_argument("--from-file")
    a.add_argument("--yes", action="store_true"); a.add_argument("--skip-validate", action="store_true")
    a.set_defaults(func=cmd_add)

    u = sub.add_parser("update"); common(u)
    u.add_argument("--name"); u.add_argument("--tenant", type=int)
    u.add_argument("--industry"); u.add_argument("--from-file")
    u.add_argument("--yes", action="store_true"); u.add_argument("--skip-validate", action="store_true")
    u.set_defaults(func=cmd_update)

    s = sub.add_parser("show"); common(s); s.set_defaults(func=cmd_show)
    lst = sub.add_parser("list"); lst.add_argument("--region"); lst.set_defaults(func=cmd_list)

    for name, fn in (("enable", cmd_enable), ("disable", cmd_disable)):
        e = sub.add_parser(name); common(e)
        e.add_argument("--platform", required=True, choices=list(manifest_mod.KNOWN_PLATFORMS))
        e.set_defaults(func=fn)

    v = sub.add_parser("validate"); common(v)
    v.add_argument("--platform", choices=list(manifest_mod.KNOWN_PLATFORMS))
    v.set_defaults(func=cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
