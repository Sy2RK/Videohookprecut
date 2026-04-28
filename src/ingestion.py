"""视频读取与格式检查模块"""

import logging
import os
from dataclasses import dataclass
from typing import List

from .config import Config
from .utils import get_video_info

logger = logging.getLogger("videoprecut.ingestion")


@dataclass
class VideoInfo:
    """视频文件信息"""

    filepath: str  # 完整文件路径
    filename: str  # 文件名（含扩展名）
    stem: str  # 文件名（不含扩展名）
    extension: str  # 扩展名（含点号，如 .mp4）
    is_mp4: bool  # 是否为 mp4 格式
    duration: float  # 时长（秒）
    fps: float  # 帧率
    width: int  # 宽度
    height: int  # 高度
    has_video: bool  # 是否包含视频轨
    has_audio: bool  # 是否包含音频轨
    video_codec: str  # 视频编码
    audio_codec: str  # 音频编码


def scan_input_dir(config: Config) -> List[VideoInfo]:
    """扫描输入目录，返回视频文件信息列表"""
    input_dir = config.input_dir
    if not os.path.isdir(input_dir):
        logger.error(f"输入目录不存在: {input_dir}")
        return []

    videos = []
    for filename in sorted(os.listdir(input_dir)):
        filepath = os.path.join(input_dir, filename)
        if not os.path.isfile(filepath):
            continue

        _, ext = os.path.splitext(filename)
        ext = ext.lower()

        if ext not in config.supported_formats:
            logger.debug(f"跳过不支持的文件格式: {filename}")
            continue

        video_info = get_video_info(filepath)
        if video_info is None:
            logger.warning(f"无法获取视频信息，跳过: {filename}")
            continue

        vi = VideoInfo(
            filepath=filepath,
            filename=filename,
            stem=os.path.splitext(filename)[0],
            extension=ext,
            is_mp4=(ext == ".mp4"),
            duration=video_info["duration"],
            fps=video_info["fps"],
            width=video_info["width"],
            height=video_info["height"],
            has_video=video_info["has_video"],
            has_audio=video_info["has_audio"],
            video_codec=video_info["video_codec"],
            audio_codec=video_info["audio_codec"],
        )
        videos.append(vi)
        logger.info(
            f"发现视频: {filename} | "
            f"时长={vi.duration:.1f}s | "
            f"分辨率={vi.width}x{vi.height} | "
            f"FPS={vi.fps:.1f} | "
            f"格式={ext}"
        )

    logger.info(f"共发现 {len(videos)} 个视频文件")
    return videos