"""YOLO 模型训练脚本

提供 YOLOv8 模型训练、验证、导出的完整流程。
"""

import argparse
import logging
import os
import sys

logger = logging.getLogger("videoprecut.trainer")


def train_model(
    data_yaml: str,
    model_size: str = "n",
    epochs: int = 100,
    batch_size: int = 16,
    imgsz: int = 640,
    device: str = "0",
    project: str = "models",
    name: str = "trademark_train",
    resume: bool = False,
) -> str:
    """训练 YOLOv8 商标检测模型

    Args:
        data_yaml: 数据集配置文件路径 (dataset.yaml)
        model_size: 模型大小 (n/s/m/l/x)
        epochs: 训练轮数
        batch_size: 批大小
        imgsz: 输入图像尺寸
        device: 设备 (GPU ID 或 "cpu")
        project: 项目保存目录
        name: 实验名称
        resume: 是否从上次中断处继续训练

    Returns:
        最佳权重文件路径
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("请安装 ultralytics: pip install ultralytics")
        sys.exit(1)

    # 验证数据集配置文件
    if not os.path.exists(data_yaml):
        raise FileNotFoundError(f"数据集配置文件不存在: {data_yaml}")

    # 选择预训练模型
    model_name = f"yolov8{model_size}.pt"
    logger.info(f"使用预训练模型: {model_name}")

    # 加载模型
    if resume:
        # 从上次训练中断处继续
        last_weights = os.path.join(project, name, "weights", "last.pt")
        if os.path.exists(last_weights):
            model = YOLO(last_weights)
            logger.info(f"从断点恢复训练: {last_weights}")
        else:
            logger.warning(f"未找到断点权重: {last_weights}，从头开始训练")
            model = YOLO(model_name)
    else:
        model = YOLO(model_name)

    # 开始训练
    logger.info(
        f"开始训练: epochs={epochs}, batch={batch_size}, "
        f"imgsz={imgsz}, device={device}"
    )

    results = model.train(
        data=data_yaml,
        epochs=epochs,
        batch=batch_size,
        imgsz=imgsz,
        device=device,
        project=project,
        name=name,
        resume=resume,
        # 数据增强参数
        hsv_h=0.015,     # 色调增强
        hsv_s=0.7,       # 饱和度增强
        hsv_v=0.4,       # 明度增强
        degrees=10.0,     # 旋转角度
        translate=0.1,    # 平移
        scale=0.5,        # 缩放
        flipud=0.0,       # 上下翻转概率
        fliplr=0.5,       # 左右翻转概率
        mosaic=1.0,       # Mosaic 数据增强
        mixup=0.1,        # MixUp 数据增强
    )

    # 获取最佳权重路径
    best_weights = os.path.join(project, name, "weights", "best.pt")
    if os.path.exists(best_weights):
        # 复制到标准位置
        target_dir = "models/weights"
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, "best.pt")

        import shutil
        shutil.copy2(best_weights, target_path)
        logger.info(f"最佳权重已保存到: {target_path}")
    else:
        logger.warning("训练完成但未找到 best.pt 权重文件")

    return best_weights


def validate_model(
    weights: str = "models/weights/best.pt",
    data_yaml: str = "models/dataset/dataset.yaml",
    device: str = "0",
) -> dict:
    """验证模型精度

    Args:
        weights: 模型权重路径
        data_yaml: 数据集配置文件路径
        device: 设备

    Returns:
        验证结果字典
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("请安装 ultralytics: pip install ultralytics")
        sys.exit(1)

    if not os.path.exists(weights):
        raise FileNotFoundError(f"权重文件不存在: {weights}")

    model = YOLO(weights)
    results = model.val(data=data_yaml, device=device)

    metrics = {
        "mAP50": float(results.box.map50),
        "mAP50-95": float(results.box.map),
        "precision": float(results.box.mp),
        "recall": float(results.box.mr),
    }

    logger.info("模型验证结果:")
    logger.info(f"  mAP@0.5: {metrics['mAP50']:.4f}")
    logger.info(f"  mAP@0.5:0.95: {metrics['mAP50-95']:.4f}")
    logger.info(f"  Precision: {metrics['precision']:.4f}")
    logger.info(f"  Recall: {metrics['recall']:.4f}")

    return metrics


def export_model(
    weights: str = "models/weights/best.pt",
    format: str = "onnx",
) -> str:
    """导出模型为其他格式

    Args:
        weights: 模型权重路径
        format: 导出格式 (onnx/tensorrt/openvino 等)

    Returns:
        导出文件路径
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("请安装 ultralytics: pip install ultralytics")
        sys.exit(1)

    if not os.path.exists(weights):
        raise FileNotFoundError(f"权重文件不存在: {weights}")

    model = YOLO(weights)
    export_path = model.export(format=format)
    logger.info(f"模型已导出: {export_path}")

    return str(export_path)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="YOLOv8 商标检测模型训练")
    parser.add_argument(
        "--data", type=str, default="models/dataset/dataset.yaml",
        help="数据集配置文件路径"
    )
    parser.add_argument(
        "--model-size", type=str, default="n", choices=["n", "s", "m", "l", "x"],
        help="模型大小 (n=nano, s=small, m=medium, l=large, x=xlarge)"
    )
    parser.add_argument("--epochs", type=int, default=100, help="训练轮数")
    parser.add_argument("--batch", type=int, default=16, help="批大小")
    parser.add_argument("--imgsz", type=int, default=640, help="输入图像尺寸")
    parser.add_argument("--device", type=str, default="0", help="设备 (GPU ID 或 cpu)")
    parser.add_argument("--project", type=str, default="models", help="项目保存目录")
    parser.add_argument("--name", type=str, default="trademark_train", help="实验名称")
    parser.add_argument("--resume", action="store_true", help="从断点恢复训练")
    parser.add_argument(
        "--validate", action="store_true",
        help="仅验证模型，不训练"
    )
    parser.add_argument(
        "--export", type=str, default=None,
        help="导出模型格式 (onnx/tensorrt/openvino)"
    )

    args = parser.parse_args()

    # 配置日志
    from .utils import setup_logging
    setup_logging()

    if args.validate:
        validate_model(
            weights="models/weights/best.pt",
            data_yaml=args.data,
            device=args.device,
        )
    elif args.export:
        export_model(
            weights="models/weights/best.pt",
            format=args.export,
        )
    else:
        train_model(
            data_yaml=args.data,
            model_size=args.model_size,
            epochs=args.epochs,
            batch_size=args.batch,
            imgsz=args.imgsz,
            device=args.device,
            project=args.project,
            name=args.name,
            resume=args.resume,
        )


if __name__ == "__main__":
    main()