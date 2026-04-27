from __future__ import annotations

import argparse


def _parse_bool_flag(value: str) -> bool:
    s = str(value).strip().lower()
    if s in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("must be true or false")


def _add_init_parser(plugin_subparsers) -> None:
    init_parser = plugin_subparsers.add_parser("init", help="Initialize a new plugin scaffold")
    init_parser.add_argument("name", help="Plugin name, e.g. weather-plugin")
    init_parser.add_argument("--path", default=".", help="Parent directory to create plugin in")
    init_parser.add_argument("--force", action="store_true", help="Allow non-empty target directory")
    init_parser.add_argument(
        "--type",
        dest="plugin_type",
        default="tools",
        choices=("tools", "mcp-stdio", "restful-api", "skill"),
        help="Plugin type, default is tools",
    )


def _add_validate_parser(plugin_subparsers) -> None:
    validate_parser = plugin_subparsers.add_parser("validate", help="Validate plugin structure and metadata")
    validate_parser.add_argument("path", help="Plugin root directory")


def _add_pack_parser(plugin_subparsers) -> None:
    pack_parser = plugin_subparsers.add_parser("pack", help="Pack validated plugin into a zip for upload")
    pack_parser.add_argument("path", help="Plugin root directory")
    pack_parser.add_argument(
        "--output",
        "-o",
        default="out",
        help="Output directory for the zip file (default: out)",
    )


def _add_publish_parser(plugin_subparsers) -> None:
    publish_parser = plugin_subparsers.add_parser("publish", help="Pack and upload plugin to market")
    publish_parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Plugin root directory (required when not using --file)",
    )
    publish_parser.add_argument(
        "--file",
        "-f",
        metavar="PATH",
        help="Use existing zip to publish; if set, skip pack and upload this file",
    )
    publish_parser.add_argument(
        "--token",
        dest="user_token",
        help=(
            "End-user Bearer token (Authorization header). Mutually exclusive with --system-token. "
            "If omitted, reads OPENJIUWEN_USER_TOKEN"
        ),
    )
    publish_parser.add_argument(
        "--system-token",
        help=(
            "System-admin token (X-System-Token header). Mutually exclusive with --token. "
            "If omitted, can use OPENJIUWEN_SYSTEM_TOKEN"
        ),
    )
    publish_parser.add_argument("--market-url", help="Market base URL (default: OPENJIUWEN_MARKET_URL)")
    publish_parser.add_argument(
        "--plugin-id",
        help=(
            "Asset/plugin id: omit on first publish (system assigns); required on later publishes "
            "when targeting an existing plugin (use id from first publish or search)"
        ),
    )
    publish_parser.add_argument(
        "--plugin-version",
        help="Override version (marketplace: x.y.z e.g. 1.0.0; v1.0.0 accepted and stripped; optional)",
    )
    publish_parser.add_argument(
        "--version-desc",
        dest="version_desc",
        default=None,
        help="This version's release notes (stored/shown as changelog on the market)",
    )
    publish_parser.add_argument("--force", action="store_true", help="Overwrite existing version")


def _add_info_parser(plugin_subparsers) -> None:
    info_parser = plugin_subparsers.add_parser(
        "info",
        help="Get plugin version details (GET /api/v1/plugins/{asset_id}/versions/{version})",
    )
    info_parser.add_argument(
        "asset_id",
        help="Asset id (same as plugin_id returned by publish)",
    )
    info_parser.add_argument("--version", "-v", required=True, help="Target version")
    info_parser.add_argument("--market-url", help="Market base URL (default: OPENJIUWEN_MARKET_URL)")


def _add_search_parser(plugin_subparsers) -> None:
    search_parser = plugin_subparsers.add_parser(
        "search",
        help="Search plugins on market (no auth); query flags match marketplace PluginListQuery",
    )
    search_parser.add_argument("query", nargs="?", default="", help="search keyword")
    search_parser.add_argument("--market-url", help="Market base URL (default: OPENJIUWEN_MARKET_URL)")
    search_parser.add_argument(
        "--type",
        dest="plugin_type",
        default=None,
        metavar="STR",
        help="plugin type (exact match plugin.yaml runtime.type, such as tools / mcp-stdio / restful-api / skill)",
    )
    search_parser.add_argument(
        "--author",
        metavar="NAME",
        default=None,
        help=(
            "publisher display name (substring fuzzy match via ILIKE, case-insensitive; "
            "quote in shell if special chars)"
        ),
    )
    search_parser.add_argument(
        "--asset-id",
        dest="search_asset_id",
        default=None,
        metavar="ID",
        help="asset id",
    )
    search_parser.add_argument(
        "--asset-type",
        dest="search_asset_type",
        default=None,
        metavar="TYPE",
        help="asset type filter (e.g. plugin; exact match; more types may be added server-side)",
    )
    search_parser.add_argument(
        "--publisher-id",
        dest="search_publisher_id",
        default=None,
        metavar="ID",
        help="publisher id",
    )
    search_parser.add_argument(
        "--page",
        type=int,
        default=None,
        metavar="N",
        help="page (default 1)",
    )
    search_parser.add_argument(
        "--page-size",
        dest="page_size",
        type=int,
        default=None,
        metavar="N",
        help="page size (default 20, max 100)",
    )
    search_parser.add_argument(
        "--order-by",
        default=None,
        choices=("install_count", "like_count", "create_time", "update_time", "review_count"),
        help="order by (default install_count)",
    )
    search_parser.add_argument(
        "--desc",
        type=_parse_bool_flag,
        default=True,
        metavar="BOOL",
        help="descending order (default true)",
    )


def _add_delete_parser(plugin_subparsers) -> None:
    delete_parser = plugin_subparsers.add_parser("delete", help="Delete plugin from market (Store delete API)")
    delete_parser.add_argument(
        "plugin_id",
        help="Asset id (same as plugin_id returned by publish)",
    )
    delete_parser.add_argument("--market-url", help="Market base URL (default: OPENJIUWEN_MARKET_URL)")
    delete_parser.add_argument(
        "--system-token",
        help=(
            "System-admin token (X-System-Token header). Mutually exclusive with --token. "
            "If omitted, can use OPENJIUWEN_SYSTEM_TOKEN"
        ),
    )
    delete_parser.add_argument(
        "--token",
        dest="user_token",
        help=(
            "End-user Bearer token (Authorization header). Mutually exclusive with --system-token. "
            "If omitted, reads OPENJIUWEN_USER_TOKEN"
        ),
    )
    delete_parser.add_argument(
        "--version",
        help="Version to delete; if omitted then delete all versions",
    )


def _add_install_parser(plugin_subparsers) -> None:
    install_parser = plugin_subparsers.add_parser(
        "install",
        help=(
            "Download artifact zip: copy bundle (default parent cwd); tools run pip on dist/*.whl "
            "into the current Python env; -o/--output only sets bundle parent dir "
            "(GET /api/v1/artifacts/{asset_id})"
        ),
    )
    install_parser.add_argument(
        "asset_id",
        help="Market asset_id (same as plugin_id returned by publish)",
    )
    install_parser.add_argument("--market-url", help="Market base URL (default: OPENJIUWEN_MARKET_URL)")
    install_parser.add_argument(
        "--version",
        "-v",
        dest="plugin_version",
        metavar="VER",
        help=(
            "Semantic version to download (e.g. 1.0.0); passed as ?version= to GET "
            "/api/v1/artifacts/{id}; omit for latest"
        ),
    )
    install_parser.add_argument(
        "--output",
        "-o",
        default=None,
        metavar="DIR",
        help=(
            "Parent directory for the plugin bundle folder (zip archive root name as subdir). "
            "Default: cwd. For tools, pip always installs wheels into the current Python env; "
            "this option only changes where the bundle is saved."
        ),
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwrite when target directory already exists",
    )


def _add_skill_import_parser(plugin_subparsers) -> None:
    sip = plugin_subparsers.add_parser(
        "skill-import",
        help=(
            "Batch-import skills from bundle zip or directory "
            "(POST /api/v1/plugins/skill-import; X-System-Token; SHA-256 computed locally)"
        ),
    )
    sip.add_argument(
        "bundle_path",
        metavar="BUNDLE",
        help=(
            "Bundle .zip path, or directory with the same layout as a collection bundle "
            "(top-level skill dirs, optional manifest.json); directories are packed locally then uploaded"
        ),
    )
    sip.add_argument("--market-url", help="Market base URL (default: OPENJIUWEN_MARKET_URL)")
    sip.add_argument(
        "--system-token",
        help="System admin token (X-System-Token). Default: OPENJIUWEN_SYSTEM_TOKEN",
    )
    sip.add_argument("--force", action="store_true", help="Pass force=true to each publish")
    sip.add_argument(
        "--fail-fast",
        dest="fail_fast",
        action="store_true",
        help="Stop after first entry failure",
    )


def _add_market_url_arg(parser) -> None:
    parser.add_argument("--market-url", help="Market base URL (default: OPENJIUWEN_MARKET_URL)")


def _add_auth_args(parser) -> None:
    parser.add_argument(
        "--token",
        dest="user_token",
        help=(
            "End-user Bearer token (Authorization header). Mutually exclusive with --system-token. "
            "If omitted, reads OPENJIUWEN_USER_TOKEN"
        ),
    )
    parser.add_argument(
        "--system-token",
        help=(
            "System-admin token (X-System-Token header). Mutually exclusive with --token. "
            "If omitted, can use OPENJIUWEN_SYSTEM_TOKEN"
        ),
    )


def _add_patch_publish_args(parser, *, include_patch_version: bool) -> None:
    parser.add_argument("skill_asset_id", help="Target Skill asset id")
    if include_patch_version:
        parser.add_argument(
            "--patch-version",
            help="Self-evolution patch version. Optional for publish; defaults to plugin.yaml version.",
        )
        parser.add_argument("path", help="Skill plugin directory or existing zip file")
    else:
        parser.add_argument("patch_version", help="Self-evolution patch version to replace")
        parser.add_argument("path", help="Skill plugin directory or existing zip file")
    parser.add_argument(
        "--source-version",
        dest="source_skill_version",
        help="Formal Skill version this patch evolved from",
    )
    parser.add_argument(
        "--version-desc",
        dest="version_desc",
        default=None,
        help="Self-evolution patch changelog",
    )
    parser.add_argument(
        "--patch-type",
        default="self-evolution",
        help="Patch type, default self-evolution",
    )
    parser.add_argument(
        "--metadata",
        help='JSON object string, e.g. \'{"score":0.9,"source":"manual"}\'',
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing patch version")
    _add_auth_args(parser)
    _add_market_url_arg(parser)


def _add_patch_parser(plugin_subparsers) -> None:
    patch_parser = plugin_subparsers.add_parser("patch", help="Manage Skill self-evolution patch assets")
    patch_subparsers = patch_parser.add_subparsers(dest="patch_command")

    publish_parser = patch_subparsers.add_parser(
        "publish",
        help="Publish a Skill self-evolution patch zip",
    )
    _add_patch_publish_args(publish_parser, include_patch_version=True)

    update_parser = patch_subparsers.add_parser(
        "update",
        help="Replace an existing Skill self-evolution patch zip",
    )
    _add_patch_publish_args(update_parser, include_patch_version=False)

    list_parser = patch_subparsers.add_parser("list", help="List Skill self-evolution patches")
    list_parser.add_argument("skill_asset_id", help="Target Skill asset id")
    list_parser.add_argument("--page", type=int, default=1, metavar="N", help="page (default 1)")
    list_parser.add_argument("--page-size", dest="page_size", type=int, default=20, metavar="N", help="page size")
    list_parser.add_argument("--status", dest="patch_status", default=None, help="Patch status filter, e.g. ACTIVE")
    _add_market_url_arg(list_parser)

    info_parser = patch_subparsers.add_parser("info", help="Get one Skill self-evolution patch detail")
    info_parser.add_argument("skill_asset_id", help="Target Skill asset id")
    info_parser.add_argument("patch_version", help="Self-evolution patch version")
    _add_market_url_arg(info_parser)

    download_parser = patch_subparsers.add_parser("download", help="Download one Skill self-evolution patch zip")
    download_parser.add_argument("skill_asset_id", help="Target Skill asset id")
    download_parser.add_argument("patch_version", help="Self-evolution patch version")
    download_parser.add_argument(
        "--output",
        "-o",
        default=None,
        metavar="PATH",
        help="Output zip file path or parent directory. Default: cwd/<asset>-<version>.zip",
    )
    _add_market_url_arg(download_parser)

    delete_parser = patch_subparsers.add_parser("delete", help="Delete one Skill self-evolution patch version")
    delete_parser.add_argument("skill_asset_id", help="Target Skill asset id")
    delete_parser.add_argument("patch_version", help='Patch version to delete, or "all"')
    _add_auth_args(delete_parser)
    _add_market_url_arg(delete_parser)


def build_plugin_parser(prog_name: str = "openjiuwen-plugin") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog_name)
    plugin_subparsers = parser.add_subparsers(dest="plugin_command")
    _add_init_parser(plugin_subparsers)
    _add_validate_parser(plugin_subparsers)
    _add_pack_parser(plugin_subparsers)
    _add_publish_parser(plugin_subparsers)
    _add_info_parser(plugin_subparsers)
    _add_search_parser(plugin_subparsers)
    _add_delete_parser(plugin_subparsers)
    _add_install_parser(plugin_subparsers)
    _add_skill_import_parser(plugin_subparsers)
    _add_patch_parser(plugin_subparsers)
    return parser
