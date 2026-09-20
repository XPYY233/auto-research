# 第三方版权与许可

本仓库的版权声明仅覆盖原创贡献。下列组件各自保留版权及许可，本项目不将其重新许可。完整依赖版本以哈希锁文件为准。

| 组件 | 已核对的许可信息 | 用途 |
| --- | --- | --- |
| PyMuPDF / MuPDF | GNU AGPL v3 或 Artifex 商业许可 | PDF 读取与原图提取 |
| pywebview | BSD-3-Clause | Mac 嵌入式界面 |
| cryptography | Apache-2.0 OR BSD-3-Clause | 本机加密 |
| Apache Arrow / pyarrow | Apache-2.0 | 表格数据与导出 |
| requests | Apache-2.0 | 网络请求 |
| PyYAML | MIT | 配置解析 |
| Pydantic | MIT | 数据验证 |
| DeepSeek Harness SDK / runtime-bin | 构建依赖元数据声明 MIT | 有界 AI 工具运行时 |
| PyInstaller | GPLv2-or-later，附构建产物分发例外 | 构建工具 |

以上依据当前开发/候选构建所安装的包元数据；不是全部原生组件的 SBOM，也不是完整分发许可结论。打包器会保留依赖自带的 LICENSE/NOTICE/COPYING，并生成 dependency-inventory.json。分发前仍需核对传递依赖、原生运行时、例外条款和对应源码义务。

PyMuPDF 的官方许可说明：[PyMuPDF](https://github.com/pymupdf/PyMuPDF#license)。GNU 许可原文和项目许可设置说明见 [GNU licenses](https://www.gnu.org/licenses/) 与 [How to use GNU licenses](https://www.gnu.org/licenses/gpl-howto.html)。本项目未购买或宣称持有商业许可。

论文 PDF、出版表图、研究数据库和私人资料包不在公开源码中，亦不属于上述软件许可授予的内容。第三方名称与商标仅用于说明依赖关系。
