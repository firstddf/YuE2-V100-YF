# 模型权重:许可与获取

## 本仓库不分发任何权重

权重合计约 **4.7 GB**,而且**许可各不相同、有的不允许再分发**,所以它们不在仓库里。
下载脚本只负责**从原始出处拉取**,下载与使用由你自行接受对应许可。

## 本项目实际使用的两份

| 模型 | 用途 | 大小 | 原始许可 | 能再分发吗 |
|---|---|---|---|---|
| `Yue2-3B-GGUF` | 歌曲生成 | 4.22 GB | **无声明** | ❌ **不能推定可以** |
| `MuScriptor-Small-GGUF` | 参考曲转谱 | 393 MB | **CC-BY-NC-4.0** | ❌ **非商用** |

### ⚠️ 关于 YuE2 权重的一处许可空白

`audio-cpp/audio.cpp-gguf` 仓库的 README 末尾写着:

> "Use and redistribution are governed by the corresponding original model license **listed above**."

但逐行核对后:**YuE2 既不在它的 `base_model` 列表(60 项),也不在 license 表(73 行)里。**
也就是说那份 GGUF 的"corresponding original model license"**根本不存在**。

同时:
- 官方 `m-a-p/YuE2-3B` 的模型卡里**只有 `language:`,没有 license 字段**;
- 第三方 `mrfakename/YuE2-3B` 标的是 **cc-by-nc-4.0**。

**结论:YuE2 权重的分发条款是空的,"无许可 = 保留所有权利",不能推定可以再分发。**
本项目因此不打包、不镜像、不转存该权重,只提供原始出处。

> 对比:YuE **v1** 的权重(`m-a-p/YuE-s1-7B-anneal-en-cot`、`m-a-p/YuE-s2-1B-general`)
> 明确是 **Apache-2.0**。但本项目用的是 YuE2,不是 v1。

## 下载

```powershell
# 本项目用的两份(会用到 huggingface_hub;Windows 上 pip 装不了包时见 docs/muscriptor.md 的绕法)
D:\mt-tool\runtime\Scripts\python.exe scripts\fetch_weights.py
```

| 权重 | 出处 |
|---|---|
| YuE2 3B | <https://huggingface.co/audio-cpp/Yue2-3B-GGUF> |
| MuScriptor Small | <https://huggingface.co/audio-cpp/audio.cpp-gguf>(目录 `MuScriptor-Small-GGUF/`) |

## 以后可能加进来的(同一仓库,许可已查明)

这些**没有**随本项目分发,只是记录许可,便于将来决定是否启用:

| 模型 | 用途 | 原始许可 |
|---|---|---|
| `HTDemucs-GGUF` | 人声分离 | **MIT** |
| `BS-RoFormer-ep368-GGUF` | 人声分离 | **Apache-2.0** |
| `Mel-Band-RoFormer-GGUF` | 人声分离 | **MIT** |
| `ACE-Step1.5-GGUF` | 参考音频翻唱 / 改编 | **MIT** |
| `SeedVC-MLX-GGUF` | 歌声转换(换音色) | **GPL-3.0** |

> 注意:`SeedVC-MLX-GGUF` 的权重是 **GPL-3.0** —— 与本项目的 GPL-3.0-or-later **同向**,
> 引入时不产生新的许可冲突。
>
> 上表来源:`audio-cpp/audio.cpp-gguf` 的 README 许可表(逐行核对过)。

## 为什么这件事要写这么细

因为"没写许可"和"许可宽松"是两件事,而**默认是后者被误当成前者**。
把原始的许可与出处逐项记下来,别人要再分发时才有依据可查 ——
这也是 `audio.cpp` 自己的做法,本项目照抄这个模式。
