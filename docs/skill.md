# DSH 技能:`song-production`

让 DSH(DeepSeek Harness)按**固化流程 + 已知边界**做歌,而不是每次凭临场发挥。

## 它是什么(以及不是什么)

**是**:一个**指令包** —— 一个 `SKILL.md`(YAML frontmatter + 正文)+ `references/` 里的按需资料。
会话开始时 DSH 只把 **name + description** 放进技能目录;真正需要时才加载正文。

**不是**:可执行程序。执行仍然靠引擎与脚本(`audiocpp_cli` / `analyze-track.py` / ffmpeg 链)。
技能负责的是**流程与判断**,不负责算力。

## 为什么要有它

因为**边界是花了很多实测才换来的**,而每次对话都可能忘:

| 边界 | 代价 |
|---|---|
| 气声/喘息只能到"哼唱" | 你不满意的那一轮 |
| 段落留空**不会**让人声退场,要用 `[Instrumental]` | 我推错、被 ASR 时间戳纠正 |
| 风格标签的影响是**概率性**的 | 全程观察 |
| ABC 是"计划",不等于音频 | 乐谱里人声部有旋律线但根本没唱 |
| 若隐若现只能整轨 | 氛围实验 |
| **DSH 听不到音频** | harness 输入模态只有 text 与 image |

技能把这些写死,**任何会话加载它之后都不会再承诺做不到的事**。

## 怎么装

**规范副本在仓库里:`skills/song-production/`。** DSH 只在自己根目录下找技能
(`.dsh/skills` 或 `.agents/skills`,相对**会话 cwd**),所以必须安装一次:

```powershell
# 安装(把 skills\ 下的所有技能复制到 D:\mt-tool\.dsh\skills\)
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-skill.ps1

# 只检查是否同步(不改文件;不一致时退出码 1)
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-skill.ps1 -Check
```

**方向很重要**:改**项目里**的副本,然后跑安装脚本。
直接改 `.dsh/skills/` 下的文件会在下次安装时被覆盖。

安装后 DSH 的技能目录会**热刷新**,不用重启会话。

## 怎么触发

| 方式 | 可用性 |
|---|---|
| **自然语言**:「参考这首歌做一首类似的」「帮我做首歌」 | ✅ 任何入口都行(已把 `disable-model-invocation` 设为 `false`) |
| **斜杠命令** `/skill:song-production` | ⚠️ **只在 TUI 里**;Web GUI 没有斜杠命令 |
| headless:`dsh --profile headless "…"` | ⚠️ 架构上可行,需要 `DEEPSEEK_API_KEY` |

> 注意命令语法是 **`/skill:<名字>`**,不是 `/<名字>`。
> 这条来自一份**已归档**的设计记录(TUI skill slash command,归档于 2026-08-04),
> 归档件是历史快照、不保证是现状;但实际观察与它一致。

**因此:在 Web GUI 里,模型调用是加载技能的唯一途径。**
把 `disable-model-invocation` 设为 `false` 不是可选优化,而是必须的 —— 否则 Web 端根本用不到。

## 内容结构

```
skills/song-production/
├─ SKILL.md                    两条铁律 + 边界表 + 7 步流程 + 时长换算 + 交付清单
└─ references/
   ├─ commands.md              命令速查(生成/分析/转谱/后期/校验)+ 排障
   ├─ style-tags.md            225 条词表分类、特征→标签映射规则、两个注意点
   ├─ vocal-styles.md          6 种演绎实测状态、气声写法、歌词语法
   └─ mixing.md                氛围后期:四项对应表 + 滤镜串 + 两个必踩的坑
```

`references` 是**按需加载**的 —— 正文里只在需要时引用,不会一次性全塞进上下文。

## 怎么确认它注册了

调用技能时会得到**两种不同**的报错,这正好用来判断状态:

| 现象 | 含义 |
|---|---|
| `is unknown or no longer available` | 名字不存在(**没装上**) |
| `is not available for model invocation` | 名字**存在**,但策略不允许模型加载(即 `disable-model-invocation: true`) |
| 正常返回 `<skill_content>` | 已注册且允许模型调用 ✅ |

## 怎么改

1. 改 `skills/song-production/` 下的文件(通常只改 `SKILL.md` 或某个 reference)
2. 跑 `scripts\install-skill.ps1`
3. 用 `-Check` 确认同步
4. 改完顺手看一眼 frontmatter:`name` 必须与**目录名**一致,
   `description` 是 DSH 路由的唯一依据(讲清"什么时候用")

## 相关

- 界面侧同样能力的入口见 [`gui.md`](gui.md) 的「🔍 参考曲分析」页签
- 转谱工具的实测数据与陷阱见 [`muscriptor.md`](muscriptor.md)
