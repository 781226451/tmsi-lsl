# tmsi-lsl

基于 **TMSi Python Interface**（`tmsi-python-interface-main/`）的 SAGA 采集程序：

1. 发现并连接 TMSi SAGA 设备；
2. 设置**选通通道**（只让指定通道处于采样状态）；
3. 启动测量、获取数据；
4. 通过 SDK 的 `LSLWriter` 把数据实时推送到 **Lab Streaming Layer (LSL)** 流，供 LabRecorder / OpenViBE / MNE-LSL / PsychoPy 等消费。

两种用法：**命令行**（`tmsi-lsl`）和 **Qt Quick 图形界面**（`tmsi-lsl-gui`，选通 / 采样率 / 设置保存为 TOML），二者共用同一套设备与推流后端。

---

## 目录结构

```
.
├── pyproject.toml              # uv 项目定义（Python 3.11）
├── .python-version             # 3.11
├── settings.example.toml       # 设置文件示例（可直接复制为 settings.toml）
├── src/tmsi_lsl/
│   ├── cli.py                  # 命令行入口（连接 → 选通 → 采样 → LSL）
│   ├── settings.py             # TOML 设置的读写/校验（不依赖 Qt）
│   ├── saga_source.py          # 设备发现/打开/通道配置/启动停止的封装
│   ├── channels.py             # 通道列表、选通语法解析与选择逻辑（不依赖 SDK）
│   ├── lsl_stream.py           # 基于 SDK LSLWriter 的 LSL outlet + 吞吐统计
│   ├── timing.py               # 取数间隔记录（包装 get_device_data，不改 SDK）
│   ├── simulator.py            # 无硬件时的模拟 SAGA 设备
│   ├── sdk_bootstrap.py        # 定位并加载 tmsi-python-interface-main
│   └── gui/                    # Qt Quick 图形界面
│       ├── app.py              # 入口：QGuiApplication + QQmlApplicationEngine
│       ├── controller.py       # QML 控制器（状态、设置读写、命令）
│       ├── models.py           # 通道表格模型 + 搜索/类型过滤代理
│       ├── session.py          # 后台线程：设备与 LSL 的生命周期
│       └── qml/Main.qml        # 界面布局
├── tests/                      # 单元测试 + LSL 端到端 + GUI 自检
├── scripts/
│   ├── probe_connections.py    # 逐个接口组合探测设备（排查“找不到设备”）
│   ├── sleep_precision.py      # 对比 time.sleep 精度（3.10 vs 3.11 / 有无 LSL）
│   └── gui_screenshot.py       # 无显示器渲染界面截图
├── docs/                       # 本地界面截图（不入库）
└── tmsi-python-interface-main/ # 官方 TMSi Python Interface（未修改）
```

## 环境要求

| 项目 | 说明 |
| --- | --- |
| 操作系统 | Windows（TMSi 驱动只有 Windows 版本） |
| Python | 3.11（由 `uv` 自动安装/管理） |
| uv | `uv 0.13+` |
| TMSi 驱动 | **必须在目标机器上安装**，DLL 固定位于 `C:\Program Files\TMSi\Saga\TMSiSagaDeviceLib.dll` |
| Python 包源 | 默认使用**清华 PyPI 镜像**（见下节），无需代理即可快速安装依赖 |

> SDK 只从上面这个固定路径加载 DLL，本程序不复制、不修改驱动。

## 快速开始

```powershell
# 1. 创建虚拟环境并安装依赖（自动下载 CPython 3.11）
uv sync

# 2. 自检：SDK 位置、驱动 DLL、pylsl、设备发现
uv run tmsi-lsl --check

# 3. 图形界面（推荐第一次用这个）
uv run tmsi-lsl-gui

# 4. 命令行：查看设备全部通道（# 为 1 基编号，选通时用它）
uv run tmsi-lsl --list-channels

# 5. 选通 1-8 号通道并开始推流（Ctrl+C 停止）
uv run tmsi-lsl --channels 1-8

# 6. 只跑 30 秒
uv run tmsi-lsl --channels "type:UNI" --duration 30
```

不想每次敲 `uv run`，也可以直接用虚拟环境里的可执行文件：

```powershell
.\.venv\Scripts\Activate.ps1        # 激活后：tmsi-lsl --list-channels
# 或
.\.venv\Scripts\tmsi-lsl.exe --list-channels
```

## 图形界面（Qt Quick）

```powershell
uv run tmsi-lsl-gui                       # 使用 ./settings.toml
uv run tmsi-lsl-gui --settings lab.toml   # 指定设置文件
```

> 界面截图保存在本地 `docs/` 目录，**不随仓库分发**（其中含真实设备序列号）。
> 可用 `scripts/gui_screenshot.py` 在本地重新生成。

界面能力：

| 区域 | 说明 |
| --- | --- |
| 通道表 | 显示编号 / 名称 / 类型 / 单位 / 采样率 / 设备状态；点击整行或勾选框即可选通 |
| 矩阵视图 | 通道名形如 `R1C1…R8C8` 时自动切换成 **8×8 电极矩阵**；单击切换单个电极，**按住左键拖拽框选一片**（`Ctrl` 追加），可随时用「列表 / 矩阵」切回 |
| 过滤 | 名称搜索（输入即筛选）+ 按类型下拉；全选 / 全不选 / 反选 / **REF+64** 一键预设 |
| 采样率 | 基准采样率 `4000 / 4096`，分频 `1 / 2 / 4 / 8`；同时显示**计划值**与**设备当前值** |
| LSL | 流名称、延迟补偿 offset |
| 设备接口 | 默认折叠（`docked/usb`），展开可选 `wifi / optical`、`network / none` |
| 设置文件 | 保存 / 另存为 / 加载，路径就显示在面板上；**推流中「加载」不可用** |
| 推流 | 「应用选通并开始推流」「停止推流」，下方实时显示帧数与帧率 |
| 消息 | 底部状态栏 + 消息面板（`Ctrl+L`），记录连接、错误、设置加载等事件 |

细节：

* **电极矩阵视图**：按通道名里的 `R<行>C<列>` 排布成 行 × 列 网格，行列标签对齐，格点显示通道名；设备缺少的格点显示为灰色不可点。检测到这类通道时界面会自动切到矩阵（手动点过「列表」后不再自动切）；
* **框选一片**：在矩阵里按住左键拖动，会实时高亮矩形区域（橙色），松开后把这批电极设为选通；按住 `Ctrl` 拖拽则**追加**到现有选通。单击仍是切换单个电极；
* 框选的替换范围**仅限矩阵内**：`CREF`、`STATUS`、`COUNTER` 等不在网格里的通道保持原状，不会被框选顺手关掉；
* `status` / `counter` 类型通道固件不允许关闭，界面会标记为「固件固定（不可关闭）」，勾选状态无法取消；若设备本来就把某个 counter 关着，它仍可按普通通道勾选；
* 通道表上方有 **REF+64** 按钮：一键选通「参考通道 + 前 64 个数据通道」，和命令行 `--preset ref64` 等价；
* **采样中通道选通只读**：推流开始后，勾选框、全选/全不选/反选/REF+64、矩阵点选与框选**全部锁定变灰**，标题旁显示「推流中·已锁定」；停止推流后自动恢复。原因同上——固件在采样期间不接受配置写入，此时改选通也不会生效；
* **采样中不能加载设置**：推流期间「加载」按钮和 `Ctrl+O` 都不可用（后端 `loadSettingsFrom()` 也会拒绝并提示）。因为加载会替换选通与采样率计划，而这些在采样中无法应用；「保存 / 另存为」不受限制；
* **采样率在「连接过程中」与「推流中」锁定**：`基准采样率` 与 `分频 divider` 两个下拉在点下「连接设备」的瞬间变灰（`busy` 期间），连接完成后恢复可编辑——因为 SAGA 允许在**已打开但未采样**（`Device_Open`）状态下写配置；一旦开始推流又变灰，直到「停止推流」。这与固件约束一致，也避免采集过程中误改；
* **采样率下拉是「计划值」**：改基准/分频不会立刻动设备，只有点「应用选通并开始推流」才会写入 SAGA。采样率框里同时显示两个数：**计划值**（大号蓝字，来自下拉/设置文件）和**设备当前值**（右侧小字，来自设备实际状态），不一致时后者变橙；顶部「设备采样率」也是设备实际值，推流开始后会刷新；
* **推流中改采样率**：SAGA 不允许在采样状态下写配置，LSL 流的 `nominal_srate` 也是建流时固定的，所以无法「边采边改」。此时界面会出现 **「应用 500 Hz 并重启推流」** 按钮：自动完成 stop → 写配置 → 重新建流 → 开始采样，断流约 1 秒，下游会看到一个新的流实例；
* 设备发现、配置、启停在**后台线程**执行（`session.py`），连接或启动时界面不会卡住；
* 启动时若当前目录存在 `settings.toml` 会自动加载；连上设备后自动把保存的选通应用到该设备，名字找不到时回退到保存的索引并在消息面板提示；
* 没有设置文件时，连接后采样率面板会**自动同步成设备当前配置**（例如设备在 4000 Hz / divider 2，界面就显示 4000 与 2），避免误以为会改动设备；
* 右上角「模拟设备」可在**没有硬件**时完整体验（通道表来自模拟 SAGA，数据为合成信号）；
* 快捷键：`Ctrl+S` 保存、`Ctrl+O` 加载、`Ctrl+L` 消息面板。

## 设置文件（TOML）

界面保存的 `settings.toml`（示例见 [settings.example.toml](settings.example.toml)）：

```toml
schema_version = 1

[device]
dr_interface = "docked"      # docked / wifi / optical
ds_interface = "usb"         # usb / network / none
device_index = 0             # 多台设备时选第几台（0 基）

[channels]
names   = ["CREF", "UNI 01", "UNI 02", "UNI 03"]   # 以通道名作为持久标识
indices = [0, 1, 2, 3]                              # 0 基索引，名称失效时的兜底

[sampling]
base_rate = 4096             # 4000 / 4096
divider   = 2                # 1 / 2 / 4 / 8，实际采样率 = base_rate / divider

[lsl]
name   = "TMSi SAGA"         # LSL 流名称
offset = 0.0335              # 元数据里的延迟补偿（秒）
```

加载规则：

* **通道以名称为准**，找不到同名通道时按保存的索引兜底，仍不可用则跳过并在消息里说明（换设备、改通道名时不会静默出错）；
* 未知的键/表会被忽略（向前兼容）；`schema_version` 高于程序支持版本、或取值非法（例如 `divider = 3`）时会明确报错，界面继续用默认值运行；
* 文件采用原子写入（先写临时文件再替换），手动编辑时注释会被保留在示例中，保存则按固定格式重写。

## 国内加速（清华 PyPI 镜像）

`pyproject.toml` 里已经默认把清华 TUNA 镜像设为唯一包索引，`uv sync` / `uv lock` 会自动走镜像，无需额外配置：

```toml
[[tool.uv.index]]
name = "tsinghua"
url = "https://pypi.tuna.tsinghua.edu.cn/simple"
default = true
```

`uv.lock` 中记录的下载地址同样是镜像地址，所以校园网/内网机器复现环境时不会再去访问 pypi.org。

需要临时切回官方 PyPI：

```powershell
$env:UV_DEFAULT_INDEX = "https://pypi.org/simple"
uv lock --refresh      # 重新生成 uv.lock
uv sync
```

也可以为其他项目全局配置：

```powershell
$env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"   # 仅当前终端
# 或写入用户级配置（%APPDATA%\uv\uv.toml）：
# default-index = "https://pypi.tuna.tsinghua.edu.cn/simple"
```

> 说明：uv 从 GitHub 下载 CPython 解释器，这一步不走 PyPI 镜像；如果不想让 uv 联网装解释器，
> 加上 `--no-python-downloads`（本机已有 3.11 时 uv 会直接复用）。

## 选通通道（`--channels`）

选通 = **只让列出的通道采样，其余通道关闭**（status / counter 通道固件不允许关闭，会提示但保持激活）。

`--channels` 接受逗号分隔的 token，编号与 `--list-channels` 输出一致（**1 基**）：

| Token | 含义 |
| --- | --- |
| `3` | 第 3 号通道 |
| `1-8` | 第 1 至第 8 号通道 |
| `type:UNI` | 所有 UNI 类型通道 |
| `name:BIP*` | 名称匹配 `BIP*` 的通道（glob，忽略大小写） |
| `BIP 01` | 裸 token 当作名称 glob |

可以混合使用：

```powershell
uv run tmsi-lsl --channels "1-8, type:AUX"
uv run tmsi-lsl --channels "name:BIP*" --lsl-name "EMG SAGA"
uv run tmsi-lsl --channel-types uni,bip        # 等价于 --channels "type:UNI,type:BIP"
```

不传 `--channels` / `--channel-types` 时**不改变**设备当前的通道配置，直接按现有配置采。

> 设备侧的“已启用通道”会持久化在 SAGA 上，所以上一条命令选通的通道会影响下一条命令；
> 每次都显式写 `--channels` 更稳妥。

### 常用预设（`--preset`）

手写 65 个通道号太啰嗦，常用的组合做成了预设，与 `--channels` / `--channel-types` 互斥：

| 预设 | 选通内容 |
| --- | --- |
| `--preset ref64` | **参考通道（CREF）+ 前 64 个 UNI 数据通道**（典型 SAGA 64 用法） |
| `--preset uni` | 所有 UNI 通道（含 CREF） |
| `--preset all` | 设备报告的全部通道 |

```powershell
uv run tmsi-lsl --preset ref64                 # 65 个通道
```

界面里对应的是通道表上方的 **`REF+64`** 按钮。

> `ref64` 按“设备通道顺序”取前 64 个 UNI 通道（排除参考通道与 AUX/sensor/counter）；
> 若设备实际不足 64 个，会给出告警并按实际数量选通，不会报错。

## LSL 输出

推流由 SDK 的 `TMSiFileFormats.file_formats.lsl_stream_writer.LSLWriter` 完成：

* 流名称：`--lsl-name`（默认 `TMSi SAGA`）
* 流类型：`EEG`（SDK 固定值）
* 源 id：`tmsi-<设备序列号>`
* 通道数：选通后的活动通道数
* 采样率：设备当前采样率
* 数据格式：`float32`
* 通道元数据：label / index / unit / type，另附 `synchronization` 段（`--lsl-offset`，默认 `0.0335` s）

> **`STATUS` / `COUNTER` 无法关闭**：这两个通道由固件强制采样。像 `ref64` 这样选通 65 个信号通道时，
> LSL 流里仍会出现 67 列（末尾 `STATUS`、`COUNTER`）。命令行会明确提示“2 channel(s) stay active
> because the firmware does not allow disabling them”，界面里对应的两行标记为「固件固定（不可关闭）」。
> 下游按 label 取前 65 个通道即可。

接收验证：

```powershell
# LabRecorder 里应能看到 "TMSi SAGA"；或用 pylsl 自检
uv run python -c "from pylsl import resolve_streams; [print(s.name(), s.channel_count(), s.nominal_srate()) for s in resolve_streams(2)]"
```

## 采样率

```powershell
uv run tmsi-lsl --channels "type:UNI" --base-sample-rate 4096 --divider 2
```

* `--base-sample-rate`：`4000`（Decimal）或 `4096`（Binary），不传则保持设备设置；
* `--divider`：`1 / 2 / 4 / 8`，对所有已启用通道生效，采样率 = 基准率 / divider。

> **注意**：LSL 流只能声明一个采样率。如果不同通道类型的 divider 不同（例如 UNI 500 Hz、status 4000 Hz），
> 程序会给出告警，因为下游会按单一采样率解释多路数据。需要严格对齐时请统一使用 `--divider`。

### 参考方式（REF）

参考方式**固定为 `common` + `fixed`**，不提供命令行开关：

* `common`：每个 UNI 通道相对 **CREF 电极**（单独参考），**不使用公共平均参考**；
* `fixed`：CREF 电极松动时**也不会**自动切换参考方式（避免数据含义悄悄改变）。

设备一致时程序**不写任何配置**；只有设备被设成别的参考方式时，才会在开始推流前纠正一次。
**参考模式只在 GUI 顶部显示**（`参考 common（CREF 单独参考）· 自动回退 fixed`），消息面板同时记一条；
CLI 不打印这一行。

> 需要程序化查看/修改时用 `SagaSource.reference()` / `SagaSource.ensure_reference("common", auto="fixed")`。

概念上参考有两个维度（仅供理解，本程序只用第一种组合）：

| 维度 | 取值 | 含义 |
| --- | --- | --- |
| reference | `common` | 每个 UNI 通道相对 **CREF 电极** |
| | `average` | 公共平均参考（CAR） |
| auto-reference | `fixed` | 参考失效时**不切换** |
| | `average` | 参考失效时自动切到 average |

SDK 里对应的枚举：`RefMethod{Common=0, Average=1}`、`AutoRefMethod{Fixed=0, Average=1}`
（`tmsi_device_enums` 里另有等价别名 `ReferenceMethod.common/average`、`ReferenceSwitch.fixed/auto`）。

参考写入和采样率一样，只能在「已连接但未采样」时进行；GUI 在开始推流前按 `common/fixed` 执行这一步。

> 注意：CREF **通道**（0 号）仍会被 `ref64` 预设采集进流里，用于事后重参考或检查电极接触；
> 这与"电气参考方式"是两件事。

## 取数间隔诊断（`--record-timing`）

SDK 的采样线程按 `TMSiThread(pause=0.01)` 每 10 ms 调一次 `device.get_device_data()`。
`--record-timing FILE.csv` 会在**不修改 vendor SDK** 的前提下包装该方法（只替换设备实例上的属性），
记录每次调用的开始时刻、耗时、与上一次的间隔、以及本次返回的 sample set 数，退出时打印统计：

```powershell
uv run tmsi-lsl --duration 30 --record-timing timing.csv
```

```
SDK data fetch timing
  fetches       : 467 over 5.028 s (92.7 Hz)
  interval (ms) : mean 10.79  p50 10.97  p95 11.10  p99 11.33  max 11.56  min 10.03
  call (ms)     : mean 0.02  p95 0.04  max 0.10
  sample sets   : 9,112 total (156 empty fetches)
  csv           : timing.csv
```

CSV 列：`index,t_start_s,duration_ms,interval_ms,num_sets`（间隔为空表示第一次调用）。
也可以编程调用：`recorder = source.enable_fetch_timing("timing.csv")`（须在 `start()` 之前）。

### Python 3.10 与 3.11 的 `time.sleep` 精度

Windows 上 CPython ≤ 3.10 会把 `time.sleep()` 向上取整到 ~15.6 ms 的系统节拍，3.11 改用高精度可等待定时器：

| `time.sleep` | 3.10.22 空进程 | 3.10.22 + LSL outlet | 3.11.17 空进程 |
| --- | --- | --- | --- |
| 1 ms | **15.74 ms** | — | 1.53 ms |
| 5 ms | **15.75 ms** | — | 5.36 ms |
| 10 ms | **15.76 ms** | 10.26 ms | 10.34 ms |

**但本程序在两个版本上几乎没有差别**：liblsl 只要真正建立一条 stream（`StreamOutlet` 或 `StreamInlet`，即启动其内部线程）
就会把进程的 Windows 定时器精度提到 1 ms，于是 `time.sleep(0.01)` 不再被 15.6 ms 节拍拉长。
本程序在 `start_measurement()` 之前已经建好 outlet，所以采样线程实测为 —— 真机 5 秒：
Python 3.11 → 92.7 Hz / 平均 10.79 ms，Python 3.10 → 91.1 Hz / 平均 10.98 ms，两者 LSL 帧率都稳定在 ~2000 帧/秒。

三组对照（Python 3.10，`time.sleep(0.01)` 实测）：

| 进程里有什么 | 实测 |
| --- | --- |
| 只有解释器 | **15.76 ms** |
| `import pylsl`（未建 stream） | 15.72 ms |
| 加载 TMSi DLL（未开始采样） | 15.78 ms |
| 建了 `StreamOutlet` | 10.26 ms |
| 建了 `StreamInlet` | 10.39 ms |

再补两个同机实测（Python 3.10），说明这个"请求"的性质：

* **请求是"粘"的**：创建 outlet 后测 10.21 ms，`del outlet` + `gc.collect()` 之后仍是 10.23 ms
  ——liblsl 只请求不归还，进程整个生命周期都保持 1 ms 节拍；
* **请求按进程生效**：另一个进程持有 outlet 时，本进程（没有任何 stream）仍是 15.49 ms
  ——Windows 10 2004 起定时器精度请求不再全局共享。

如果想复现上面两组对比：

```powershell
uv run python scripts/sleep_precision.py                                   # 当前解释器（无 LSL）
uv run --python 3.10 --no-project --with pylsl python scripts/sleep_precision.py --outlet
```

> 也就是说：**空脚本里 3.10 的 sleep 确实差 5.4 ms，但在本程序里被 liblsl 建立的 stream 掩盖了**。
> 若以后有路径在取数时尚未建立任何 LSL stream（例如只用 SDK 不推流），3.10 上仍会遇到 ~15.6 ms 的节拍。
> 注意这只是消除了"被拉长"，并**不等于 3.10 与 3.11 等价**——接收端实测的抖动长尾仍与发送端版本相关（见上一节表格）。

### 不开 LSL 时的取数间隔（`scripts/fetch_only_probe.py`）

要看清"没有 LSL 掩盖"的真实表现，就直接跑 SDK 采样、不建任何 stream：

```powershell
uv run python scripts/fetch_only_probe.py --duration 6                       # 3.11
uv run --python 3.10 --no-project python scripts/fetch_only_probe.py --duration 6   # 3.10
```

（该脚本不需要 pylsl、也不需要 Qt，只用到 SDK 与标准库。）

真机实测（SAGA DR，67 通道 @ 2000 Hz，各 6 秒）：

| 指标 | Python 3.11.17 | Python 3.10.22 |
| --- | --- | --- |
| 取数频率 | **93.7 Hz** | **64.1 Hz** |
| 间隔 mean / p95 / max | **10.67 / 11.13 / 11.51 ms** | **15.59 / 16.17 / 17.10 ms** |
| 每次样本数（含空读 / 仅非空） | 20.5 / 29.9 | 30.0 / 31.9 |
| 样本吞吐 | 1,927 sets/s | 1,931 sets/s |
| 单次取数耗时 mean | 0.03 ms | 0.29 ms |
| 空读比例 | 31.4% | 6.0% |

解读：

* **间隔差 4.9 ms（46%）**，正是 15.6 ms 节拍与 1 ms 节拍的差别；
* **吞吐相同**（~1,930 sets/s）：设备内部缓冲把差异吸收了，所以"不丢数，但取数更粗"；
* 设备内部块的粒度约 **30 个 sample set**（≈15 ms @2000 Hz）：3.11 以 93.7 Hz 轮询，比块到达更密，所以 31% 是空读；3.10 的 64 Hz 恰好贴近块率，空读只有 6%；
* 这就是"没有 LSL outlet 抬高定时器"时的真实表现，也是为什么**只用 SDK、不推流**的路径在 3.10 上会慢一拍。

### 接收端分块与时间戳（`scripts/lsl_receive_probe.py`）

`--record-timing` 记录的是 **SDK 侧**取数；这个脚本记录 **消费端**：每次 `pull_chunk` 拿到多少样本、
LSL 时间戳是否连续——即 LabRecorder / MNE-LSL 实际看到的样子。

```powershell
# 终端 1：发送端
uv run tmsi-lsl --duration 20

# 终端 2：接收端探针
uv run python scripts/lsl_receive_probe.py --duration 8 --csv receive.csv
```

`--min-samples` 决定「每次获取」的含义：

| 取值 | 行为 |
| --- | --- |
| `1`（默认） | 有数据就返回 → 看到**发送端**真实分块粒度 |
| `0` | 攒满 `--max-samples`（默认 1024）或 `--pull-timeout` 才返回 → 看到**接收端**的批量拉取行为 |

CSV 列：`index,pulled_at_s,gap_ms,samples,first_ts,last_ts,span_ms`。
统计里有 `worst ts gap`：时间戳步长与「上一块样本数 ÷ 采样率」的最大偏差，用来判断是否掉样本。

**交叉实测**（真机 67 通道 @ 2000 Hz，8 秒，`min_samples=1`）：

| 发送端 | 接收端 | pulls | 样本/秒 | 每次样本 mean / p95 / max | pull 间隔 mean / p95 | 时间戳最大偏差 |
| --- | --- | --- | --- | --- | --- | --- |
| 3.11 | 3.11 | 499 | 2004 | 28.7 / 36 / 48 | 14.34 / 21.31 ms | 19.7 ms |
| 3.10 | 3.10 | 443 | 1998 | 31.4 / 62 / 79 | 15.77 / 30.86 ms | 29.8 ms |
| 3.11 | 3.10 | 466 | 2005 | 30.5 / 38 / 64 | 15.23 / 21.29 ms | 21.5 ms |
| 3.10 | 3.11 | 466 | 2000 | 31.6 / 57 / 72 | 15.83 / 31.31 ms | 30.4 ms |

结论：

* 四个组合的**吞吐都是 ~2000 样本/秒**，没有丢数；
* 分块的**抖动跟发送端版本走**（3.11 发送端 p95 36–38 样本，3.10 发送端 57–62），与接收端无关；
* 中位数都是 ~32 样本（≈16 ms），说明 liblsl 交付给 Python 的粒度约 15 ms，比发送端 ~10.8 ms 的推送间隔更粗；
* 3.10 发送端偶发 ~30 ms 的交付间隔（≈60 样本），即下游会看到轻微的时间戳"台阶"，但不影响总样本数。

## 无硬件演练（`--simulate`）

没有设备时，`--simulate` 用内置的模拟设备跑**完整链路**（通道配置 → LSL outlet → 数据推送）：

```powershell
uv run tmsi-lsl --simulate --channels "1-8" --duration 10
```

## 可编程接口

```python
from tmsi_lsl.channels import parse_spec, select_indices
from tmsi_lsl.lsl_stream import LslStream
from tmsi_lsl.saga_source import SagaSource

source = SagaSource(dr_interface="docked", ds_interface="usb")
source.connect()

channels = source.all_channels()                       # 完整通道列表
indices = select_indices(channels, parse_spec("1-8"))  # 解析选通表达式
source.set_active_channels(indices)                    # 应用

stream = LslStream("MyExperiment")
stream.open(source.device)        # 必须在 start() 之前
source.start()
try:
    ...                           # 采集期间做别的事情
finally:
    source.stop()
    stream.close()
    source.close()
```

调用顺序（与 SDK 要求一致）：

```
discover → open → 选通通道 → 采样率配置 → LSLWriter.open → start_measurement
                                                            ↓
                                     stop_measurement → LSLWriter.close → close
```

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 正常结束（含 Ctrl+C 主动停止、`--list-channels`、`--check` 通过） |
| 1 | 运行期错误（设备打开/配置/采样失败、LSL outlet 创建失败） |
| 2 | 命令行或选通表达式错误 |
| 3 | 环境不满足（找不到 SDK、驱动 DLL 缺失、未发现设备） |
| 130 | 在初始化阶段被 Ctrl+C 中断 |

## 常见问题

**`--check` 报 `SAGA driver NOT FOUND`**
未安装 TMSi 驱动，或 DLL 不在 `C:\Program Files\TMSi\Saga\`。安装 SAGA 驱动后重试；SDK 不支持自定义 DLL 路径。

**`--check` 报 `SAGA driver : present but locked by another process`**
这行其实是 SDK 对“DLL 加载失败”的统一说法。最常见原因：TMSi 官方 **SAGA User Application 正在运行并占用驱动**
（可在任务管理器结束 `saga_user_application_*.exe`，或用 PowerShell：`Get-Process saga_user_application* | Stop-Process`）。
驱动同一时刻只允许一个进程连接，官方软件不退出，本程序就无法加载。
若确认没有官方程序在跑，则是 DLL 自身初始化失败（`WinError 1114`），可注销/重新登录或重装驱动。

**`--check` 驱动 OK，但 `devices : none found`**
先逐个接口组合探测，比反复试 `--dr-interface` 快：

```powershell
uv run python scripts/probe_connections.py
```

如果六种组合全部 `no device`，说明 **Windows 根本没有枚举到 TMSi 设备**（设备管理器里也不会有），
属于物理连接问题：DR 是否开机、是否卡好在 docking station 上、USB 线是否插在主机后置口、
docking station 指示灯是否亮、是否误插在无供电的 hub 上。

**`no SAGA device found ...`**
检查 USB 线、数据记录仪（DR）是否开机、是否已插在 docking station 上，以及是否有别的程序（TMSi 官方软件）已经占用了设备——驱动同一时刻只允许一个进程连接。

**改不了基准采样率 / 分频？**
它们只在**连接过程中**和**推流中**变灰：前者防止半连接状态乱改，后者是因为 SAGA 固件不允许在采样中写配置（`TMSiSetDeviceConfig` 要求设备处于 `Device_Open`）。
**已连接但未推流时是可以修改的**，改完点「应用选通并开始推流」生效；推流中要换速率请用「应用 X Hz 并重启推流」按钮。命令行不受影响，每次运行都可以带自己的 `--base-sample-rate` / `--divider`。

**推流进行中能改采样率/分频吗？**
不能「边采边改」，两条硬性限制：

1. **SAGA 固件**：`TMSiSetDeviceConfig`（采样率、分频都在里面）的前置条件是设备状态机处于 **Device_Open**（已打开、未采样）。
   采集中写配置不满足前置条件，DLL 会直接报错——SDK 自己也注释了这一点，本程序也在 `SagaSource.set_sampling()` 里拦截了这种调用。
2. **LSL 协议**：`StreamInfo` 的 `nominal_srate` 与 `channel_count` 在 outlet 创建时就固定了，之后无法修改。
   即便设备真的能变速率，同一个 LSL 流的元数据也不会跟着变，下游会按旧速率解释数据，时间轴就错了。

所以正确的做法是**断流重配**：停止采样 → 写配置 → 用新速率发布一个新流 → 重新开始采样。
界面上对应「应用 500 Hz 并重启推流」按钮（推流中且速率不一致时出现），断流约 1 秒。

如果下游完全不能接受断流，唯一可行的替代方案是：**设备固定跑最高速率（4000/4096、divider 1），降采样放到下游软件做**，
这样 LSL 流的速率始终不变；你改的只是下游的重采样率，而不是设备。

**`The interface bandwidth is not compatible with the channel configuration`**
选通通道太多或采样率太高，超出接口带宽（wifi 约 2 Mbps，docked/optical 约 32 Mbps）。降低通道数、提高 divider，或改装在 docking station 上。

**改了基准/分频，设备还是 2000 Hz？**
三个原因，按顺序排查：

1. **下拉框只是计划值**——只有点「应用选通并开始推流」（或命令行运行 `tmsi-lsl`）才会把采样率写进 SAGA；只改下拉不会动设备。面板此时会显示橙色提示：`设备当前 4000 Hz；点…后生效为 500 Hz`。
2. **正在推流**——采样率不能在采集过程中修改，需要先「停止推流」再重新开始。
3. **看的是哪个数**——采样率框里 **大号蓝字是计划值**（来自下拉/设置文件，点开始推流后才生效），**右侧小字 `设备 X Hz` 是设备当前实际值**（不一致时变橙）；顶部「设备采样率」也是设备实际值。两者不同就说明计划还没应用。

换算关系：实际速率 = 基准速率 ÷ divider（`4000/8 = 500`、`4000/2 = 2000`、`4096/4 = 1024`）。命令行可直接验证：

```powershell
uv run tmsi-lsl --preset ref64 --base-sample-rate 4000 --divider 8 --duration 2
# → LSL stream 'TMSi SAGA' published: 67 channels @ 500 Hz
```

**卡在“Acquiring”但没有数据**
看状态行 `last block ... ago`；如果持续增长，检查是否被其他程序抢占、DR 电量、以及 `--channels` 是否把通道全关了（程序会拒绝 0 通道的配置）。

**日志太吵/太安静**
`--log-level DEBUG` 打开细节（包含 SDK 调试日志），`-q` 只显示告警和错误。

**为什么依赖里没有 pandas / scipy / EDFlib**
SDK 的 `TMSiFileFormats/file_formats/__init__.py` 会一次性导入 Poly5 / XDF / EDF 三种写入器，而它们分别依赖 numpy、pandas、scipy、EDFlib。
本程序只用到 LSL，所以 `lsl_stream.py` 会先尝试完整导入，失败时只注册一个最小包对象、仅加载 `lsl_stream_writer`（只依赖 pylsl），避免为一个推流程序装几百 MB 的科学计算库。
以后要导出 poly5/xdf，直接 `uv add pandas scipy EDFlib-Python` 即可，导入逻辑会自动走完整包。

**GUI 依赖了哪些包**
`pyside6-essentials`（Qt Quick / Quick Controls，不含 WebEngine 等附加模块）+ `tomli`（Python 3.10 读取 TOML；3.11 起可用内置 `tomllib`）。
没有使用 `pyside6` 元包，因为它会额外拉入 WebEngine、Addons 等约 160 MB 用不到的模块。

**启动时 stderr 出现 `netinterfaces.cpp ... INFO| netif ...`**
这是 liblsl 自身在 outlet 创建时打印的网络接口信息（只出现一次）。当前 pylsl 1.18 未暴露设置日志级别的接口，无法通过 API 关闭；它不影响推流，重定向 stderr 即可屏蔽：`2>$null`。

**SDK 的活动日志**
设置环境变量 `TMSi_ACTIVITY=ON` 后，SDK 会把采集活动写入 `文档\TMSi\Activity\`。

## 开发

```powershell
uv sync                       # 含 pytest
uv run pytest                 # 97 个测试：设置/选通/模型/矩阵交互/端到端 LSL/GUI 自检

# 界面无显示器自检（加载真实 QML，跑一遍模拟推流）
uv run tmsi-lsl-gui --self-test

# 本地生成界面截图（写入 docs/，该目录不入库）
uv run python scripts/gui_screenshot.py --simulate --out docs/gui-streaming.png
uv run python scripts/gui_screenshot.py --demo-grid --view matrix --out docs/gui-matrix.png
uv run python scripts/gui_screenshot.py --demo-grid --view matrix --region 2,3,4,5 \
    --out docs/gui-matrix-region.png

# 真机测试（需要 SAGA + 已安装驱动）
$env:TMSI_LSL_HARDWARE = "1"
uv run pytest -m hardware -v
```

`tests/` 覆盖：

* `test_channels.py` / `test_settings.py`：选通语法、选择逻辑、TOML 读写与非法值校验（纯 Python，无 Qt）；
* `test_gui.py`：通道模型、过滤代理、矩阵模型（含框选区计算）、控制器保存/加载/异常恢复（offscreen Qt）；
* `test_gui_interaction.py`：用 `QTest` 向真实 QML 发送鼠标事件，验证单击切换、拖拽框选、`Ctrl` 追加拖拽；
* `test_stream.py`：模拟设备 → SDK 数据服务 → LSL outlet → `pylsl` 接收的端到端校验；
* `test_gui_app.py`：子进程运行 `--self-test`，确保 QML 能加载且信号链路连通。

QML 静态检查（`unqualified` 提示来自 `app` / `channelModel` / `channelFilter` 这三个 context property，属预期）：

```powershell
.\.venv\Scripts\pyside6-qmllint.exe -I .venv\Lib\site-packages\PySide6\qml src\tmsi_lsl\gui\qml\Main.qml
```

架构要点：QML 只与 `AppController`（QML 属性 + 槽）和两个数据显示模型交互；所有 SDK 调用都在
`DeviceSession` 所在的 `QThread` 里执行，通过队列化信号把结果送回界面线程。

## 版本管理

仓库已初始化（`main` 分支），首次提交包含程序源码、测试、脚本与 vendor 的 TMSi SDK **代码**。

纳入版本控制：`src/`、`tests/`、`scripts/`、`README.md`、`LICENSE`、`pyproject.toml`、`uv.lock`、`.python-version`、
`settings.example.toml`、`tmsi-python-interface-main/`（除生成的文档）。

**不**纳入（见 `.gitignore`）：

| 路径 | 原因 |
| --- | --- |
| `.venv/` | 由 `uv sync` 重建 |
| `__pycache__/`、`.pytest_cache/` | 生成物 |
| `settings.toml` | 本机配置（含本机通道选择），示例见 `settings.example.toml` |
| `docs/` | 本地界面截图，含真实设备序列号，不随仓库分发 |
| `tmsi-python-interface-main/Documentation/` | 33 MB / 1000+ 生成的 Sphinx HTML，运行时用不到 |
| `.acl-recovery/` | 权限修复的一次性备份 |

常用操作：

```powershell
git status                      # 看改动
git add -A && git commit -m "…" # 提交
git log --oneline --graph       # 历史
git remote add origin <url>     # 需要备份/协作时再加远端
git push -u origin main
```

> 提交身份当前是占位的仓库级配置（`tmsi-signal-source <tmsi-signal-source@localhost>`）。
> 换成自己的：`git config user.name "你的名字"`、`git config user.email "you@example.com"`。

---

底层调用到的 SDK 接口（`TMSiSDK/`）：

| 步骤 | API |
| --- | --- |
| 发现 | `TMSiSDK().discover(DeviceType.saga, dr_interface, ds_interface)` / `get_device_list` |
| 连接 | `dev.open()` / `dev.close()` |
| 通道 | `dev.get_device_channels()` / `dev.get_device_active_channels()` / `dev.set_device_active_channels(indices, activate)` |
| 采样率 | `dev.set_device_sampling_config(base_sample_rate=..., channel_type=..., channel_divider=...)` |
| 采集 | `dev.start_measurement(MeasurementType.SAGA_SIGNAL)` / `dev.stop_measurement()` |
| 推流 | `FileWriter(FileFormat.lsl, name).open(dev)`（内部注册 `SampleDataServer` 消费者） |

## 许可证

本项目（`src/`、`scripts/`、`tests/` 等）采用 **MIT** 许可证，见 [LICENSE](LICENSE)。

`tmsi-python-interface-main/` 是 TMSi 官方的 **TMSi Python Interface**（(c) Twente Medical Systems
International B.V.），按 **Apache License 2.0** 分发，全文见
[tmsi-python-interface-main/LICENSE.txt](tmsi-python-interface-main/LICENSE.txt)。
该目录未做任何修改，仅作为依赖被 `sys.path` 引入；使用/分发时请遵守 Apache-2.0 的署名要求。
