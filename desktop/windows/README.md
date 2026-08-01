# Auto Research Windows 客户端

这里是 Windows 正式用户端的独立平台目录。当前版本 `0.1.0-dev.1` 只包含数据目录与 Windows Credential Manager 地基，尚未生成可交付安装包。

## 最终用户验收路径

1. 在干净 Windows 电脑上安装；
2. 导入签名 `.aresearch` 官方资料包；
3. 无 API key 完成离线搜索与证据查看；
4. 配置用户自己的 DeepSeek key；
5. 上传真实 PDF 并完成质量抽取；
6. 重启后确认官方包、私人库和加密对话均保持隔离；
7. 升级和回退 App/资料包，确认私人数据不被覆盖；
8. 卸载后按用户选择保留或删除私人数据。

## Mac 上可做与不可做

Mac 可以验证纯路径契约、凭据适配逻辑、Python 静态检查和共享核心接口；不能代替 Windows Credential Manager、WebView2、安装器、文件占用、Defender/SmartScreen、升级和卸载实测。

当前轻量测试：

```bash
python3 -m unittest discover -s desktop/windows/tests -p 'test_*.py'
```

只要并行任务仍在修改兼容接口，或者共享核心尚未宣布冻结，本目录不得向用户输出“稳定安装包”。
