# 隔离目标的源码构建、安装与验证

这是受审目标的隔离测试流程，不是跳过运行时校验的安装开关。默认生产源码保持不变。
新服务器必须在自己的解释器、SQLite 绑定和数据盘上重新完成资格流程；不能复制云端
的历史回执、运行时补丁或数据库来获得资格。除既有 Linux built-in `_sqlite3` 绑定外，
现支持一种严格限定的动态绑定：Linux ELF64、小端 x86-64，使用全局、无版本的
受支持 SQLite 函数导入及重定位。其他动态形式仍会被拒绝；默认生产资格白名单不变，
不会因为观察成功而自动启用。具体映射、符号来源和完整性检查见
[`DYNAMIC_SQLITE_RUNTIME_IDENTITY.md`](DYNAMIC_SQLITE_RUNTIME_IDENTITY.md)。
这不是通用 Linux 或独立服务器验收结论；每个目标主机、运行时和数据盘都须单独验证。

## 1. 固定源码和目标

先按仓库 `START_HERE_ZH.md` 恢复源码并保存该提交内的 `SOURCE_MANIFEST.json`。
使用最终准备运行服务的解释器、独立私有目录和选定数据盘，依次执行
`qualification/TARGET_RUNTIME_OBSERVATION.md` 中的目标观察、物理崩溃恢复和独立
字节审计，再运行明确提供全部证据的九项候选校验。不要把退出成功或单个 JSON
布尔值当成自动批准。保留真实失败、原始结果和测试 JUnit。

独立审查后，按 `qualification/MATERIALIZE_TARGET_CANDIDATE.md` 生成并核验明确的
受审 basis。`materialize_target_candidate.py` 接收其精确摘要，只向全新目录复制
已验证源码，并仅替换身份/证据字面值。输出 `source/`、`candidate.patch` 和
`RECEIPT.json`。原始源码、验证器、容量边界和已有数据库不变。

## 2. 构建和安装独立 wheel

在独立构建副本中工作，保留 materializer 输出不变。所有命令中的 `python` 必须是
已完成目标资格的同一个解释器，不能安装后随意移动它或替换 SQLite 包。
运行依赖应按所选 Linux/Python 配置的 hash 锁文件准备；安装锁文件不等于通过资格。
`requirements-linux-py313-runtime.lock.txt` 固定当前 Linux/Python 3.13 配置的
运行依赖 wheel 哈希；它不替代构建工具锁文件，也不能用于任意 Python ABI/平台。
当前锁文件针对相应云端配置，不是跨平台通用依赖承诺。

```sh
python -I -B -m pip install --require-hashes -r source/requirements-build-dev9.lock.txt
cp -a source wheel-input
python -I -B -m pip wheel --no-build-isolation --no-deps --wheel-dir wheels wheel-input
python -I -B -m pip install --no-deps --no-compile wheels/long_audio_backend-0.2.0rc3.dev66-py3-none-any.whl
```

使用全新隔离环境，不覆盖正在工作的安装。`--no-deps` 仅适用于运行依赖已经按固定
清单准备完毕的情况；不是忽略依赖。构建工具必须先安装好，原生模型/库另行准备。
`--no-compile` 防止安装生成产品字节码；首次验证前不导入产品。不要在服务的
`PYTHONPATH` 中混入源码或测试工具。

### 同环境运行推理与接收器

Linux/Python3.13的整合环境使用
`requirements-linux-py313-runtime-and-receiver.lock.txt`。不要强行叠加旧推理锁和
接收器锁：合并锁明确采用接收器的pycparser3.12，替代推理单独锁中的3.1。
pyftpdlib2.1.0使用官方sdist；锁保存其已验证源码包摘要和本次构建的wheel摘要。
先安装固定构建依赖，再安装整合依赖：

```sh
python -I -B -m pip install --require-hashes -r requirements-build-dev9.lock.txt
python -I -B -m pip install --no-build-isolation --no-compile --require-hashes \
  -r requirements-linux-py313-runtime-and-receiver.lock.txt
python -I -B -m pip check
```

可先在准备环境下载并构建wheel，再用`--no-index --find-links`离线安装。不要为
离线安装猜测或修改hash。不同构建产物须单独记录摘要；来源sdist和本次本地wheel
不是同一个文件。这个锁不是其他Python版本/平台的兼容承诺，也不批准目标资格。

## 3. 验证实际安装的产品字节

用同一个解释器运行新工具的绝对路径，显式提供 materializer 回执及其已保存摘要：

```sh
python -I -B /absolute/tools/qualification/verify_installed_target.py \
  --receipt /absolute/candidate/RECEIPT.json --receipt-sha256 EXACT_RECEIPT_SHA256
```

工具使用 package spec 定位安装，不先导入产品代码；核验所有产品源文件，拒绝
额外/缺失/修改文件、符号链接和产品字节码缓存。它只证明已安装源字节与指定候选
一致，不验证整个 Python 环境、模型或系统库，也不启动服务。依赖和模型的校验是
单独步骤。普通安装产生的缓存须通过重建一个全新的无编译安装解决，不应对工作中的
环境随意清理。

## 4. 在新数据目录做有限启动和实际语音检查

模型布局必须满足 `strict_supervisor` 的 canonical 路径、独立普通文件等检查。
复制模型时核验字节；软链接或多硬链接会被拒绝。保留模型许可，不把权重传进源码 Git。
按照 `STRICT_BACKEND_SUPERVISOR.md` 提供显式模型、policy 和 storage 参数，先 `check`
再 `serve`。只绑定 loopback，在新 0700 数据目录和既有私有 instance.lock 上测试。
SIGTERM 主动停止监督器的正常信号退出码为 143，不能错误地要求为零。

必须分别检查真实锁占用/释放、工作线程、有限输入的完成状态、原生回执/PCM/文本
输出和关闭结果。健康接口只是存活，不等于模型就绪。安装或短时语音成功仍不等于
准确率、长时稳定性、手机录音或目标服务器上线验收。

不同受审目标的 wheel 可能拥有相同开发版本和文件名，但包含不同的精确运行时
身份。必须记录 wheel SHA-256 和 materializer 回执，不能只凭文件名或版本号认定
安装一致；不要在已有同版本安装上依赖 pip 的“已满足”判断。

当前同一云主机上的实际 Python 3.13 动态绑定已通过有限物理恢复流程和九项候选
校验，以及受审安装后的短时真实语音检查。完整范围和未通过项目见
[`动态运行时结果`](../qualification/dynamic_install_20261104/RESULT.md)。
这不是独立服务器安装或上线验收。

后续dev67已重新执行源绑定的物理证明、构建全新wheel并安装，完成三代际真实输入、
两次跨inbox交接和一次明确的中断恢复。完整来源、wheel/回执hash及边界见
[完整安装后的三代际流程](INSTALLED_GENERATION_FLOW_RESULT_20261104.md)。
这是同一云主机的新环境/新存储证明，不代表用户服务器或旧存储迁移获准。
