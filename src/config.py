"""全局配置模块 - V2"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    """工作流全局配置"""

    # ── 路径配置 ──
    input_dir: str = "input"
    output_dir: str = "output"
    batch_dir: str = ""  # 批次输出目录，运行时自动生成（output/batch_YYYYMMDD_HHMMSS）
    model_weights: str = "models/weights/best.pt"
    trademark_dir: str = "trademarks"
    temp_dir: str = "temp"

    # ── 检测配置 ──
    confidence_threshold: float = 0.5  # YOLO 检测置信度阈值
    iou_threshold: float = 0.45  # NMS 的 IoU 阈值
    frame_sample_rate: int = 1  # 帧采样率 (1=每帧检测, 2=隔帧检测)

    # ── 剪辑配置 ──
    buffer_before_sec: float = 0.3  # 商标片段前缓冲时长(秒)
    buffer_after_sec: float = 0.3  # 商标片段后缓冲时长(秒)
    min_segment_sec: float = 0.1  # 最小商标片段时长(秒)，低于此值忽略
    merge_gap_sec: float = 0.5  # 相邻商标片段间隔小于此值时合并

    # ── 转换配置 ──
    video_codec: str = "libx264"  # 输出视频编码
    audio_codec: str = "aac"  # 输出音频编码
    crf: int = 18  # 恒定质量因子 (0-51, 越小质量越高)
    preset: str = "medium"  # 编码预设

    # ── 硬件配置 ──
    device: str = "0"  # GPU 设备号, "cpu" 表示使用 CPU
    batch_size: int = 16  # YOLO 推理批大小

    # ── 并行配置 ──
    use_parallel: bool = True  # 是否启用并行处理
    max_workers: int = 4  # 最大并行工作进程数
    gpu_ids: List[int] = field(default_factory=lambda: [0])  # 可用 GPU 列表

    # ── 多模态AI配置 ──
    ai_provider: str = "dashscope"  # AI提供商: openai / anthropic / dashscope / local
    ai_api_key: str = ""  # API密钥（从环境变量或配置传入）
    ai_model: str = "qwen-plus-latest"  # 模型名称
    ai_base_url: str = ""  # 自定义API地址（可选，用于兼容接口）
    ai_max_tokens: int = 1024  # 最大生成token数
    ai_temperature: float = 0.3  # 生成温度

    # ── 视频结构分析配置 ──
    frame_sample_count: int = 8  # 结构分析采样帧数
    hook_max_duration: float = 10.0  # Hook最大时长（秒）
    hook_safety_buffer: float = 0.5  # Hook安全缓冲（秒），从AI判断的hook_end提前此值，确保Hook中不含任何Gameplay内容
    hook_min_duration: float = 0.5  # Hook最小时长（秒），低于此值视为无Hook
    hook_description_enabled: bool = True  # 是否启用Hook描述
    discard_no_hook: bool = True  # 无Hook时是否丢弃素材
    discard_no_trademark: bool = True  # 无商标/结束画面时是否丢弃素材
    trademark_min_duration: float = 2.0  # 商标最小时长（秒），低于此值视为AI误判，不算商标
    ffmpeg_timeout: int = 300  # FFmpeg 操作超时时间（秒）

    # ── 支持的视频格式 ──
    supported_formats: List[str] = field(
        default_factory=lambda: [".mp4", ".webm", ".avi", ".mov", ".mkv", ".flv"]
    )

    def to_dict(self) -> dict:
        """转换为字典，用于多进程间传递配置"""
        return {
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "batch_dir": self.batch_dir,
            "model_weights": self.model_weights,
            "trademark_dir": self.trademark_dir,
            "temp_dir": self.temp_dir,
            "confidence_threshold": self.confidence_threshold,
            "iou_threshold": self.iou_threshold,
            "frame_sample_rate": self.frame_sample_rate,
            "buffer_before_sec": self.buffer_before_sec,
            "buffer_after_sec": self.buffer_after_sec,
            "min_segment_sec": self.min_segment_sec,
            "merge_gap_sec": self.merge_gap_sec,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "crf": self.crf,
            "preset": self.preset,
            "device": self.device,
            "batch_size": self.batch_size,
            "use_parallel": self.use_parallel,
            "max_workers": self.max_workers,
            "gpu_ids": self.gpu_ids,
            "ai_provider": self.ai_provider,
            "ai_api_key": self.ai_api_key,
            "ai_model": self.ai_model,
            "ai_base_url": self.ai_base_url,
            "ai_max_tokens": self.ai_max_tokens,
            "ai_temperature": self.ai_temperature,
            "frame_sample_count": self.frame_sample_count,
            "hook_max_duration": self.hook_max_duration,
            "hook_safety_buffer": self.hook_safety_buffer,
            "hook_min_duration": self.hook_min_duration,
            "hook_description_enabled": self.hook_description_enabled,
            "discard_no_hook": self.discard_no_hook,
            "discard_no_trademark": self.discard_no_trademark,
            "trademark_min_duration": self.trademark_min_duration,
            "ffmpeg_timeout": self.ffmpeg_timeout,
            "supported_formats": self.supported_formats,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """从字典创建配置对象"""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def ensure_dirs(self) -> None:
        """确保所有必要目录存在"""
        os.makedirs(self.input_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        if self.batch_dir:
            os.makedirs(self.batch_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.model_weights), exist_ok=True)

    def get_video_output_dir(self, video_stem: str) -> str:
        """获取单个视频的输出目录

        Args:
            video_stem: 视频文件名（不含扩展名）

        Returns:
            视频专属输出目录路径
        """
        return os.path.join(self.batch_dir, video_stem)
