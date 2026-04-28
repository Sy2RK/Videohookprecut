"""YOLO 商标检测模块 - 逐帧检测视频中的竞品商标"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    import numpy as np

from .config import Config

logger = logging.getLogger("videoprecut.detector")


@dataclass
class Detection:
    """单帧检测结果"""

    frame_number: int  # 帧号
    timestamp: float  # 时间戳（秒）
    confidence: float  # 检测置信度
    class_id: int  # 类别ID
    class_name: str  # 类别名称
    bbox: List[float]  # 边界框 [x_center, y_center, width, height]（归一化）


@dataclass
class VideoDetectionResult:
    """视频检测结果"""

    video_path: str  # 视频路径
    fps: float  # 视频帧率
    total_frames: int  # 总帧数
    duration: float  # 视频时长
    detections: List[Detection] = field(default_factory=list)  # 所有检测结果

    @property
    def has_trademark(self) -> bool:
        """是否检测到商标"""
        return len(self.detections) > 0

    @property
    def trademark_frames(self) -> List[Detection]:
        """获取所有检测到商标的帧"""
        return self.detections


class TrademarkDetector:
    """商标检测器 - 使用 YOLOv8 模型"""

    def __init__(self, config: Config):
        self.config = config
        self._model = None

    def _load_model(self):
        """延迟加载 YOLO 模型"""
        if self._model is not None:
            return

        try:
            from ultralytics import YOLO
        except ImportError:
            raise ImportError(
                "请安装 ultralytics: pip install ultralytics"
            )

        model_path = self.config.model_weights
        logger.info(f"加载 YOLO 模型: {model_path}")

        if not __import__("os").path.exists(model_path):
            logger.warning(
                f"模型权重文件不存在: {model_path}，"
                f"请先运行训练脚本或下载预训练权重"
            )
            # 使用预训练模型作为兜底
            logger.info("使用 YOLOv8n 预训练模型作为基础")
            model_path = "yolov8n.pt"

        self._model = YOLO(model_path)
        logger.info("YOLO 模型加载完成")

    def detect_video(self, video_path: str) -> VideoDetectionResult:
        """对视频进行逐帧商标检测

        Args:
            video_path: mp4 视频文件路径

        Returns:
            VideoDetectionResult 检测结果
        """
        self._load_model()

        # 延迟导入 cv2
        import cv2

        # 打开视频获取基本信息
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0.0
        cap.release()

        logger.info(
            f"开始检测: {video_path} | "
            f"总帧数={total_frames} | FPS={fps:.1f} | "
            f"时长={duration:.1f}s"
        )

        # 使用 YOLO 的视频推理接口（更高效）
        detections = self._detect_with_yolo(video_path, fps, total_frames)

        result = VideoDetectionResult(
            video_path=video_path,
            fps=fps,
            total_frames=total_frames,
            duration=duration,
            detections=detections,
        )

        logger.info(
            f"检测完成: 共检测到 {len(detections)} 个商标帧 "
            f"({len(detections) / max(total_frames, 1) * 100:.1f}% 帧率)"
        )

        return result

    def _detect_with_yolo(
        self,
        video_path: str,
        fps: float,
        total_frames: int,
    ) -> List[Detection]:
        """使用 YOLO 的流式推理接口检测视频

        Args:
            video_path: 视频路径
            fps: 视频帧率
            total_frames: 总帧数

        Returns:
            检测结果列表
        """
        detections = []
        sample_rate = self.config.frame_sample_rate

        # 使用 YOLO 的 stream 模式进行推理
        results_stream = self._model(
            source=video_path,
            conf=self.config.confidence_threshold,
            iou=self.config.iou_threshold,
            device=self.config.device,
            batch=self.config.batch_size,
            stream=True,
            verbose=False,
        )

        frame_idx = 0
        for result in results_stream:
            # 根据采样率决定是否处理该帧
            if frame_idx % sample_rate != 0:
                frame_idx += 1
                continue

            timestamp = frame_idx / fps if fps > 0 else 0.0

            if result.boxes is not None and len(result.boxes) > 0:
                boxes = result.boxes
                for box_idx in range(len(boxes)):
                    conf = float(boxes.conf[box_idx])
                    cls_id = int(boxes.cls[box_idx])
                    cls_name = self._model.names.get(cls_id, f"class_{cls_id}")
                    bbox = boxes.xywhn[box_idx].tolist()  # 归一化的 xywh 格式

                    detection = Detection(
                        frame_number=frame_idx,
                        timestamp=timestamp,
                        confidence=conf,
                        class_id=cls_id,
                        class_name=cls_name,
                        bbox=bbox,
                    )
                    detections.append(detection)

            frame_idx += 1

        return detections

    def detect_frame(self, frame: "np.ndarray", frame_number: int, fps: float) -> List[Detection]:
        """检测单帧图像中的商标

        Args:
            frame: BGR 格式的 numpy 数组
            frame_number: 帧号
            fps: 视频帧率

        Returns:
            该帧的检测结果列表
        """
        self._load_model()

        # 延迟导入
        import numpy as np

        results = self._model(
            source=frame,
            conf=self.config.confidence_threshold,
            iou=self.config.iou_threshold,
            device=self.config.device,
            verbose=False,
        )

        detections = []
        if results and results[0].boxes is not None and len(results[0].boxes) > 0:
            boxes = results[0].boxes
            timestamp = frame_number / fps if fps > 0 else 0.0

            for box_idx in range(len(boxes)):
                conf = float(boxes.conf[box_idx])
                cls_id = int(boxes.cls[box_idx])
                cls_name = self._model.names.get(cls_id, f"class_{cls_id}")
                bbox = boxes.xywhn[box_idx].tolist()

                detection = Detection(
                    frame_number=frame_number,
                    timestamp=timestamp,
                    confidence=conf,
                    class_id=cls_id,
                    class_name=cls_name,
                    bbox=bbox,
                )
                detections.append(detection)

        return detections