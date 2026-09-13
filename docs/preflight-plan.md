# yue2-V100 开工前系统检测计划

**目标**:在 Windows + Tesla V100(SM70)上,通过**自编译 audio.cpp(CUDA 12.x)+ GGUF 量化权重**跑通 YuE2。
**检测执行方式**:`pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-env.ps1`
**最近一次执行**:2026-09-13,结果 `results/preflight-20260913-093147.json`
**判定**:**READY WITH WARNINGS**(PASS 16 / WARN 3 / FAIL 0)——可以开工,但有 3 项必须先处理。

---

## 一、为什么检测项是这些

V100 走 YuE2 有两条路,官方 Python 路线已被排除,原因有两条且都已核实:

1. YuE2 的 pipeline 在初始化时硬检查 `torch.cuda.is_bf16_supported()`,V100 无 BF16 硬件;
2. PyTorch 2.10 的 cu128 wheel 只编到 Turing(7.5)及以上,Volta 被移出支持矩阵。

而 **audio.cpp 预编译包明确不覆盖 Volta**(官方 Windows 文档:预编译 CUDA 包需要驱动 580+ 与 CUDA 13,Volta 不在其中)。
所以只剩**自编译**一条路,这决定了检测重点必须落在「这台机器能不能编出 sm_70 的 CUDA 程序」。

## 二、检测项与判据

### A. 操作系统与主机

| 检查项 | 判据 | 为什么查 |
|---|---|---|
| OS 版本 | build ≥ 19041 | 过旧的 Windows 与新版 VS/CUDA 组合易出问题 |
| LongPathsEnabled | = 1 | `build\...\ggml-cuda` 深层路径易撞 MAX_PATH |
| 可用内存 | ≥ 8 GB | GGUF 走 mmap,不需要官方那 24 GB |

**实测**:Windows 11 build 22000(注册表 ProductName 仍写 "Windows 10 Pro",以 build 号为准)/ LongPathsEnabled = 1 / 可用 54.8 GB
→ **PASS / PASS / PASS**

### B. GPU 与驱动

| 检查项 | 判据 | 为什么查 |
|---|---|---|
| 设备与算力 | compute capability = 7.0 | 编译目标就是 compute_70,换卡就得换架构参数 |
| 显存 | ≥ 16 GB | Q4_0 实测峰值 ~7.7 GB、Q8_0 ~8.9 GB(5090 数据) |
| 驱动版本 | ≥ 570 且 < 581 | CUDA 12.8 要求 ≥ 570;581+ 已移除 Volta 支持,不可再升 |

**实测**:Tesla **V100-SXM2-16GB** / 16384 MiB / CC 7.0 / 驱动 **572.83**
→ **PASS / PASS / PASS**

> ⚠️ 注意:你之前说是 **PCIe** 版,实测是 **SXM2** 版。软件路径完全一致(同为 SM70/16 GB),仅带宽与 FP16 算力略有差异,不影响本计划。

### C. CUDA 工具链(最关键)

| 检查项 | 判据 | 为什么查 |
|---|---|---|
| nvcc 存在且为 12.x | release 12.* | CUDA 13 彻底移除 sm_70 |
| `compute_70` 在 `--list-gpu-arch` 中 | 必须存在 | **决定性检查**:没有它就没有 V100 构建 |
| 同机安装的 toolkit 列表 | 无 v13.x 混入 | 构建可能静默选到 13.x 而失败 |
| CUDA_PATH | 建议设置 | CMake 未显式指定 `CUDAToolkit_ROOT` 时会用它 |

**实测**:nvcc = `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin\nvcc.exe`,release 12.8 V12.8.61
`--list-gpu-arch` 含 `compute_70` ✅
**但机器上还装了 v13.1**,且 `CUDA_PATH` 为空
→ **PASS / PASS / WARN / WARN**

### D. MSVC 主机编译器(Windows 特有坑)

| 检查项 | 判据 | 为什么查 |
|---|---|---|
| `cl.exe` 存在 | 有 | nvcc 用 cl.exe 作主机编译器,不支持 clang-cl |
| `_MSC_VER` < CUDA 的上限 | host_config.h 允许区间内 | 超限会被 nvcc 直接拒绝 |
| 选中的 VS 实例 | 必须显式指定 | 多版本共存时会自动挑最新的那个 |
| Windows SDK | 存在 | cl.exe 依赖 |

**实测**:CUDA 12.8 的 `host_config.h` 上限为 **`_MSC_VER < 1950`**
可用 cl.exe = **14.44.35207**(`_MSC_VER` 1944 < 1950)✅,位于
`C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`
机器上另有 **VS 18 Community 18.3.11520.95**——`vswhere -latest` 会优先返回它,而它的 `_MSC_VER ≥ 1950` **会被 CUDA 12.8 拒绝**。
Windows SDK = 10.0.26100.0
→ **PASS / PASS / INFO(须显式指定 VS2022)/ PASS**

### E. 构建工具与磁盘

| 检查项 | 判据 | 为什么查 |
|---|---|---|
| cmake | ≥ 3.20(audio.cpp 要求) | 低于则无法配置 |
| ninja | 有则更优 | 与 MSVC 组合构建更快 |
| git | 有 | 拉取 dev 分支源码 |
| 目标盘剩余 | ≥ 20 GB 通过,12–20 GB 警告 | 权重 2.7 GB + 源码 0.1 GB + 构建树 3–8 GB |
| HF 缓存盘剩余 | ≥ 10 GB | 缓存默认位置可能不在项目盘 |
| python + hf CLI | 有 | 只用于下载 GGUF |

**实测**:cmake 4.3.0 / ninja 1.13.0 / git 2.41.0 / python 3.12.10(含 hf CLI)
磁盘:**C: 5.2 GB,D: 31.5 GB,E: 5.8 GB**
`HF_HOME` 当前指向 **`E:\ComfyUI\models\LLM`(只剩 5.8 GB)** ⚠️
→ **PASS / PASS / PASS / PASS / WARN / PASS**

### F. 网络

| 检查项 | 判据 |
|---|---|
| `hf-mirror.com:443` 可达 | 2.7–4.2 GB 权重能下下来 |

**实测**:可达 → **PASS**

---

## 三、必须先处理的 3 个警告

| # | 警告 | 风险 | 对策 |
|---|---|---|---|
| **W1** | 机器上同时有 CUDA 12.8 与 **13.1** | 配置阶段可能挑到 13.1 → `compute_70` 不存在直接失败;而且 CUDA 13 的程序需要驱动 ≥580,你的 572.83 **跑不起来**,等于白编 | 构建时**同时显式指定** `-DCUDAToolkit_ROOT` 和 `-DCMAKE_CUDA_COMPILER` 指向 v12.8,两个都要设(只设一个会出现「用新 nvcc 编译、链接旧 libcudart」的静默混搭) |
| **W2** | `CUDA_PATH` 为空 | 省略 `CUDAToolkit_ROOT` 时 CMake 找不到工具链 | 构建前 `setx CUDA_PATH "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8"`,或在配置命令里写死 |
| **W3** | `HF_HOME=E:\ComfyUI\models\LLM`,E 盘只剩 5.8 GB | 下 2.7–4.2 GB 权重会把 E 盘压到不足 2 GB,且可能污染你现有的 ComfyUI 模型目录 | 本次会话内 `$env:HF_HOME='D:\mt-tool\yue2-V100\cache'`,或用 `hf download --local-dir` 直接落到项目内 |

### 另需固定的一条纪律(非警告,但违反就白干)

**VS 实例必须显式指定为 VS2022 BuildTools**。机器上的 VS 18 会被自动探测选中,而 CUDA 12.8 的 host_config.h 拒绝 `_MSC_VER ≥ 1950`:

```powershell
.\scripts\build_windows.ps1 -VsInstall "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools" ...
```

若要手工 cmake,则必须先从 VS2022 的 `vcvars64.bat` 进入环境,不要用 VS18 的。

---

## 四、还需要遵守的两条项目约束

1. **预编译包不可用**:audio.cpp 的 Windows CUDA 预编译包面向 Turing 以上 + 驱动 580+,V100 不在覆盖范围。**只能用源码自编译**。
2. **驱动不要升级超过 581**:Volta 支持已在 580 之后的分支移除。当前 572.83 正确,保持不动。

## 五、执行进度

| # | 步骤 | 状态 |
|---|---|---|
| 1 | 固定工具链(v12.8 + VS2022 + 架构 70) | 已完成 —— 在 `configure.ps1` 内固定,未做系统级 `setx` |
| 2 | 克隆 audio.cpp dev 分支 | 已完成 —— head `87544b5`(2026-09-12) |
| 3 | 手工 cmake 配置(单架构 + 仅 yue2 + Release) | 已完成 —— 见第七节 |
| 4 | 编译 `audiocpp_cli` | 未开始(按约定在此暂停) |
| 5 | 下载 Q8_0 权重(4.2 GB) | 未开始 |
| 6 | 首次生成并记录 RTF / 峰值显存 | 未开始 |

> 步骤 3 的三个体积优化点(默认值都会显著放大构建目录):
> `-DCMAKE_CUDA_ARCHITECTURES=70-real`(默认会编 10 个架构)、
> `-DAUDIOCPP_MODEL_SET=custom -DAUDIOCPP_MODELS=yue2`(默认编 62 个模型族)、
> Release 构建类型(默认 RelWithDebInfo 带 `-g`,目标文件明显膨胀)。

## 六、复检

任何时候重跑一次即可,脚本只读、可重复执行,报告写到 `results\preflight-<时间戳>.json`:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-env.ps1
```

退出码:`0` = 无 FAIL,`1` = 存在 FAIL。可在升级驱动、增删 VS/CUDA 之后用来重新确认。

---

## 七、配置阶段实测记录(2026-09-13)

### 7.1 发现:本执行环境里 Ninja 会挂死,必须改用 Visual Studio 生成器

首次以 `-G Ninja` 配置时,卡在 `Detecting C compiler ABI info` 超过 10 分钟。逐项隔离后的结果:

| 隔离测试 | 结果 |
|---|---|
| cl.exe 直接编译 + 链接最小 C 文件 | **2.2 秒完成** → 编译器与链接器正常 |
| 最小 CMake 工程 + PATH 上的 ninja 1.13(`jobserver-pipe`) | 挂死 |
| 最小 CMake 工程 + VS 自带 ninja 1.12.1(C only) | 挂死 |
| 最小 CMake 工程 + CUDA + **Visual Studio 17 2022** 生成器 | **10–40 秒完成** |

PATH 上的 `ninja.exe` 是 `1.13.0.git.kitware.jobserver-pipe-1`,其 job server 走**命名管道**,而本执行沙箱禁止命名管道。但换成不含该特性的 1.12.1 后**同样挂死**,因此结论是:**在沙箱化的 shell 里 ninja 不可用**——CMake 的 ABI 探测会驱动构建工具去跑一个 `try_compile` 小工程,这一步被卡住。

**处置**:`configure.ps1` 默认改用 **Visual Studio 17 2022** 生成器。额外收益是生成器名称把 VS 大版本钉死在 2022,天然绕开了"VS 18 因 `_MSC_VER >= 1950` 被 CUDA 12.8 拒绝"这个坑。

> ⚠️ 这条是**执行环境**的限制,不是项目缺陷。在普通终端里手动用 `-G Ninja` 通常没有问题,而且构建更快。若要走 ninja,请显式 `-DCMAKE_MAKE_PROGRAM` 指向 VS 自带的 1.12.1,避开 `jobserver-pipe` 版本。

### 7.2 配置结果(全部锁定成功)

| 项 | 实际值 |
|---|---|
| 生成器 | Visual Studio 17 2022(x64,仅 Release) |
| C/CXX 编译器 | VS2022 BuildTools `cl.exe` 14.44.35207 |
| CUDA 编译器 | `nvcc` **12.8.61**,主机编译器 MSVC 19.44.35220.0 |
| CUDAToolkit | **v12.8**(确认不是 v13.1) |
| CUDA 架构 | `70-real`(仅 SASS,无多余架构) |
| 模型族 | `custom selected [yue2] linked [yue2]` |
| ggml | 0.12.0,commit `87544b5`(与仓库 HEAD 一致) |
| 其他 | OpenMP 2.0 已找到;NCCL 未找到(单卡无影响);SageAttention2 因 sm_70 自动禁用 |
| 配置耗时 / 构建目录体积 | 127 秒 / **49 MB**(编译后才会有实质增长) |

配置期间只有两类无害警告:ggml 的 `CMP194` 策略提示、sentencepiece 的 `cmake_minimum_required(3.5)` 弃用提示。

### 7.3 下一步命令

```powershell
cmake --build "D:\mt-tool\yue2-V100\build" --config Release --target audiocpp_cli
```

（VS 生成器是多配置的,必须在构建时用 `--config Release`;这也是脚本里把 `CMAKE_CONFIGURATION_TYPES` 限定为 Release 的原因。）
