# V100(SM70)本地补丁记录

本项目为在 Tesla V100 上运行 YuE2,对 **vendored 第三方源码** 做了改动。集中记录在此,便于复现、升级与回退。

补丁目录:`audio.cpp/`(git 检出,head `87544b5`,dev 分支)

---

## Patch 1:`fattn.cu` —— Volta 闪存注意力落到未实现的 16 列瓦片

**文件**:`audio.cpp/external/ggml/src/ggml-cuda/fattn.cu`
**位置**:`ggml_cuda_get_best_fattn_kernel()` 的 Volta 分支(约第 500 行)
**改动**:`return BEST_FATTN_KERNEL_MMA_F16;` → `return BEST_FATTN_KERNEL_TILE;`

### 现象

首次生成直接失败,日志反复刷同一条错误后 `unspecified launch failure`:

```
.../fattn-mma-f16.cuh:1740: ERROR: CUDA kernel flash_attn_ext_f16
  has no device code compatible with CUDA arch 700. ggml-cuda.cu was compiled for: 700
CUDA error: unspecified launch failure
  current device: 0, in function ggml_backend_cuda_synchronize at ggml-cuda.cu:3424
```

### 根因

1. `common.cuh:373` 的 `no_device_code()` 是**设备端** `__trap()`;`NO_DEVICE_CODE` 宏只在设备代码里生效。
   所以这不是"没编出来",而是**编出来了、但走的是一个刻意的陷阱分支**。
2. `fattn-mma-f16.cuh:1738` —— Volta 的 MMA f16 内核只实现 `ncols1*ncols2 >= 32`:
   ```cpp
   #ifdef VOLTA_MMA_AVAILABLE
       if (ncols1*ncols2 < 32) { NO_DEVICE_CODE; return; }
   #endif
   ```
   报错行 1740 正落在这里 → 被喂进了 16 列(或更小)的瓦片。
3. `common.cuh:260` 只在 **arch 恰好等于 Volta** 时定义 `VOLTA_MMA_AVAILABLE`:
   ```cpp
   #if !defined(GGML_USE_HIP) && __CUDA_ARCH__ == GGML_CUDA_CC_VOLTA
   #define VOLTA_MMA_AVAILABLE
   #endif
   ```
4. `common.cuh:330` 的 `volta_mma_available()` 判据是 **`ggml_cuda_highest_compiled_arch(cc) == VOLTA`**——即"编译进二进制的最高架构恰好是 Volta"。
5. 于是 `fattn.cu:493` 的 Volta 专用分支被激活,该分支在大矩阵时返回 `MMA_F16`;而瓦片选择函数 `fattn.cu:21-26` 的
   `if constexpr (ncols2 <= 16) { if (Q->ne[1] <= 16/ncols2) ... }` **没有 arch 守卫**,Volta 上可能选出 16 列瓦片 → 撞上第 2 条的陷阱。

**为什么这个 bug 平时看不见**:默认的多架构构建(含 86/89/120)会让 `highest_compiled_arch` 远高于 Volta,
`volta_mma_available()` 恒为 false,Volta 专用分支根本进不去。**正是"只编 sm_70"这个体积优化把它暴露了出来。**

### 修复选择与取舍

| 方案 | 评价 |
|---|---|
| `-DGGML_CUDA_FA=OFF` 重编(**官方开关**,定义 `GGML_CUDA_NO_FA`) | 不改源码,但要重编全部 ggml-cuda(约 185 个 `.cu`),且 FA 整体关闭后注意力可能退到 CPU,长序列下慢到不可用 |
| 让 `volta_mma_available()` 返回 false | 改的是被几乎所有 TU 包含的头文件 → 同样全量重编 |
| **改 `fattn.cu` 调度返回值(采用)** | **只重编 `fattn.cu` 一个 TU** + 重新链接;保留 GPU 上的 FA,只是不用张量核 MMA |

TILE 内核(`fattn-tile.cuh`)是 arch 通用的,其 `NO_DEVICE_CODE` 只用于"跳过未使用变体",另有
`static_assert(ggml_cuda_fattn_tile_get_config(...) != 0)` 保证配置存在;上游也把它作为
"无可用张量核硬件"的官方回退。因此这是语义正确的降级,而非绕过。

### 性能影响(实测)

同一 seed、同一请求,补丁后 V100-SXM2-16GB 实测:

| 指标 | 值 |
|---|---|
| AR 语义生成 | 18.56 s / 1015 tokens = **54.7 tok/s** |
| NAR 声学合成 | 3.51 s |
| VAE 解码 | 1.08 s |
| 端到端 | **25.7 s**(产出 40.60 s 音频,RTF 0.63) |
| 峰值显存 | 6137 MiB |

对照 vendor 在 RTX 4090 上的 **139.48 tok/s**,V100 约为其 1/2.5。
其中一部分差距来自 `ggml_cuda_graph_set_enabled: disabling CUDA graphs due to GPU architecture`
(Volta 上 ggml 自动关闭 CUDA graphs),因此**无法区分**「走 TILE 而损失多少」与「V100 硬件本身的差距」。
若日后想量化补丁代价,可临时用 `-DGGML_CUDA_FA=OFF` 或把 `volta_mma_available()` 改成 false 重编对照
(两者都需要全量重编 ggml-cuda)。

### 回退

```powershell
git -C D:\mt-tool\yue2-V100\audio.cpp checkout -- external/ggml/src/ggml-cuda/fattn.cu
```

重编后即恢复原状。补丁在源码中带有 `[yue2-V100 local patch]` 注释标记,便于升级时定位。

### ⚠️ 编辑注意:该文件是 CRLF(而且本身就是混合行尾)

`fattn.cu` 在 git 里存的是 CRLF(实测 CR=550 / LF=568,即文件内部混用)。若用会"规范化行尾"的编辑器
或脚本改动它、把整个文件转成纯 LF,`git diff` 会显示**整个文件都被改动**(561/550),把真实补丁淹没。

改完后务必确认改动量:

```powershell
git -C D:\mt-tool\yue2-V100\audio.cpp diff --stat
# 期望:1 file changed, 11 insertions(+), 1 deletion(-)
```

本仓库直接改文件时,应保留其原有行尾(用 `[System.IO.File]::ReadAllText` + 按文件自身换行符拼接,
再以 UTF-8 无 BOM 写回)。

---

## Patch 2:ABC 乐谱导出(补上缺失的 detokenizer)

**不是修 bug,是补上游缺的能力。** YuE2 的招牌是"先生成乐谱、再渲染音频",但本移植版把生成的乐谱丢掉了。

### 现象

`cot=full` 规划阶段确实工作(`abc_generated_tokens 874`),但乐谱拿不到:

- `pipeline.cpp:140` 只在**输入**提供 ABC 时写 `out.abc`;模型自己生成的 token 从未被反解码
- `session.cpp:234` 只设置 `result.audio_output` → `--text-out` 直接抛 `task result has no text output`
- 根因:`Yue2TextTokenizer` **只有 `encode()`,没有 `decode()`**

### 改动(10 处锚点,6 个文件,由 `scripts\apply-abc-patch.ps1` 应用)

| 文件 | 改动 |
|---|---|
| `include/engine/models/yue2/tokenizer_text.h` | 声明 `decode(ids)` |
| `src/models/yue2/tokenizer_text.cpp` | 加 `unmap_token_bytes()`(`map_token_bytes` 的逆,反查表由同一个 `unicode_byte_to_utf8` 生成,不会漂移)+ 实现 `decode()`;跳过 `TOKEN_ATTR_CONTROL`(即 `<abc>`/`</abc>`/`<music>` 等结构标记) |
| `include/engine/models/yue2/pipeline.h` | 声明 `last_abc()` |
| `src/models/yue2/pipeline.cpp` | 生成 `abc_ids` 后 decode 存入 `plan.abc`;`run()` 存进 `Impl::last_abc_`;加公开包装 |
| `src/models/yue2/session.cpp` | 非空时写入 `result.text_output`(框架本来就有这个字段,`session.h:197`) |

关键有利条件:**`id_to_token` 反查表在词表加载时已经建好了**(`tokenizer_text.cpp:128`),所以 decode 只需查表 + 反映射 + 拼接,不需要重建词表。

### 验证

1. `cot=full --text-out score.abc` → 导出乐谱,内容应为合法 ABC
2. **往返**:把导出的 `score.abc` 用 `abc_file` 喂回去 → 应能正常生成(证明导出的谱是可用的)
3. `cot=off` 时不应产生 `text_output`(保持为空)

### 回退

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\apply-abc-patch.ps1 -Revert
```

脚本幂等:重复执行会报告 `already applied`,不会重复插入。

---

## 记录约定

- 只改 vendored 第三方代码且必须改动时,才登记在此,并附**回退命令**。
- 补丁一律带可搜索标记:`[yue2-V100 local patch]`。
- 升级 `audio.cpp`(dev 分支更新)时,先 `git stash` 或重新应用本文件中的补丁。
