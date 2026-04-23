from __future__ import annotations

import argparse

from cli_core.cli_args import _parse_bool_flag


def _add_init_parser_teamskills(plugin_subparsers) -> None:
    init_parser = plugin_subparsers.add_parser(
        "init",
        help="Create a skill scaffold",
    )
    init_parser.add_argument("name", help="Skill name")
    init_parser.add_argument("--path", default=".", help="Parent directory (default: .)")
    init_parser.add_argument("--force", action="store_true", help="Overwrite non-empty target")
    init_parser.add_argument(
        "--type",
        dest="plugin_type",
        default="teamskills",
        choices=("teamskills", "skill"),
        help="Scaffold type",
    )


def _add_validate_parser_teamskills(plugin_subparsers) -> None:
    validate_parser = plugin_subparsers.add_parser(
        "validate",
        help="Validate skill directory",
    )
    validate_parser.add_argument("path", help="Skill root path")


def _add_pack_parser_teamskills(plugin_subparsers) -> None:
    pack_parser = plugin_subparsers.add_parser("pack", help="Pack skill into zip")
    pack_parser.add_argument("path", help="Skill root path")
    pack_parser.add_argument(
        "--output",
        "-o",
        default="out",
        help="Output directory (default: out)",
    )


def _add_publish_parser_teamskills(plugin_subparsers) -> None:
    publish_parser = plugin_subparsers.add_parser("publish", help="Publish a skill")
    publish_parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Skill root path (required without --file)",
    )
    publish_parser.add_argument(
        "--file",
        "-f",
        metavar="PATH",
        help="Publish an existing zip",
    )
    publish_parser.add_argument(
        "--token",
        dest="user_token",
        help=(
            "User bearer token (mutually exclusive with --system-token)"
        ),
    )
    publish_parser.add_argument(
        "--system-token",
        help=(
            "System token (mutually exclusive with --token)"
        ),
    )
    publish_parser.add_argument(
        "--market-url",
        help="Market base URL",
    )
    publish_parser.add_argument(
        "--id",
        dest="plugin_id",
        default=None,
        help=(
            "Existing skill id (required for later versions)"
        ),
    )
    publish_parser.add_argument(
        "--version",
        "-v",
        dest="plugin_version",
        help="Version to publish (x.y.z)",
    )
    publish_parser.add_argument(
        "--version-desc",
        dest="version_desc",
        default=None,
        help="Version notes",
    )
    publish_parser.add_argument("--force", action="store_true", help="Overwrite existing version")


def _add_info_parser_teamskills(plugin_subparsers) -> None:
    info_parser = plugin_subparsers.add_parser(
        "info",
        help="Show skill version details",
    )
    info_parser.add_argument(
        "asset_id",
        help="Skill id",
    )
    info_parser.add_argument("--version", "-v", required=True, help="Version")
    info_parser.add_argument(
        "--market-url",
        help="Market base URL",
    )


def _add_search_parser_teamskills(plugin_subparsers) -> None:
    search_parser = plugin_subparsers.add_parser(
        "search",
        help="Search skills",
    )
    search_parser.add_argument("query", nargs="?", default="", help="Keyword")
    search_parser.add_argument(
        "--market-url",
        help="Market base URL",
    )
    search_parser.add_argument(
        "--type",
        dest="plugin_type",
        default=None,
        choices=("skill", "teamskills"),
        metavar="{skill,teamskills}",
        help="Filter by skill type",
    )
    search_parser.add_argument(
        "--author",
        metavar="NAME",
        default=None,
        help="Filter by author name",
    )
    search_parser.add_argument(
        "--asset-id",
        dest="search_asset_id",
        default=None,
        metavar="ID",
        help="Filter by skill id",
    )
    search_parser.add_argument(
        "--asset-type",
        dest="search_asset_type",
        default=None,
        metavar="TYPE",
        help="Filter by asset type",
    )
    search_parser.add_argument(
        "--publisher-id",
        dest="search_publisher_id",
        default=None,
        metavar="ID",
        help="Filter by publisher id",
    )
    search_parser.add_argument(
        "--page",
        type=int,
        default=None,
        metavar="N",
        help="Page number",
    )
    search_parser.add_argument(
        "--page-size",
        dest="page_size",
        type=int,
        default=None,
        metavar="N",
        help="Page size (1-100)",
    )
    search_parser.add_argument(
        "--order-by",
        default=None,
        choices=("install_count", "like_count", "create_time", "update_time", "review_count"),
        help="Sort field",
    )
    search_parser.add_argument(
        "--desc",
        type=_parse_bool_flag,
        default=True,
        metavar="BOOL",
        help="Sort descending",
    )


def _add_delete_parser_teamskills(plugin_subparsers) -> None:
    delete_parser = plugin_subparsers.add_parser("delete", help="Delete a skill")
    delete_parser.add_argument(
        "skill_id",
        metavar="SKILL_ID",
        help="Skill id",
    )
    delete_parser.add_argument(
        "--market-url",
        help="Market base URL",
    )
    delete_parser.add_argument(
        "--system-token",
        help=(
            "System token (mutually exclusive with --token)"
        ),
    )
    delete_parser.add_argument(
        "--token",
        dest="user_token",
        help=(
            "User bearer token (mutually exclusive with --system-token)"
        ),
    )
    delete_parser.add_argument(
        "--version",
        "-v",
        help="Version (omit to delete all)",
    )


def _add_install_parser_teamskills(plugin_subparsers) -> None:
    install_parser = plugin_subparsers.add_parser(
        "install",
        help=(
            "Download and install a skill"
        ),
    )
    install_parser.add_argument(
        "asset_id",
        help="Skill id",
    )
    install_parser.add_argument(
        "--market-url",
        help="Market base URL",
    )
    install_parser.add_argument(
        "--version",
        "-v",
        dest="plugin_version",
        metavar="VER",
        help="Version (default: latest)",
    )
    install_parser.add_argument(
        "--output",
        "-o",
        default=None,
        metavar="DIR",
        help="Output parent directory (default: cwd)",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing target",
    )


def _add_skill_import_parser_teamskills(plugin_subparsers) -> None:
    sip = plugin_subparsers.add_parser(
        "skill-import",
        help=(
            "Batch import skills"
        ),
    )
    sip.add_argument(
        "bundle_path",
        metavar="BUNDLE",
        help="Bundle zip path or directory",
    )
    sip.add_argument(
        "--market-url",
        help="Market base URL",
    )
    sip.add_argument(
        "--system-token",
        help="System token",
    )
    sip.add_argument("--force", action="store_true", help="Force publish each entry")
    sip.add_argument(
        "--fail-fast",
        dest="fail_fast",
        action="store_true",
        help="Stop on first failure",
    )


def build_teamskills_parser(prog_name: str = "jiuwen-teamskills") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog_name, allow_abbrev=False)
    parser.set_defaults(_teamskills_cli=True)
    skill_subparsers = parser.add_subparsers(dest="skill_command")
    _add_init_parser_teamskills(skill_subparsers)
    _add_validate_parser_teamskills(skill_subparsers)
    _add_pack_parser_teamskills(skill_subparsers)
    _add_publish_parser_teamskills(skill_subparsers)
    _add_info_parser_teamskills(skill_subparsers)
    _add_search_parser_teamskills(skill_subparsers)
    _add_delete_parser_teamskills(skill_subparsers)
    _add_install_parser_teamskills(skill_subparsers)
    _add_skill_import_parser_teamskills(skill_subparsers)
    return parser
