"""restful-api 插件：zip 目录布局、contract 校验与抽取。"""

from __future__ import annotations

import zipfile
from typing import Any

from plugins_market.validation.base import raise_invalid_config, raise_invalid_structure
from plugins_market.validation.types.tools import validate_tools_json
from plugins_market.validation.zip_utils import (
    DecompressCounter,
    has_src_tree,
    safe_read_zip_member,
    validate_png_icon_bytes,
)

TOOLS_SCHEMA_PATH = "schemas/tools.json"
_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
_ALLOWED_SEND_METHODS = {"None", "Header", "Query", "Body", "Path"}


def _parse_restful_tools_json(raw_bytes: bytes) -> list[tuple[int, dict[str, Any]]]:
    return list(enumerate(validate_tools_json(raw_bytes)))


def _validate_restful_input_schema(path_prefix: str, tool_path: str, input_schema: dict[str, Any]) -> None:
    properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
    required = input_schema.get("required", [])
    if required is not None and not isinstance(required, list):
        raise_invalid_config(f"{path_prefix}.input_schema.required 必须为数组")

    for required_name in required or []:
        if not isinstance(required_name, str):
            raise_invalid_config(f"{path_prefix}.input_schema.required 项必须为字符串")
        if required_name not in properties:
            raise_invalid_config(f"{path_prefix}.input_schema.required 包含未知字段: {required_name}")

    for param_name, param in properties.items():
        if not isinstance(param, dict):
            raise_invalid_config(f"{path_prefix}.input_schema.properties.{param_name} 必须为对象")
        send_method = param.get("send_method")
        if send_method is not None and (not isinstance(send_method, str) or send_method not in _ALLOWED_SEND_METHODS):
            raise_invalid_config(f"{path_prefix}.input_schema.properties.{param_name}.send_method 非法")
        description = param.get("description")
        if description is not None and (not isinstance(description, str) or not description.strip()):
            raise_invalid_config(f"{path_prefix}.input_schema.properties.{param_name}.description 非法")
        if "{" + param_name + "}" in tool_path and send_method not in ("Path", "path"):
            raise_invalid_config(f"{path_prefix}.input_schema.properties.{param_name}.send_method 必须为 Path")
        if str(send_method or "") in ("Path", "path") and ("{" + param_name + "}" not in tool_path):
            raise_invalid_config(
                f"{path_prefix}.input_schema.properties.{param_name}.send_method "
                "为 Path 但 path 中不存在该参数"
            )


def _validate_restful_output_schema(path_prefix: str, output_schema: dict[str, Any]) -> None:
    output_properties = output_schema.get("properties") if isinstance(output_schema.get("properties"), dict) else {}
    for param_name, param in output_properties.items():
        if not isinstance(param, dict):
            raise_invalid_config(f"{path_prefix}.output_schema.properties.{param_name} 必须为对象")
        description = param.get("description")
        if description is not None and (not isinstance(description, str) or not description.strip()):
            raise_invalid_config(f"{path_prefix}.output_schema.properties.{param_name}.description 非法")


def _validate_restful_headers(path_prefix: str, headers: Any) -> None:
    if headers is None:
        return
    if not isinstance(headers, list):
        raise_invalid_config(f"{path_prefix}.headers 必须为数组")
    for idx, header in enumerate(headers):
        header_prefix = f"{path_prefix}.headers[{idx}]"
        if not isinstance(header, dict):
            raise_invalid_config(f"{header_prefix} 必须为对象")
        if not isinstance(header.get("name"), str) or not str(header.get("name") or "").strip():
            raise_invalid_config(f"{header_prefix}.name 必填")
        if not isinstance(header.get("value"), str):
            raise_invalid_config(f"{header_prefix}.value 必须为字符串")


def validate_restful_api_tools_json(validated_tools: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    for i, tool in validated_tools:
        path_prefix = f"schemas/tools.json tools[{i}]"
        tool_path = tool.get("path")
        if not isinstance(tool_path, str) or not tool_path.strip():
            raise_invalid_config(f"{path_prefix}.path 必填")

        method = tool.get("method")
        if not isinstance(method, str) or method.upper() not in _ALLOWED_METHODS:
            raise_invalid_config(f"{path_prefix}.method 非法")

        input_schema = tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {}
        output_schema = tool.get("output_schema") if isinstance(tool.get("output_schema"), dict) else {}
        _validate_restful_input_schema(path_prefix, tool_path, input_schema)
        _validate_restful_output_schema(path_prefix, output_schema)
        _validate_restful_headers(path_prefix, tool.get("headers", []))

    return [tool for _, tool in validated_tools]


def extract_restful_api_contract(
    yaml_data: dict[str, Any],
    zf: zipfile.ZipFile,
    layout: dict[str, Any],
    counter: DecompressCounter,
) -> dict[str, Any]:
    api = yaml_data.get("api")
    if not isinstance(api, dict):
        raise_invalid_config("plugin.yaml api must be object for restful-api type")
    base_url = str(api.get("base_url") or "").strip()
    if not base_url:
        raise_invalid_config("plugin.yaml api.base_url 必填")

    tools_raw = safe_read_zip_member(zf, layout["tools_json_path"], counter)
    validated_tools = _parse_restful_tools_json(tools_raw)
    tools = validate_restful_api_tools_json(validated_tools)
    return {
        "api_prefix": base_url,
        "tools": tools,
        "header_configuration": api.get("default_headers") or {},
    }


def validate_restful_api_layout(
    zf: zipfile.ZipFile,
    prefix: str,
    counter: DecompressCounter,
) -> dict:
    """校验 restful-api 包根目录：README、可选 icon.png、非空 src/、schemas/tools.json。"""
    names = set(zf.namelist())

    readme_path = prefix + "README.md"
    if readme_path not in names:
        raise_invalid_structure("插件包结构不符合要求：restful-api 类型缺少 README.md")

    icon_path = prefix + "icon.png"

    if not has_src_tree(names, prefix):
        raise_invalid_structure("插件包结构不符合要求：restful-api 类型缺少 src/ 目录")

    tools_json_path = prefix + TOOLS_SCHEMA_PATH
    if tools_json_path not in names:
        raise_invalid_structure("插件包结构不符合要求：restful-api 类型缺少 schemas/tools.json 文件")

    if icon_path in names:
        icon_bytes = safe_read_zip_member(zf, icon_path, counter)
        validate_png_icon_bytes(icon_bytes, path=icon_path)
    else:
        icon_bytes = b""

    return {
        "icon_path": icon_path if icon_path in names else "",
        "icon_bytes": icon_bytes,
        "readme_path": readme_path,
        "tools_json_path": tools_json_path,
    }
