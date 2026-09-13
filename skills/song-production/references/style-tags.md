# 风格标签:词表、映射规则、校验

## 三条硬规则

1. **标签必须全英文。** 模型的 `[Tags]` 段是按英文标签训练的;中文只是界面显示层。
   界面上显示 `city pop · 城市流行`,下发给模型的**只有** `city pop`。
2. **词表是开放的** —— 不在精选词表里的词(如 `dream pop`、`trip hop`)**模型照样接受**。
   精选词表只是方便点选,不是白名单。
3. **`[Tags]` 与 `[Verse]/[Chorus]/[Instrumental]` 都不可翻译**。改了模型就不认。

## 精选词表(225 条,`examples/style_tags.json`)

| 类别 | 数量 | 作用 | 例 |
|---|---|---|---|
| **language** | 11 | **最关键**,决定唱什么语言 | `Mandarin` `Cantonese` `Japanese` `English` |
| genre | 71 | 曲风,顺带决定编曲密度 | `city pop` `shoegaze` `trap soul` `ambient` |
| instrument | 48 | 配器 | `acoustic guitar` `Rhodes` `808 bass` `二胡` |
| mood | 52 | 情绪 | `dreamy` `melancholic` `spacious` `nostalgic` |
| gender | 9 | 人声性别/声部 | `female` `male` `soprano` `童声` |
| timbre | 34 | 音色/唱法 | `breathy vocal` `whispery vocal` `husky vocal` |

中文对照在 `examples/style_tags_zh.json`(198 条英文标签都有中文解释)。

**上游推荐的五要素组合**:语言 + 曲风 + 乐器 + 情绪 + 音色。
例:`Mandarin, city pop, electric piano, groovy bass, warm vocal, nostalgic`

## 校验

```powershell
& $PY $ROOT\scripts\test-style-tags.py
```

它保证:每个英文标签都有中文解释、默认风格全部命中词表、预设零残留、往返一致。
**写进 `style` 的标签不要求都在词表内**,但**必须英文**——这一条没有例外。

## 特征 → 标签的映射规则

来自 `scripts/analyze-track.py`。这些是**启发式**,不是定律;拿它当"起点建议",不是结论。

| 测到的 | 给的标签 | 依据 |
|---|---|---|
| BPM < 75 | `calm` `ambient` | 很慢 |
| BPM 75–105 | `chillout` `dreamy` | 中慢 |
| BPM 105–135 | `groovy` `danceable` | 中快 |
| BPM ≥ 135 | `energetic` `upbeat` | 快 |
| 立体声宽度 ≥ 0.35 | `spacious` `atmospheric` | 宽而散 |
| 立体声宽度 ≤ 0.18 | `meditative` | 干而集中 |
| >5 kHz 能量 < 0.5% | `relaxing` | 偏暗/柔 |
| >5 kHz 能量 > 2% | `bright`(词表外) | 偏亮 |
| 逐秒音量起伏 ≥ 7 dB | `powerful` `intense` | 动态大 |
| 逐秒音量起伏 ≤ 2.5 dB | `meditative` | 平稳 |
| 调性为小调 | `melancholic` | **弱推断**,模板相关 ≥0.5 才给 |
| 调性为大调 | `uplifting` | **弱推断** |
| 拍内强弱分明 | `groovy` | 有律动 |
| MuScriptor 标出的乐器 | 对应 instrument 标签 | 直接映射 |

### 两个必须注意的点

**① BPM 有倍频歧义,两个档位的标签都要给。**
`zh-01` 的节拍网格是 117.5 BPM,但自相关在半速 58.7 处更强。**谁对取决于人怎么打拍子**,
所以脚本把两档标签都列出来,由用户听。不要替用户决定。

**② 测的是整轨,不是人声。**
频谱特征由编曲主导,**推不出唱法**。不要从"高频占比低"得出"唱得虚"。

## 两个交叉验证的实例(证明测量可信)

| 曲目 | librosa | MuScriptor | 一致 |
|---|---|---|---|
| `zh-01` 城市流行 | 117.5 BPM(拍网格) | 116.8 BPM(鼓点细化) | 差 0.6% |
| `dream-A` dream pop | 80.7(半速读数) | 79.9 | 差 1.0% |

两个**独立方法**互证。如果只靠"听"或只靠单一工具,拿不到这种一致性。

## 时长与速度是两件事

- **时长**:有精确控制(1 秒 = 25 语义 token),见主文件。
- **速度(BPM)**:**没有参数**。只能靠标签(`upbeat` / `dreamy` …)粗略影响。
  理论上 ABC 的 `Q:`(速度)与 `M:`(拍号)字段可能影响生成,**但未验证**,不要当既有能力承诺。
