# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import argparse

from cli_core.cli_args import _parse_bool_flag


def _normalize_skill_like_plugin_type(value: str) -> str:
    return "swarmskill" if value == "teamskills" else value


def _skill_type_choice(value: str) -> str:
    normalized = _normalize_skill_like_plugin_type(value)
    if normalized not in {"skill", "swarmskill"}:
        raise argparse.ArgumentTypeError("expected one of: skill, swarmskill")
    return normalized


def _skill_type_filter_choice(value: str) -> str:
    selected = {
        _skill_type_choice(part.strip())
        for part in value.split(",")
        if part.strip()
    }
    if not selected:
        raise argparse.ArgumentTypeError("expected one of: skill, swarmskill")
    return ",".join(kind for kind in ("skill", "swarmskill") if kind in selected)


def _skill_type_metavar() -> str:
    return "{skill,swarmskill}"


def _skill_type_help() -> str:
    return "Scaffold type"


def _search_type_help() -> str:
    return "Filter by skill type"


def _default_search_plugin_type() -> str:
    return "skill,swarmskill"


def _set_legacy_teamskills_marker(args: argparse.Namespace) -> None:
    setattr(args, "_teamskills_cli", True)
    setattr(args, "_swarmskill_cli", True)


class _NormalizeSkillTypeAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, _skill_type_choice(values))
        setattr(namespace, "_teamskills_cli", True)
        setattr(namespace, "_swarmskill_cli", True)


class _NormalizeSkillTypeFilterAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, _skill_type_filter_choice(values))
        setattr(namespace, "_teamskills_cli", True)
        setattr(namespace, "_swarmskill_cli", True)


class _LegacyTeamskillsAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, "_teamskills_cli", True)


class _LegacyStoreTrueAction(argparse.Action):
    def __init__(
        self,
        option_strings,
        dest,
        nargs=0,
        default=False,
        required=False,
        help_text=None,
        **kwargs,
    ):
        if help_text is not None and "help" not in kwargs:
            kwargs["help"] = help_text
        super().__init__(
            option_strings,
            dest,
            nargs=nargs,
            default=default,
            required=required,
            **kwargs,
        )

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, True)
        setattr(namespace, "_teamskills_cli", True)


class _LegacyStoreConstAction(argparse.Action):
    def __init__(
        self,
        option_strings,
        dest,
        nargs=0,
        const=None,
        default=None,
        required=False,
        help_text=None,
        **kwargs,
    ):
        super().__init__(
            option_strings,
            dest,
            nargs=nargs,
            const=const,
            default=default,
            required=required,
            help=help_text,
            **kwargs,
        )

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, self.const)
        setattr(namespace, "_teamskills_cli", True)


class _LegacyStoreBoolAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, _parse_bool_flag(values))
        setattr(namespace, "_teamskills_cli", True)


class _LegacyStoreIntAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, int(values))
        setattr(namespace, "_teamskills_cli", True)


class _LegacyStoreStrAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, "_teamskills_cli", True)


def _add_legacy_teamskills_aliases(plugin_subparsers) -> None:
    init_parser = plugin_subparsers.add_parser("init-teamskills", help=argparse.SUPPRESS)
    init_parser.add_argument("name", help=argparse.SUPPRESS)
    init_parser.add_argument("--path", default=".", help=argparse.SUPPRESS, action=_LegacyStoreStrAction)
    init_parser.add_argument("--force", action=_LegacyStoreTrueAction, help=argparse.SUPPRESS)
    init_parser.add_argument(
        "--type",
        dest="plugin_type",
        default="swarmskill",
        choices=("swarmskill", "skill"),
        type=_skill_type_choice,
        metavar=_skill_type_metavar(),
        help=argparse.SUPPRESS,
        action=_NormalizeSkillTypeAction,
    )
    init_parser.set_defaults(skill_command="init", _teamskills_cli=True)

    search_parser = plugin_subparsers.add_parser("search-teamskills", help=argparse.SUPPRESS)
    search_parser.add_argument("query", nargs="?", default="", help=argparse.SUPPRESS)
    search_parser.add_argument("--market-url", help=argparse.SUPPRESS, action=_LegacyStoreStrAction)
    search_parser.add_argument(
        "--type",
        dest="plugin_type",
        default=_default_search_plugin_type(),
        metavar=_skill_type_metavar(),
        help=argparse.SUPPRESS,
        action=_NormalizeSkillTypeFilterAction,
    )
    search_parser.add_argument(
        "--author",
        default=None,
        help=argparse.SUPPRESS,
        action=_LegacyStoreStrAction,
    )
    search_parser.add_argument(
        "--asset-id",
        dest="search_asset_id",
        default=None,
        help=argparse.SUPPRESS,
        action=_LegacyStoreStrAction,
    )
    search_parser.add_argument(
        "--asset-type",
        dest="search_asset_type",
        default=None,
        help=argparse.SUPPRESS,
        action=_LegacyStoreStrAction,
    )
    search_parser.add_argument(
        "--publisher-id",
        dest="search_publisher_id",
        default=None,
        help=argparse.SUPPRESS,
        action=_LegacyStoreStrAction,
    )
    search_parser.add_argument(
        "--page",
        default=None,
        help=argparse.SUPPRESS,
        action=_LegacyStoreIntAction,
    )
    search_parser.add_argument(
        "--page-size",
        dest="page_size",
        default=None,
        help=argparse.SUPPRESS,
        action=_LegacyStoreIntAction,
    )
    search_parser.add_argument(
        "--order-by",
        default=None,
        help=argparse.SUPPRESS,
        action=_LegacyStoreStrAction,
    )
    search_parser.add_argument(
        "--desc",
        default=True,
        help=argparse.SUPPRESS,
        action=_LegacyStoreBoolAction,
    )
    search_parser.set_defaults(skill_command="search", _teamskills_cli=True)


def _add_init_parser_swarmskill(plugin_subparsers) -> None:
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
        default="swarmskill",
        choices=("swarmskill", "skill"),
        type=_skill_type_choice,
        metavar=_skill_type_metavar(),
        help=_skill_type_help(),
        action=_NormalizeSkillTypeAction,
    )


def _add_validate_parser_swarmskill(plugin_subparsers) -> None:
    validate_parser = plugin_subparsers.add_parser(
        "validate",
        help="Validate skill directory",
    )
    validate_parser.add_argument("path", help="Skill root path")


def _add_pack_parser_swarmskill(plugin_subparsers) -> None:
    pack_parser = plugin_subparsers.add_parser("pack", help="Pack skill into zip")
    pack_parser.add_argument("path", help="Skill root path")
    pack_parser.add_argument(
        "--output",
        "-o",
        default="out",
        help="Output directory (default: out)",
    )


def _add_publish_parser_swarmskill(plugin_subparsers) -> None:
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


def _add_info_parser_swarmskill(plugin_subparsers) -> None:
    info_parser = plugin_subparsers.add_parser(
        "info",
        help="Show skill version details",
    )
    info_parser.add_argument(
        "asset_id",
        help="Skill id",
    )
    info_parser.add_argument(
        "--version",
        "-v",
        default=None,
        metavar="VER",
        help="Version (default: latest public / latest)",
    )
    info_parser.add_argument(
        "--market-url",
        help="Market base URL",
    )


def _add_search_parser_swarmskill(plugin_subparsers) -> None:
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
        default=_default_search_plugin_type(),
        metavar=_skill_type_metavar(),
        help=_search_type_help(),
        action=_NormalizeSkillTypeFilterAction,
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
        help="Page size (1-200)",
    )
    search_parser.add_argument(
        "--order-by",
        default=None,
        choices=("install_count", "like_count", "view_count", "create_time", "update_time", "review_count"),
        help="Sort field",
    )
    search_parser.add_argument(
        "--desc",
        type=_parse_bool_flag,
        default=True,
        metavar="BOOL",
        help="Sort descending",
    )


def _add_delete_parser_swarmskill(plugin_subparsers) -> None:
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


def _add_install_parser_swarmskill(plugin_subparsers) -> None:
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


def _add_skill_import_parser_swarmskill(plugin_subparsers) -> None:
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


def build_swarmskill_parser(prog_name: str = "jiuwen-teamskills") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog_name, allow_abbrev=False)
    parser.set_defaults(_swarmskill_cli=True)
    skill_subparsers = parser.add_subparsers(dest="skill_command")
    _add_init_parser_swarmskill(skill_subparsers)
    _add_validate_parser_swarmskill(skill_subparsers)
    _add_pack_parser_swarmskill(skill_subparsers)
    _add_publish_parser_swarmskill(skill_subparsers)
    _add_info_parser_swarmskill(skill_subparsers)
    _add_search_parser_swarmskill(skill_subparsers)
    _add_delete_parser_swarmskill(skill_subparsers)
    _add_install_parser_swarmskill(skill_subparsers)
    _add_skill_import_parser_swarmskill(skill_subparsers)
    _add_legacy_teamskills_aliases(skill_subparsers)
    return parser

