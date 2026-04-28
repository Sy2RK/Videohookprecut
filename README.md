# Videoprecut - 竞品广告素材分析工作流 V2

自动分析竞品广告素材的视频结构，识别 Hook/Gameplay/商标三段式结构，拆分存档并生成结构化描述。

## 功能特性

- 📁 **自动扫描**：读取 input/ 目录中的视频文件
- 🔄 **格式转换**：非 mp4 格式自动转换为 mp4
- 🧠 **AI结构分析**：多模态AI模型检测 Hook/Gameplay/商标三段式结构
- 🎯 **YOLO商标检测**：精确定位商标帧，微调AI分析结果
- ✂️ **智能分段**：Hook + Gameplay 独立导出，商标片段丢弃
- 📝 **Hook描述**：AI分析Hook视觉元素，生成一句话描述
- 📊 **JSON元数据**：结构化存储每个视频的分析结果
- ⚡ **并行处理**：多进程并行加速批量视频处理

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

**无Hook的素材直接丢弃。**

## 快速开始

### 1. 安装依赖

```bash
# 系统依赖：FFmpeg
brew install ffmpeg          # macOS
# apt install ffmpeg         # Ubuntu

# Python 依赖
pip install -r requirements.txt

# 多模态AI依赖（按需安装）
pip install openai           # 使用 GPT-4o
# pip install anthropic      # 使用 Claude Vision
```

### 2. 配置AI

```bash
# 方式1: 命令行参数
python -m src.main --ai-provider openai --ai-api-key sk-xxx

# 方式2: 环境变量
export OPENAI_API_KEY=sk-xxx
python -m src.main --ai-provider openai
```

### 3. 处理视频

```bash
# 将待处理视频放入 input/ 目录

# 基本使用（OpenAI GPT-4o）
python -m src.main --ai-api-key sk-xxx

# 使用 Anthropic Claude
python -m src.main --ai-provider anthropic --ai-model claude-sonnet-4-20250514 --ai-api-key sk-xxx

# 使用本地模型（需自行部署 OpenAI 兼容接口）
python -m src.main --ai-provider local --ai-base-url http://localhost:8000/v1

# 并行处理
python -m src.main --parallel --workers 4 --ai-api-key sk-xxx

# 不丢弃无Hook素材
python -m src.main --no-discard-no-hook --ai-api-key sk-xxx
```

## 输出结构

```
output/
├── hooks/                    # Hook 视频输出
│   ├── abc123_hook.mp4
│   └── ...
├── gameplay/                 # Gameplay 视频输出
│   ├── abc123_gameplay.mp4
│   └── ...
└── metadata/                 # JSON 元数据
    ├── abc123.json
    └── ...
```

### JSON 元数据格式

```json
{
  "filename": "abc123.mp4",
  "has_hook": true,
  "hook_description": "一只卡通猫咪在彩色障碍赛道上奔跑的炫酷画面",
  "segments": {
    "hook": { "start": 0.0, "end": 3.5, "duration": 3.5 },
    "gameplay": { "start": 3.5, "end": 28.7, "duration": 25.2 },
    "trademark": { "start": 28.7, "end": 30.8, "duration": 2.1 }
  },
  "video_info": { "width": 1080, "height": 1920, "fps": 30.0, "total_duration": 30.8 },
  "ai_confidence": 0.85,
  "yolo_refined": true,
  "processing_time": 12.5
}
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--input` | `input` | 输入视频目录 |
| `--output` | `output` | 输出根目录 |
| `--model` | `models/weights/best.pt` | YOLO模型权重路径 |
| `--ai-provider` | `openai` | AI提供商 (openai/anthropic/local) |
| `--ai-api-key` | | AI API密钥 |
| `--ai-model` | `gpt-4o` | AI模型名称 |
| `--ai-base-url` | | 自定义AI API地址 |
| `--ai-temperature` | `0.3` | AI生成温度 |
| `--conf` | `0.5` | YOLO检测置信度阈值 |
| `--sample-count` | `8` | 视频结构分析采样帧数 |
| `--crf` | `18` | 视频质量CRF值 |
| `--hook-max-duration` | `10.0` | Hook最大时长(秒) |
| `--no-discard-no-hook` | | 不丢弃无Hook的素材 |
| `--no-hook-description` | | 禁用Hook元素描述 |
| `--parallel` | `True` | 启用并行处理 |
| `--workers` | `4` | 最大工作进程数 |
| `--gpus` | `0` | 可用GPU列表 |

## 项目结构

```
Videoprecut/
├── input/                        # 输入视频目录
├── output/
│   ├── hooks/                    # Hook 视频输出
│   ├── gameplay/                 # Gameplay 视频输出
│   └── metadata/                 # JSON 元数据
├── src/
│   ├── main.py                   # 主入口（V2工作流）
│   ├── config.py                 # 全局配置（含AI配置）
│   ├── analyzer.py               # 多模态AI分析器
│   ├── structurer.py             # 视频结构分析（Hook/Gameplay/商标分段）
│   ├── detector.py               # YOLO 商标检测
│   ├── segmenter.py              # 片段分析
│   ├── editor.py                 # 视频剪辑（分段导出）
│   ├── ingestion.py              # 视频读取与格式检查
│   ├── converter.py              # 视频格式转换
│   ├── parallel.py               # 多进程并行处理
│   ├── trainer.py                # YOLO模型训练脚本
│   └── utils.py                  # 工具函数（帧采样等）
├── models/                       # YOLO模型和数据集
├── trademarks/                   # 商标样本图片
├── logs/                         # 处理日志
└── plans/                        # 设计文档
```

## 处理流程

```
输入视频 → 格式检查/转换 → AI采样关键帧 → 多模态AI分析结构 →
判断是否有Hook(无则丢弃) → YOLO精确定位商标(可选) →
剪切Hook视频 → 剪切Gameplay视频 → AI分析Hook元素 →
生成JSON元数据 → 输出到output/
```

## YOLO模型训练（可选）

YOLO用于精确定位商标帧，微调AI分析结果。不训练模型也能运行（仅使用AI分析）。

```bash
# 训练模型
python -m src.trainer --data models/dataset/dataset.yaml --epochs 100
```

## 许可证

MIT License
