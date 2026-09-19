# ADR 0003：候选制品身份与构建边界

状态：实施中，2026-09-19，关联 #4。

构建入口仍为 `desktop/macos/build_app.command`。只接受干净 Git 状态、Apple Silicon 和 Python 3.14.3；每次新建环境，先按哈希安装构建工具，再按哈希安装完整应用依赖。不复用可变虚拟环境，不升级到未锁定版本。唯一的源码分发依赖 proxy_tools 使用已锁定的 setuptools/wheel 构建，关闭构建隔离隐式下载。

每次候选分配新的 candidate_id，保存在清单及独立输出目录。build55 是本次源码版本序号，candidate_id 是每份二进制的独立身份。原 build54 不覆盖；dist 的便捷链接只指向最新成功候选，旧候选目录仍保留。构建失败不得切换链接。

清单记录 Git 提交、依赖锁哈希、发布契约哈希、Python、实际构建系统、签名和公证状态。App 附构建环境依赖及上游许可证/第三方声明；它们不是完整原生二进制 SBOM，也不等于完成分发许可审查。外部对应源码归档来自同一 Git 提交。

签名完成后运行自包含冒烟，再为 App 生成逐文件和符号链接清单。制作 DMG 前与复制到暂存区后都核验同一清单；DMG 名含 candidate_id，已存在则拒绝覆盖。候选验收后封装同一 App，禁止重新构建替换。

目前只验证本机 macOS 26.3.1，最低系统声明收紧到此版本。自动检查在 macOS 15 上通过不能替代冻结 App 的系统兼容验收。第二台 Mac、安装界面、升级/回退和卸载保留数据仍须分别取得证据。

许可仍有分发门槛：PyMuPDF/MuPDF 的 AGPL 或商业授权，以及完整对应源码/第三方原生依赖义务需要在组内正式分发前收口。Harness SDK/runtime 轮子声明 MIT，并携带第三方声明，构建时原样保留。当前候选不声明 public_distribution_ready，不购买授权、不公开仓库。

依据：[PyMuPDF 官方许可](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)、[Harness SDK 项目元数据](https://pypi.org/project/deepseek-harness-sdk/0.1.1rc1/)以及本地下载轮子的 METADATA/LICENSE/THIRD_PARTY_NOTICES。
