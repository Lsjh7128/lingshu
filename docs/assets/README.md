# docs/assets · 灵枢架构图（豆包生图）

| 项 | 内容 |
|---|---|
| 成品 | [`lingshu-architecture-doubao.png`](lingshu-architecture-doubao.png)（1800×2400，3:4 竖版，真 PNG，约 2.4 MB） |
| 生成方式 | 火山方舟（ARK）图像生成 API `POST /api/v3/images/generations` |
| 模型 | `doubao-seedream-5-0-pro-260628` |
| 参数 | `size=1800x2400`（该模型单图面积上限 4 622 420 px，1800×2400 = 4 320 000 在限内）、`watermark=false` |
| 提示词 | [`arch-prompt-v7.txt`](arch-prompt-v7.txt)（最终版） |
| 内容口径 | 依据 [`../00-架构总览.md`](../00-架构总览.md)：宪法层 → 元认知/目标与意图/关系模型/叙事整合 → 记忆（唯一写方）/情感/用户情感感知（影子）→ 合成器 → 宿主核心 → 可观测性；右侧虚线旁路为「学习机制 L0–L3 · 只产生提议」 |

## 复现

```powershell
cd <REPO_ROOT>
node scripts/gen_arch_image.mjs --prompt-file docs/assets/arch-prompt-v7.txt `
  --out docs/assets/lingshu-architecture-doubao.png --size 1800x2400
```

脚本从 `~/.dsh/volc_ark.json` 读取 `api_key` / `model` / `endpoint`，也可用 `--model` 覆盖。

## 迭代记录（提示词草稿）

`arch-prompt-v1.txt` … `arch-prompt-v7.txt` 是逐轮迭代稿，差异集中在版式约束与文字精度：

- v1：首版，模型自动选成 9:19 手机长条，右侧注释贴边；
- v2：改 3:4，主数据流居中轴，文字全对，但右侧「学习机制」注释略出画；
- v3：右侧改为窄卡，注释完整但层间对齐被打散，标题多出 `^` 符号；
- v4：竖排注释完整，但「只读约束」被写成「读读约束」；
- v5：修正错字，注释完整，但注释为旋转 90° 的竖排；
- v6：版式干净、注释水平可读，但模型自行补了 `lingshu.org` 等虚构页脚；
- v7：在 v6 基础上禁止网址/页脚，**采用为成品**。

## 使用注意

- 图中文字由图像模型渲染，虽已逐字核对（宪法层、元认知、目标与意图、关系模型、叙事整合、记忆、情感、用户情感感知、合成器、宿主核心、可观测性、学习机制、输入/输出），仍需以文档为准；图像模型每次重跑结果不同，不保证逐像素可复现。
- 本图是**概念架构图**，不是精确的接口/时序图；接口与契约以 `docs/19-控制论重构契约.md` 与 `rules/*.yaml` 为准。
- 图内括号副标题（认知监控/目标建模/关系推理/叙事生成/情感模型）为模型按语义补注，非文档原文。
