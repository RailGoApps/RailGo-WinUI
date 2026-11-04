# RailGPT v3.0.0 / 正式发布

## 中文

RailGPT v3.0.0 是本地优先铁路智能助手的正式稳定版。它在 v2 可观测执行引擎之上，完成了前级语义路由、结构化上下文、Memory OS、能力合同和工具证据门禁的升级。

### 重点更新

- 支持 DeepSeek、OpenAI（ChatGPT）与任意 OpenAI-compatible 服务商。
- Settings 提供主 Key、Thinker Key、三种 Agent 模式的独立模型配置，以及自定义兼容端点。
- API Key 继续由 Windows DPAPI 按当前用户加密保存；安装包与仓库均不携带用户密钥。
- `FAST-GO` 保持低延迟，`FAST-PLUS` 与 `DEEP` 保持推理优先；模型变更对下一次请求即时生效。
- 改进 AgentContextPackage、活动主题帧、日期裁决、工具能力合同、RailGo 运营缓存与事实证据隔离。
- 发布 Windows 便携版与支持自选安装目录、桌面快捷方式和卸载的正式安装包。

### 下载说明

- `RailGPT_v3.0.0_Setup_x64.exe`：推荐普通 Windows 用户使用。
- `RailGPT_v3.0.0_win64.zip`：免安装便携版。

首次启动后，请在 Settings > API 中配置自己的服务商、API Key 和模型。外部数据的覆盖、时效和可用性由相应服务提供方决定。

## English

RailGPT v3.0.0 is the formal stable release of the local-first railway AI assistant. It retains the observable v2 execution engine and upgrades semantic routing, structured context, the Memory OS, capability contracts, and tool-evidence gates.

### Highlights

- DeepSeek, OpenAI (ChatGPT), and arbitrary OpenAI-compatible providers are supported.
- Settings offers primary and thinker keys, independent model configuration for all three Agent modes, and a custom compatible endpoint.
- API keys remain encrypted with current-user Windows DPAPI; neither the repository nor the installer contains user credentials.
- `FAST-GO` remains latency-oriented, while `FAST-PLUS` and `DEEP` remain reasoning-oriented. Model changes apply to subsequent requests without a restart.
- AgentContextPackage, active topic frames, date resolution, capability contracts, RailGo operational caching, and fact-evidence isolation are improved.
- A Windows portable package and a professional installer with selectable installation directory, desktop shortcut, and uninstaller are provided.

### Downloads

- `RailGPT_v3.0.0_Setup_x64.exe`: recommended for standard Windows installation.
- `RailGPT_v3.0.0_win64.zip`: portable no-install build.

After first launch, configure your own provider, API key, and model IDs in Settings > API. Coverage, freshness, and uptime of external data are controlled by their respective providers.
