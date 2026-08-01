# macOS 0.3.0 开发预览交接单

更新时间：2026-08-01

## 这版是什么

`0.3.0-preview.1` 是当前 Mac 上的稳定演示候选，不是最终公开发行版。本阶段不开发 Windows，但路径、资料包、私人库和安全凭据边界不锁死在 Mac。

- App：`desktop/macos/dist/Auto Research.app`
- DMG：`desktop/macos/dist/Auto-Research-0.3.0-preview.1-macOS-arm64.dmg`
- 核心来源：`9aa461a feat(evidence): add signed librarian briefs and desktop product contract`
- 核心产品声明：`2026.07.30-librarian-brief-stable.1`
- 证据 schema：v12
- 应用标识：`com.researcher.autoresearch`
- 最低 macOS：13.0

生成物的最终 SHA、测试数和实机结论由本次收口任务报告；源码提交和 tag 是可复现边界，`.app`、DMG 和生产资料不进入 Git。

## 本轮解决的问题

- 用户只打开个人科研工作台 App；浏览器、导师只读和 ngrok 退出产品入口。
- 图书管理员历史跨重启保存，并与科学 SQLite 分离。
- 不再读取或删除旧 Keychain 历史项，避免 ad-hoc 重建后索要“登录钥匙串密码”。
- 当前预览采用 AES-256-GCM + Application Support 私有随机密钥；正式签名发行再切回 OS secure credential store。
- 新增签名研究简报导出与应用图标。
- 记录 `.aresearch` 数据包、用户私人库、无密钥离线搜索和 BYOK 引导路线。

## 你怎样验收

1. 双击 `Auto Research.app`，确认没有钥匙串密码框；
2. 搜索一个熟悉的词，打开一条原文或 PDF；
3. 运行一次图书管理员问题并下载研究简报；
4. 关闭再打开 App，确认刚才的对话还在；
5. 关闭窗口后，确认 App 退出。

若 AI 没有配置，离线搜索仍应正常；API 密钥是 AI 服务通行证，不是 Auto Research 密码。

## 以后每次怎样更新

1. 功能对话先说明改动、测试、数据库影响并提交；
2. 确认没有另一个对话继续写同一批文件；
3. 关闭正在运行的 Auto Research；
4. 双击 `更新桌面版.command`；
5. 更新器备份数据库、做一次联合检查、构建一次候选并保留上一版；
6. 你按上面的五步验收。

更新器因未提交文件或临时服务停止时，不要输入密码、不要强制删除文件，把提示交给 Codex。

## 仍未完成的正式产品前提

- 当前 App 仍依赖开发机项目目录；
- `.aresearch` 导入器、私人库和无密钥首启流程尚未实现；
- 用户 API 密钥设置向导尚未实现；
- 未完成 Developer ID 签名、公证和干净电脑安装验证；
- 正式可分享语料必须逐项完成版权和隐私审查；
- App 稳定、语料完整和科学准确仍是三个不同结论。
