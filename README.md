# Videoprecut - 竞品广告素材分析工作流 V2

自动分析竞品广告素材的视频结构，识别 Hook/Gameplay/商标三段式结构，拆分存档并生成结构化描述。

## 功能特性

- 📁 **自动扫描**：递归读取 input/ 目录中的视频文件（支持产品子目录）
- 🔄 **格式转换**：非 mp4 格式自动转换为 mp4
- 🧠 **AI结构分析**：多模态AI模型检测 Hook/Gameplay/商标三段式结构
- ✂️ **智能分段**：Hook + Gameplay 独立导出，商标片段丢弃
- 📝 **Hook描述**：AI分析Hook视觉元素，生成描述/情感/过渡方式
- 📊 **JSON元数据**：结构化存储每个视频的分析结果
- 🔗 **飞书导入**：从飞书多维表格批量导入视频URL

## 视频结构模型

```
典型竞品广告素材：

┌──────────┬──────────────────────────────┬──────────────┐
│   Hook    │     Gameplay（玩法展示）      │  商标/Try Now │
│  纯图像   │  带hook元素的玩法演示          │   结尾画面    │
│  几秒钟   │  几十秒                       │   几秒       │
└──────────┴──────────────────────────────┴──────────────┘
     ↓                    ↓                     ↓
  保存hook视频      保存gameplay视频          丢弃
```

**无Hook或无商标的素材直接丢弃。**

## 快速开始

### 1. 安装依赖

```bash
# 系统依赖：FFmpeg
brew install ffmpeg          # macOS
# apt install ffmpeg         # Ubuntu

# Python 依赖
pip install -r requirements.txt

# 多模态AI依赖（按需安装）
pip install openai           # 使用 GPT-4o / DashScope
# pip install anthropic      # 使用 Claude Vision
```

### 2. 配置AI

```bash
# 方式1: 命令行参数
python -m src.main --ai-provider dashscope --ai-api-key sk-xxx

# 方式2: 环境变量（.env 文件）
DASHSCOPE_API_KEY=sk-xxx
```

### 3. 处理视频

```bash
# 将待处理视频放入 input/ 目录（支持产品子目录）
# input/
# ├── Arrow Maze/
# │   ├── video1.mp4
# │   └── video2.mp4
# └── Arrow Out/
#     └── video3.mp4

# 基本使用（DashScope，默认）
python -m src.main --ai-api-key sk-xxx

# 使用 OpenAI GPT-4o
python -m src.main --ai-provider openai --ai-api-key sk-xxx

# 使用 Anthropic Claude
python -m src.main --ai-provider anthropic --ai-model claude-sonnet-4-20250514 --ai-api-key sk-xxx

# 限制处理数量
python -m src.main --limit 5 --ai-api-key sk-xxx

# 不丢弃无Hook素材
python -m src.main --no-discard-no-hook --ai-api-key sk-xxx
```

### 4. 从飞书导入视频

```bash
# 从飞书多维表格导入视频URL到 input/ 目录
python -m src.bitable_import --app-token xxx --table-id xxx
```

## 输出结构

```
output/
└── batch_20260428_180000/       # 按批次组织
    ├── 58acfd1090e646678b92c7e41fcaface/
    │   ├── hook.mp4             # Hook 视频片段
    │   ├── gameplay.mp4         # Gameplay 视频片段
    │   └── analysis.json        # 结构化分析结果
    └── abc123def456/
        ├── hook.mp4
        ├── gameplay.mp4
        └── analysis.json
```

### JSON 元数据格式

```json
{
  "filename": "58acfd1090e646678b92c7e41fcaface.mp4",
  "product": "Arrow Maze - Escape Puzzle",
  "has_hook": true,
  "hook_description": "一位戴帽子的老年男性角色坐在彩色箭头迷宫前，通过对话气泡强调游戏免费且无广告",
  "hook_emotion": "安心、轻松",
  "hook_transition": "人物形象与文字气泡直接消失，画面聚焦于背景迷宫并出现鼠标指针开始演示玩法",
  "segments": {
    "hook": { "start": 0.0, "end": 3.5, "duration": 3.5 },
    "gameplay": { "start": 3.5, "end": 36.0, "duration": 32.5 },
    "trademark": { "start": 36.0, "end": 40.0, "duration": 4.0 }
  },
  "video_info": { "width": 426, "height": 640, "fps": 30.0, "total_duration": 40.0 },
  "ai_confidence": 0.95,
  "processing_time": 37.3
}
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--input` | `input` | 输入视频目录 |
| `--output` | `output` | 输出根目录 |
| `--limit` | | 限制处理视频数量 |
| `--ai-provider` | `dashscope` | AI提供商 (openai/anthropic/dashscope/local) |
| `--ai-api-key` | | AI API密钥 |
| `--ai-model` | `qwen-plus-latest` | AI模型名称 |
| `--ai-base-url` | | 自定义AI API地址 |
| `--ai-temperature` | `0.3` | AI生成温度 |
| `--sample-count` | `8` | 视频结构分析采样帧数 |
| `--crf` | `18` | 视频质量CRF值 |
| `--preset` | `medium` | 编码预设 |
| `--buffer` | `0.3` | 剪切缓冲时长(秒) |
| `--hook-max-duration` | `10.0` | Hook最大时长(秒) |
| `--no-discard-no-hook` | | 不丢弃无Hook的素材 |
| `--no-discard-no-trademark` | | 不丢弃无商标的素材 |
| `--no-hook-description` | | 禁用Hook元素描述 |
| `--no-parallel` | | 禁用并行处理 |
| `--workers` | `4` | 最大工作进程数 |

## 项目结构

```
Videoprecut/
├── input/                        # 输入视频目录（支持产品子目录）
├── output/                       # 输出目录（按批次组织）
├── src/
│   ├── main.py                   # 主入口（V2工作流编排）
│   ├── config.py                 # 全局配置
│   ├── analyzer.py               # 多模态AI分析器
│   ├── structurer.py             # 视频结构分析（Hook/Gameplay/商标分段）
│   ├── editor.py                 # 视频剪辑（分段导出）
│   ├── ingestion.py              # 视频读取与格式检查
│   ├── converter.py              # 视频格式转换
│   ├── parallel.py               # 多进程并行处理
│   ├── bitable_import.py         # 飞书多维表格视频导入
│   └── utils.py                  # 工具函数（帧采样等）
├── bitable_video_import/         # 飞书多维表格插件
├── logs/                         # 处理日志
└── plans/                        # 设计文档
```

## 处理流程

```
输入视频 → 格式检查/转换 → 多模态AI分析结构(视频直传) →
判断是否有Hook(无则丢弃) → 判断是否有商标(无则丢弃) →
剪切Hook视频 → 剪切Gameplay视频 → AI分析Hook元素(描述/情感/过渡) →
生成JSON元数据 → 输出到output/batch_XXX/
```

## 许可证

MIT License
