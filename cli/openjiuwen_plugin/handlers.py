from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from openjiuwen_plugin.logging_config import get_logger
from openjiuwen_plugin.market import (
    plugin_delete,
    plugin_info,
    plugin_install_download,
    plugin_search,
    skill_patch_delete,
    skill_patch_download_zip,
    skill_patch_info,
    skill_patch_list,
    skill_patch_upload,
    skill_import,
)
from openjiuwen_plugin.utils import sha256_file_hex
from openjiuwen_plugin.plugin import (
    PublishError,
    SKILL_IMPORT_BUNDLE_MAX_BYTES,
    plugin_init,
    plugin_install,
    plugin_pack,
    plugin_pack_skill_bundle,
    plugin_publish,
    plugin_validate,
)
from openjiuwen_plugin.schemas import PluginListQuery
from openjiuwen_plugin.schemas import PublishPluginInput
from openjiuwen_plugin.schemas import SkillPatchPublishRequest

logger = get_logger(__name__)


def _cli_log_command_failed(command: str, detail: object) -> None:
    """子命令失败时的统一日志前缀。"""
    logger.error("openjiuwen-plugin %s failed: %s", command, detail)


def _cli_resolve_market_url(args_market_url: str | None, *, err_msg: str) -> str | None:
    market_url = args_market_url or os.getenv("OPENJIUWEN_MARKET_URL")
    if not market_url:
        logger.error(err_msg)
        return None
    return market_url


def _cli_resolve_publish_auth(args) -> tuple[str | None, str | None, int]:
    user_token = args.user_token or os.getenv("OPENJIUWEN_USER_TOKEN")
    system_token = args.system_token or os.getenv("OPENJIUWEN_SYSTEM_TOKEN")
    has_user = bool(user_token and str(user_token).strip())
    has_sys = bool(system_token and str(system_token).strip())
    if has_user and has_sys:
        logger.error("publish supports exactly one auth method: use either --token or --system-token")
        return None, None, 1

    user_token = str(user_token).strip() if user_token else ""
    system_token = str(system_token).strip() if system_token else ""
    if system_token:
        return None, system_token, 0

    if not user_token:
        logger.error(
            "publish requires user token: pass --token or set OPENJIUWEN_USER_TOKEN "
            "(or use --system-token / OPENJIUWEN_SYSTEM_TOKEN)"
        )
        return None, None, 1
    return user_token, None, 0


def _cli_resolve_delete_auth(args) -> tuple[str | None, str | None, int]:
    user_token = args.user_token or os.getenv("OPENJIUWEN_USER_TOKEN")
    system_token = args.system_token or os.getenv("OPENJIUWEN_SYSTEM_TOKEN")

    has_user = bool(user_token and str(user_token).strip())
    has_sys = bool(system_token and str(system_token).strip())
    if has_user and has_sys:
        logger.error("delete supports exactly one auth method: use either --token or --system-token")
        return None, None, 1

    if has_sys:
        return None, str(system_token).strip(), 0

    if not has_user:
        logger.error(
            "delete requires user token: pass --token or set OPENJIUWEN_USER_TOKEN "
            "(or use --system-token / OPENJIUWEN_SYSTEM_TOKEN)"
        )
        return None, None, 1
    return str(user_token).strip(), None, 0


def _cli_resolve_patch_auth(args, action: str) -> tuple[str | None, str | None, int]:
    user_token = args.user_token or os.getenv("OPENJIUWEN_USER_TOKEN")
    system_token = args.system_token or os.getenv("OPENJIUWEN_SYSTEM_TOKEN")
    has_user = bool(user_token and str(user_token).strip())
    has_sys = bool(system_token and str(system_token).strip())
    if has_user and has_sys:
        logger.error("%s supports exactly one auth method: use either --token or --system-token", action)
        return None, None, 1
    if has_sys:
        return None, str(system_token).strip(), 0
    if not has_user:
        logger.error(
            "%s requires user token: pass --token or set OPENJIUWEN_USER_TOKEN "
            "(or use --system-token / OPENJIUWEN_SYSTEM_TOKEN)",
            action,
        )
        return None, None, 1
    return str(user_token).strip(), None, 0


def _cli_parse_patch_metadata(raw: str | None) -> dict | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata must be a valid JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("metadata must be a JSON object")
    return parsed


def _cli_resolve_patch_download_path(output: str | None, skill_asset_id: str, patch_version: str) -> Path:
    default_name = f"{skill_asset_id.strip()}-{patch_version.strip()}.zip"
    if not output:
        return Path.cwd().resolve() / default_name
    out = Path(output).resolve()
    if out.suffix.lower() == ".zip":
        return out
    return out / default_name


def handle_init(args) -> int:
    try:
        plugin_root = plugin_init(args.name, Path(args.path), force=args.force, plugin_type=args.plugin_type)
    except Exception as exc:
        _cli_log_command_failed("init", exc)
        return 1
    logger.info("plugin initialized at: %s", plugin_root)
    return 0


def handle_validate(args) -> int:
    result = plugin_validate(Path(args.path).resolve())
    if result.warnings:
        for warning in result.warnings:
            logger.warning("%s", warning)
    if result.errors:
        for err in result.errors:
            _cli_log_command_failed("validate", err)
        return 1
    logger.info("plugin validation passed")
    return 0


def handle_pack(args) -> int:
    try:
        plugin_root = Path(args.path).resolve()
        out_dir = (
            Path(args.output).resolve() if Path(args.output).is_absolute() else (plugin_root / args.output).resolve()
        )
        zip_path = plugin_pack(plugin_root, out_dir)
    except Exception as exc:
        _cli_log_command_failed("pack", exc)
        return 1
    logger.info("packed: %s", zip_path)
    return 0


def handle_publish(args) -> int:
    user_token, system_token, exit_code = _cli_resolve_publish_auth(args)
    if exit_code != 0:
        return exit_code

    market_url = _cli_resolve_market_url(
        args.market_url,
        err_msg="publish requires --market-url or OPENJIUWEN_MARKET_URL",
    )
    if not market_url:
        return 1
    if not args.file and not args.path:
        logger.error("path (plugin directory) is required when not using --file")
        return 1

    try:
        publish_input = PublishPluginInput(
            plugin_path=Path(args.path).resolve() if args.path else None,
            zip_path=Path(args.file).resolve() if args.file else None,
            plugin_id=args.plugin_id or None,
            plugin_version=args.plugin_version or None,
            version_desc=args.version_desc or None,
            force=args.force,
        )
        result = plugin_publish(
            market_url=market_url,
            user_token=user_token,
            system_token=system_token,
            publish_input=publish_input,
        )
    except PublishError as e:
        _cli_log_command_failed("publish", e.detail)
        return 1
    except ValueError as e:
        _cli_log_command_failed("publish", e)
        return 1

    logger.info(
        "published: plugin_id=%s name=%s version=%s status=%s",
        result.plugin_id,
        result.name,
        result.version,
        result.status,
    )
    logger.info(
        "提示：请保存上方 plugin_id，后续发新版本时需传 --plugin-id；若未保存可执行 openjiuwen-plugin search <关键词> 查询"
    )
    return 0


def handle_info(args) -> int:
    market_url = args.market_url or os.getenv("OPENJIUWEN_MARKET_URL")
    if not market_url:
        logger.error("info requires --market-url or OPENJIUWEN_MARKET_URL")
        return 1
    try:
        detail = plugin_info(market_url, args.asset_id, args.version)
    except Exception as exc:
        _cli_log_command_failed("info", exc)
        return 1

    logger.info("asset_id: %s", detail.asset_id or args.asset_id)
    for key in detail.__class__.model_fields:
        if key == "asset_id":
            continue
        val = getattr(detail, key)
        if val in (None, ""):
            continue
        if key == "tags":
            if isinstance(val, list) and val:
                logger.info("tags: %s", ", ".join(str(x) for x in val))
            continue
        logger.info("%s: %s", key, val)

    return 0


def handle_search(args) -> int:
    market_url = args.market_url or os.getenv("OPENJIUWEN_MARKET_URL")
    if not market_url:
        logger.error("search requires --market-url or OPENJIUWEN_MARKET_URL")
        return 1
    try:
        if args.page is not None and args.page < 1:
            logger.error("search --page must be >= 1")
            return 1
        if args.page_size is not None and (args.page_size < 1 or args.page_size > 100):
            logger.error("search --page-size must be between 1 and 100")
            return 1
        query = PluginListQuery(
            search_keyword=args.query or "",
            plugin_type=args.plugin_type,
            publisher_name=args.author,
            asset_id=args.search_asset_id,
            asset_type=args.search_asset_type,
            publisher_id=args.search_publisher_id,
            page=args.page or 1,
            page_size=args.page_size or 20,
            order_by=args.order_by or "install_count",
            desc=args.desc,
        )
        result = plugin_search(market_url, query)
        if not result.items:
            logger.info("no results.")
            return 0
        logger.info("page=%s page_size=%s total=%s", result.page, result.page_size, result.total)
        for item in result.items:
            aid = item.asset_id
            name = item.name
            ver = item.latest_version
            logger.info("  %s  %s  %s", aid, name, ver)
    except Exception as exc:
        _cli_log_command_failed("search", exc)
        return 1
    return 0


def handle_delete(args) -> int:
    market_url = _cli_resolve_market_url(
        args.market_url,
        err_msg="delete requires --market-url or OPENJIUWEN_MARKET_URL",
    )
    if not market_url:
        return 1
    try:
        user_token, system_token, exit_code = _cli_resolve_delete_auth(args)
        if exit_code != 0:
            return exit_code
        if system_token:
            plugin_delete(
                market_url,
                args.plugin_id,
                logger,
                version=args.version,
                system_token=system_token,
            )
        else:
            plugin_delete(
                market_url,
                args.plugin_id,
                logger,
                version=args.version,
                user_token=user_token,
            )
    except Exception as exc:
        _cli_log_command_failed("delete", exc)
        return 1
    return 0


def handle_install(args) -> int:
    market_url = _cli_resolve_market_url(
        args.market_url,
        err_msg="install requires --market-url or OPENJIUWEN_MARKET_URL",
    )
    if not market_url:
        return 1

    asset_id = str(args.asset_id).strip()
    plugin_version = (args.plugin_version or "").strip() or None

    fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="openjiuwen_dl_")
    os.close(fd)
    own_tmp = Path(tmp_name)
    zip_path = own_tmp

    extract_root = Path(args.output).resolve() if args.output else Path.cwd().resolve()
    try:
        dl_info = plugin_install_download(
            market_url,
            asset_id,
            zip_path,
            version=plugin_version,
        )
        if dl_info.verified:
            logger.info("download checksum verified: %s", dl_info.actual_checksum_sha256)
        else:
            logger.warning("server did not provide a checksum; skipped verification")
        installed = plugin_install(
            zip_path,
            extract_dir=extract_root,
            force=args.force,
        )
        logger.info("install finished, saved to: %s", installed.resolve())
    except Exception as exc:
        _cli_log_command_failed("install", exc)
        return 1
    finally:
        try:
            own_tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


def handle_skill_import(args) -> int:
    market_url = _cli_resolve_market_url(
        args.market_url,
        err_msg="skill-import requires --market-url or OPENJIUWEN_MARKET_URL",
    )
    if not market_url:
        return 1
    system_token = args.system_token or os.getenv("OPENJIUWEN_SYSTEM_TOKEN")
    if not system_token or not str(system_token).strip():
        logger.error("skill-import requires --system-token or OPENJIUWEN_SYSTEM_TOKEN")
        return 1

    path = Path(args.bundle_path).resolve()
    pack_tmp: Path | None = None
    try:
        if path.is_dir():
            pack_tmp = Path(tempfile.mkdtemp(prefix="oj_skill_bundle_pack_"))
            bundle = pack_tmp / "bundle.zip"
            try:
                plugin_pack_skill_bundle(path, bundle)
            except ValueError as e:
                _cli_log_command_failed("skill-import", f"pack directory: {e}")
                return 1
        elif path.is_file():
            bundle = path
            raw = bundle.stat().st_size
            if raw > SKILL_IMPORT_BUNDLE_MAX_BYTES:
                _cli_log_command_failed(
                    "skill-import",
                    f"bundle file too large: {raw} bytes (limit {SKILL_IMPORT_BUNDLE_MAX_BYTES})",
                )
                return 1
        else:
            _cli_log_command_failed("skill-import", f"bundle zip or directory not found: {path}")
            return 1

        checksum = sha256_file_hex(bundle)
        try:
            result = skill_import(
                market_url,
                str(system_token).strip(),
                zip_path=bundle,
                checksum_sha256=checksum,
                force=bool(args.force),
                fail_fast=bool(args.fail_fast),
            )
        except PublishError as e:
            _cli_log_command_failed("skill-import", e.detail)
            return 1

        s = result.summary
        logger.info("skill-import summary: total=%s ok=%s failed=%s", s.total, s.ok, s.failed)
        for item in result.results:
            if item.status == "ok":
                logger.info(
                    "  ok entry=%s plugin_id=%s name=%s version=%s",
                    item.entry,
                    item.plugin_id,
                    item.name,
                    item.version,
                )
            else:
                logger.error(
                    "  fail entry=%s error=%s message=%s",
                    item.entry,
                    item.error,
                    item.message,
                )

        return 1 if s.failed else 0
    finally:
        if pack_tmp is not None:
            shutil.rmtree(pack_tmp, ignore_errors=True)


def _handle_patch_publish_or_update(args, *, update: bool) -> int:
    action = "patch update" if update else "patch publish"
    user_token, system_token, exit_code = _cli_resolve_patch_auth(args, action)
    if exit_code != 0:
        return exit_code
    market_url = _cli_resolve_market_url(
        args.market_url,
        err_msg=f"{action} requires --market-url or OPENJIUWEN_MARKET_URL",
    )
    if not market_url:
        return 1

    source = Path(args.path).resolve()
    pack_tmp: Path | None = None
    try:
        metadata = _cli_parse_patch_metadata(args.metadata)
        if source.is_dir():
            pack_tmp = Path(tempfile.mkdtemp(prefix="openjiuwen_patch_publish_"))
            zip_path = plugin_pack(source, pack_tmp)
        elif source.is_file():
            zip_path = source
        else:
            _cli_log_command_failed(action, f"Skill plugin directory or zip file not found: {source}")
            return 1

        checksum = sha256_file_hex(zip_path)
        patch_version = args.patch_version if getattr(args, "patch_version", None) else None
        req = SkillPatchPublishRequest(
            zip_path=zip_path,
            checksum_sha256=checksum,
            skill_asset_id=args.skill_asset_id,
            patch_version=patch_version,
            source_skill_version=args.source_skill_version,
            version_desc=args.version_desc,
            patch_type=args.patch_type,
            metadata=metadata,
            force=True if update else bool(args.force),
        )
        result = skill_patch_upload(
            market_url,
            user_token,
            system_token,
            req,
            update=update,
        )
    except PublishError as e:
        _cli_log_command_failed(action, e.detail)
        return 1
    except ValueError as e:
        _cli_log_command_failed(action, e)
        return 1
    finally:
        if pack_tmp is not None:
            shutil.rmtree(pack_tmp, ignore_errors=True)

    logger.info(
        "%s: skill_asset_id=%s patch_id=%s patch_version=%s status=%s",
        "patch updated" if update else "patch published",
        result.skill_asset_id,
        result.patch_id,
        result.patch_version,
        result.status,
    )
    logger.info("storage_url: %s", result.storage_url)
    return 0


def handle_patch(args) -> int:
    patch_command = getattr(args, "patch_command", None)
    if patch_command in {"publish", "update"}:
        return _handle_patch_publish_or_update(args, update=patch_command == "update")

    if patch_command == "list":
        market_url = _cli_resolve_market_url(
            args.market_url,
            err_msg="patch list requires --market-url or OPENJIUWEN_MARKET_URL",
        )
        if not market_url:
            return 1
        try:
            if args.page < 1:
                logger.error("patch list --page must be >= 1")
                return 1
            if args.page_size < 1 or args.page_size > 100:
                logger.error("patch list --page-size must be between 1 and 100")
                return 1
            result = skill_patch_list(
                market_url,
                args.skill_asset_id,
                page=args.page,
                page_size=args.page_size,
                status=args.patch_status,
            )
        except Exception as exc:
            _cli_log_command_failed("patch list", exc)
            return 1
        logger.info("page=%s page_size=%s total=%s", result.page, result.page_size, result.total)
        if not result.items:
            logger.info("no skill patches.")
            return 0
        for item in result.items:
            logger.info(
                "  %s  source=%s  status=%s  patch_id=%s",
                item.patch_version,
                item.source_skill_version or "-",
                item.status or "-",
                item.patch_id,
            )
        return 0

    if patch_command == "info":
        market_url = _cli_resolve_market_url(
            args.market_url,
            err_msg="patch info requires --market-url or OPENJIUWEN_MARKET_URL",
        )
        if not market_url:
            return 1
        try:
            detail = skill_patch_info(market_url, args.skill_asset_id, args.patch_version)
        except Exception as exc:
            _cli_log_command_failed("patch info", exc)
            return 1
        for key in detail.__class__.model_fields:
            val = getattr(detail, key)
            if val in (None, ""):
                continue
            if key == "metadata" and isinstance(val, dict):
                logger.info("%s: %s", key, json.dumps(val, ensure_ascii=False, sort_keys=True))
                continue
            logger.info("%s: %s", key, val)
        return 0

    if patch_command == "download":
        market_url = _cli_resolve_market_url(
            args.market_url,
            err_msg="patch download requires --market-url or OPENJIUWEN_MARKET_URL",
        )
        if not market_url:
            return 1
        dest = _cli_resolve_patch_download_path(args.output, args.skill_asset_id, args.patch_version)
        try:
            dl_info = skill_patch_download_zip(market_url, args.skill_asset_id, args.patch_version, dest)
        except Exception as exc:
            _cli_log_command_failed("patch download", exc)
            return 1
        if dl_info.verified:
            logger.info("download checksum verified: %s", dl_info.actual_checksum_sha256)
        else:
            logger.warning("server did not provide a checksum; skipped verification")
        logger.info("patch downloaded: %s", dest.resolve())
        return 0

    if patch_command == "delete":
        market_url = _cli_resolve_market_url(
            args.market_url,
            err_msg="patch delete requires --market-url or OPENJIUWEN_MARKET_URL",
        )
        if not market_url:
            return 1
        user_token, system_token, exit_code = _cli_resolve_patch_auth(args, "patch delete")
        if exit_code != 0:
            return exit_code
        try:
            result = skill_patch_delete(
                market_url,
                args.skill_asset_id,
                args.patch_version,
                user_token=user_token,
                system_token=system_token,
            )
        except Exception as exc:
            _cli_log_command_failed("patch delete", exc)
            return 1
        logger.info("patch deleted: skill_asset_id=%s patch_version=%s", result.skill_asset_id, result.patch_version)
        return 0

    logger.error("patch requires subcommand: publish/update/list/info/download/delete")
    return 1


COMMAND_HANDLERS = {
    "init": handle_init,
    "validate": handle_validate,
    "pack": handle_pack,
    "publish": handle_publish,
    "info": handle_info,
    "search": handle_search,
    "delete": handle_delete,
    "install": handle_install,
    "skill-import": handle_skill_import,
    "patch": handle_patch,
}
