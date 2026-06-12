# skillhub/cli

本目录包含两条可独立安装的 CLI，以及共享实现 `cli_core/`（随各自 wheel 打包，不单独发布）。

| 目录 | 发行名 / 命令 | 说明 |
|------|---|---|
| `openjiuwen_plugin/` | `openjiuwen-plugin` | openJiuwen 插件市场 CLI（`tools` / `mcp-stdio` / `restful-api` / `skill`） |
| `jiuwen_teamskills/` | `jiuwen-teamskills` | TeamSkills Hub CLI（默认 `swarmskill`，兼容 `skill`） |
| `cli_core/` | - | 两条 CLI 共用的参数解析、校验、打包、市场请求逻辑 |

## 开发安装

```bash
cd skillhub/cli/openjiuwen_plugin
pip install -e .

cd ../jiuwen_teamskills
pip install -e .
```

安装后可分别执行：

- `openjiuwen-plugin -h`
- `jiuwen-teamskills -h`

## 测试

在各自子目录执行：

```bash
pytest
```

## 构建 wheel

在 `skillhub/cli/` 下执行：

```bash
pip wheel ./openjiuwen_plugin
pip wheel ./jiuwen_teamskills
```

可按需追加 `--no-deps`、`-w dist` 等参数。
