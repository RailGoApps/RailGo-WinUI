# 本机开发环境

验证日期：2026-11-04。源码：`D:\learn_pytorch\RailGo-WinUI`。

## 已配置

- .NET SDK 8.0.427：`D:\devtools\dotnet`，安装包通过微软公布的 SHA-512 校验。
- Windows SDK 10.0.26100.0：沿用已有的 `D:\Windows Kits\10`。
- WebView2 Runtime 156.0.4314.58：沿用已有安装。
- Python 3.12.13：独立环境 `D:\devtools\venvs\railgo`，按仓库 `requirements-dev.txt` 和约束文件安装。PyTorch 为 2.13.0+cpu，不修改已有 CUDA 环境。
- 用户环境变量配置了 `DOTNET_ROOT`、SDK PATH 和 `RAILGPT_DEV_PYTHON`。现有终端需要重新打开，或执行下面的环境脚本。

双击 `D:\learn_pytorch\RailGo-开发终端.cmd`，或在 PowerShell 执行：

```powershell
. D:\devtools\railgo-env.ps1
& D:\devtools\railgo-build.ps1
```

脚本构建 x64、Debug、无 MSIX 包的版本，包含 Windows App SDK 文件。只为本次构建指定 `RuntimeIdentifiers=win-x64`，保留项目本身的其他架构设置。

Visual Studio 自带的 MSBuild 当前缺少 .NET SDK 解析组件，专用环境脚本通过 `MSBuildSDKsPath` 指定 SDK，并关闭本终端中的工作负载解析。已验证该 MSBuild 可读取项目的 .NET 8 SDK 属性；没有验证 IDE 内的设计器、F5 调试或 MSIX 发布。

## 已执行的检查

- x64 Debug 主程序构建：0 个错误、334 个警告。警告包含可空引用、缺少 await 和 MVVM 生成器提示，详见构建日志。
- `pip check`：没有损坏的依赖。
- PyTorch 矩阵运算和 sentence-transformers、OpenAI SDK、pywebview、PyInstaller 导入成功。
- 独立 Python 后端启动，状态接口及嵌入页面均返回 HTTP 200。
- 原生 RailGo 窗口正常响应，自动启动 RailGPT 3.0.0 后端。
- 真实 Named Pipe 查询 `station.search`，关键词“北京”，返回 9 条结果。
- 关闭原生窗口后，测试创建的后端启动器、工作进程退出，5033 端口释放。

构建产物：

```text
RailGo\bin\x64\Debug\net8.0-windows10.0.26100.0\win-x64\RailGo.exe
```

通过 `D:\learn_pytorch\RailGo-运行.cmd` 启动会设置开发模式 Python 路径。单独双击 EXE 时，需确保它继承了 `RAILGPT_DEV_PYTHON`。

检查记录：`artifacts/dev-setup/debug-build-x64.log`、`python-imports.log`、`backend-smoke.json`、`native-smoke.json`、`native-pipe-result.json`、`shutdown-check.json`。

未执行 Release Runtime 打包、MSIX 签名或安装测试。此前交付的 PDF 和 ZIP 保留了当时的测试记录，本次新增结果以这里和当前日志为准。

安装方式参考微软文档：<https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-install-script>。
