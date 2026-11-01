# 股神盯盘助手 · Screen Stocks Assistant

面向《桌面股神 / Screen Stocks》的 Windows 本地盯盘工具：实时行情、持仓与资金图表、排期详情、模拟账户，以及可手动启停的游戏自动交易。

**下载：** [Windows 便携版 Release](https://github.com/cfy114514/screen-stocks-assistant/releases/latest)

**首次使用：** [中文启用教程](docs/启用教程.md)

**策略说明：** [当前规则与限制](docs/交易规则.md)

这是非官方实验工具。已针对游戏 **1.0.13** 的本地 Mod 文件接口验证；其他版本不会启动实际自动交易。它不直连游戏服务器，不修改金币或存档数值，也不包含游戏文件或反编译代码。模拟结果、排期目标价均不保证实际收益。

## 快速开始

1. 退出游戏，备份并编辑以下文件：

   ```text
   %USERPROFILE%\AppData\LocalLow\Conradical Games\Screen Stocks\thesmallshort_online_settings.sav
   ```

2. 在 `SettingsData.value` 下，把 `"modSupport": false` 改成 `"modSupport": true`，保留其他内容。
3. 启动游戏并进入在线市场，等待生成 `mods\export\market.json`、`history.json`。
4. 从 Release 下载 `ScreenStocksAssistant-v0.1.1-windows-x64.zip`，解压后双击 `ScreenStocksAssistant.exe`。不需要安装 Python。
5. 浏览器自动打开本地控制页。**默认不启动实际交易**；确认规则后，才能点击“启动自动交易”。

更完整的路径、文件内容示例和故障排查见 [启用教程](docs/启用教程.md)。

## 功能

- 行情约每 0.5 秒读取，冷却显示约每 0.25 秒更新；底层数据仍受游戏导出频率限制。
- 游戏现金、净资产、持仓表，多空持仓规模图，约每 10 秒采样的资金曲线。
- 可折叠的交易日志与模拟日志。界面展示最近 100 条，本地文件保留完整记录。
- 排期执行时间可点击，分别展示公告时间、北京时间执行时间、倒计时、来源状态和近期价格。
- 独立模拟账户；实际交易通过本地 `mods\commands` 文件由游戏执行。
- 成交回报、持仓变化双重确认；不明确的指令不会自动重复发送。

## 启停与数据

- “停止自动交易”停止外置程序的新指令，已发出的指令仍需等待游戏处理。
- **关闭浏览器标签页不会停止交易。** 使用“退出助手”会先停止交易进程，再关闭本地网页服务。
- 游戏内自动操盘槽位由游戏管理，本工具的停止按钮不控制它们。
- 再次双击 EXE 会打开已经运行的控制页，不会自动开启第二份交易进程。
- 配置、资金采样与交易记录保存在 `%LOCALAPPDATA%\ScreenStocksAssistant\`，不会上传至 GitHub。
- 不需要 GitHub 登录、API 密钥或联网 AI 服务。网页只监听本机 `127.0.0.1`。

## 默认股票分工

默认把 `$BANK`、`$BUBL` 留给游戏内槽位，外置程序不会向这两只股票发单。**下载程序不会自动创建游戏内槽位。** 可参考教程设置，也可退出助手后在 `config.json` 中调整 `game_managed_stocks`。不要让两套自动策略控制同一股票。

## 从源码运行

Windows 10/11 x64、Python 3.13。运行部分仅使用 Python 标准库，无需 `pip install`：

```powershell
python server.py
```

测试使用临时目录和虚构市场，不需要运行游戏：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

## 构建 Windows 便携版

在 Windows x64 上：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

输出为 `dist\ScreenStocksAssistant.exe` 和 `artifacts\ScreenStocksAssistant-v0.1.1-windows-x64.zip`。构建不读取游戏或个人交易数据。CI 也会运行测试并构建 Windows 包。

## 说明

本项目由实际使用中逐步整理而来，策略是可审查的试运行规则，不是经过收益回测的盈利承诺。模拟账户与当前真实游戏策略并不完全相同，不能拿模拟收益当作实际策略的回测结果。报告问题时请删除个人目录和不想公开的交易数据。

MIT License；《Screen Stocks》的名称和游戏资产属于其各自权利人。
