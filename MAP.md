# MAP — Nanodesu! 的工程地图

解包工具的工程结构。它读一个归档、把结构报出来，中途从不运行里面的程序。

## 顶层目录

```
nanodesu.py          主入口与全部命令（extract / neutralize / pyz / verify / search / build / info / panel）
neutralize.py        失活动作的实现，四条不可动约束与 caveats 都在这里
boundary.py          反向边界声明（BOUNDARY_NOTICE），挂在每一条返回路径上
boundary_voice.py    人格文案层，与机器可读输出严格分离
panel_window.py      窗口与终端渲染，读 panel.json 与 font.json
panel.json           九色面板定义
font.json            字模表
test/                14 个测试文件，含 spawn_guard.py（进程守卫）
tools/               开发期工具，不进运行时
.github/workflows/   发布与 CI
```

## 模块职责

`nanodesu.py` 是唯一入口，也是唯一会写文件的地方，而且只往输出目录写。它对归档的读取集中在 CArchive 目录表、条目字节与 PYZ 三处，其余内容一概看不见 —— 这一点写在 `README` 的「它不建立什么」里，而不是留给读者推测。

`neutralize.py` 做一个动作：把指定模块换成空 stub，产出一个**变体**。四条约束写在 `ACTION_CONTRACT.md`：原件不动、改动逐字节记录（含改前内容）、永不称 clean / safe / fixed、报告说出无法验证的部分。`verify_untouched()` 读回原归档逐条目比对，把「除目标模块外一个字节没动」变成可核对的断言。

`boundary.py` 的四句是安全论证的执行形式：解包不等于源码、同哈希不等于安全、干净不等于没藏东西、成功不等于完整。它被挂在每条返回路径上，`info` 不问也打印。

## 数据流

```
输入归档
  -> CArchive 目录表（名字、偏移、长度）
  -> 条目字节 / PYZ 索引
  -> 输出目录（路径被钳制在输出目录内，越界条目被修复而非跟随）
  -> 报告：stdout（人读）+ --json（机器读）+ _archive_manifest.json
  -> 可选：neutralize 产出一个变体 + _neutralize_record.json
```

## 入口与命令

```
python nanodesu.py extract  app.exe -o work/
python nanodesu.py neutralize app.exe -o work/ --in-pyz --modules payload
python nanodesu.py pyz app.exe       # 列出 PYZ 索引
python nanodesu.py verify  app.exe   # 读回原归档做逐条目比对
python nanodesu.py info              # 打印边界声明
python nanodesu.py --boundary        # 同上，显式请求
```

## 测试与验证

`test/` 里 14 个文件，运行 `python -m pytest -q`。按职责分三类：格式与解析（`test_format_matrix`、`test_nanodesu`）、边界与安全（`test_boundary`、`test_security_review`、`test_symlink_reported`、`spawn_guard.py`）、人格层（`test_voice*.py`、`test_panel_window`）。

`tools/prove_variant_still_runs.py` 是端到端验证：自己造一个 onefile exe、运行、中和、重建、再运行，用「变体能不能起来、Python 能不能初始化」两个判据。

## 边界（写进条目的）

不执行、不加载、不启动归档；唯一的进程是问 magic number 的 Python 解释器。不做反编译器、不做恶意检测器、不替代杀毒软件。原件永不修改、移动或删除。

## 发布

`pyproject.toml` 定义包；tag 触发 `.github/workflows/release.yml`，Trusted Publishing 到 PyPI，并用 `nanodesu.exe` 做 GitHub release 附件。sdist 发布前会被解包并自己跑一遍测试。
