from __future__ import annotations

JS_TS_NETWORK_SINK_PATTERN_SOURCE = (
    r"fetch"
    r"|axios(?:\.(?:get|post|put|delete|patch))?"
    r"|https?\.request"
    r"|undici\.request"
    r"|new\s+WebSocket"
    r"|[A-Za-z_$][\w$]*\.goto"
)
PYTHON_NETWORK_CLIENT_PATTERN_SOURCE = r"requests|httpx|aiohttp|urllib\.request"
PYTHON_NETWORK_METHOD_PATTERN_SOURCE = r"get|post|put|delete|patch|request|urlopen"
URL_TRANSFER_COMMAND_PATTERN_SOURCE = r"curl|wget|Invoke-WebRequest"
GIT_CLONE_COMMAND_PATTERN_SOURCE = r"git\s+clone"
CODE_NETWORK_SINK_PATTERN_SOURCE = (
    rf"(?:{JS_TS_NETWORK_SINK_PATTERN_SOURCE}|"
    rf"(?:{PYTHON_NETWORK_CLIENT_PATTERN_SOURCE})\.(?:{PYTHON_NETWORK_METHOD_PATTERN_SOURCE}))"
)
ENDPOINT_COMMAND_PATTERN_SOURCE = (
    rf"(?:{URL_TRANSFER_COMMAND_PATTERN_SOURCE}|{GIT_CLONE_COMMAND_PATTERN_SOURCE})"
)
