"""主入口模块 - V2 工作流编排

V2 工作流：
1. 扫描 input/ 目录（递归，支持产品子目录）
2. 格式检查与转换
3. 多模态AI分析视频结构（检测Hook/Gameplay/商标三段式）
4. 无Hook或无商标的素材直接丢弃
5. 剪切Hook/Gameplay视频 → output/batch_XXX/{video_stem}/
6. AI分析Hook元素（描述+情感+过渡方式）
7. 保存JSON元数据 → output/batch_XXX/{video_stem}/analysis.json
"""

import argparse
import logging
import os
import sys
import time
from typing import List, Optional

# 加载 .env 环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv 未安装时跳过

from .config import Config
from .ingestion import VideoInfo, scan_input_dir
from .converter import ensure_mp4, cleanup_converted
from .analyzer import MultimodalAnalyzer
from .structurer import analyze_video_structure
from .editor import cut_segment
from .parallel import run_parallel, summarize_results
from .utils import (
    setup_logging,
    check_ffmpeg,
    sample_keyframes,
    extract_segment_frames,
    save_metadata,
)

logger = logging.getLogger("videoprecut")


def process_video(
    video: VideoInfo,
    config: Config,
    analyzer: Optional[MultimodalAnalyzer] = None,
) -> dict:
    """处理单个视频的完整V2流程

    Args:
        video: 视频信息对象
        config: 全局配置
        analyzer: 多模态分析器（可选，未传入时自动创建）

    Returns:
        处理结果字典
    """
    start_time = time.time()
    result = {
        "video": video.filename,
        "video_stem": video.stem,
        "product": video.product,
        "success": False,
        "discarded": False,
        "discard_reason": "",
        "hook_path": "",
        "gameplay_path": "",
        "metadata_path": "",
        "hook_description": "",
        "hook_emotion": "",
        "hook_transition": "",
        "error": "",
        "duration_sec": 0.0,
    }

    try:
        # ── 步骤1: 格式检查与转换 ──
        logger.info(f"[1/5] 格式检查: {video.filename}")
        mp4_path = ensure_mp4(video, config)

        # ── 步骤2: 多模态AI分析视频结构 ──
        if analyzer is None:
            analyzer = MultimodalAnalyzer(config)
        use_video_mode = analyzer.provider.supports_video

        if use_video_mode:
            # 视频直传模式（Qwen原生视频输入，帧级精度）
            logger.info(f"[2/5] AI分析视频结构(视频直传): {video.filename}")
            ai_structure = analyzer.analyze_video_structure_from_file(
                mp4_path, video.duration
            )
        else:
            # 帧采样模式（回退方案，精度受限于采样密度）
            logger.info(f"[2/5] AI分析视频结构(帧采样): {video.filename}")
            frames = sample_keyframes(
                mp4_path,
                num_frames=config.frame_sample_count,
            )
            if not frames:
                raise RuntimeError("无法从视频中采样帧")
            logger.info(f"  采样到 {len(frames)} 个关键帧")
            ai_structure = analyzer.analyze_video_structure(frames, video.duration)

        # ── 步骤3: 整合AI分析结果 ──
        structure = analyze_video_structure(
            ai_structure=ai_structure,
            video_duration=video.duration,
            config=config,
        )

        # ── 判断是否丢弃 ──
        if structure.should_discard:
            result["discarded"] = True
            result["success"] = True
            if not structure.has_hook:
                result["discard_reason"] = "no_hook"
                logger.info(f"⚠ 无Hook，丢弃: {video.filename}")
            elif not structure.has_trademark:
                result["discard_reason"] = "no_trademark"
                logger.info(f"⚠ 无商标/结束画面，丢弃: {video.filename}")
            else:
                result["discard_reason"] = "unknown"
                logger.info(f"⚠ 丢弃: {video.filename}")
            return result

        # ── 步骤4: 剪切Hook和Gameplay视频 ──
        logger.info(f"[4/5] 剪切视频片段: {video.filename}")

        # 创建视频专属输出目录
        video_output_dir = config.get_video_output_dir(video.stem)
        os.makedirs(video_output_dir, exist_ok=True)

        # 剪切Hook视频
        if structure.hook_segment and structure.hook_duration > 0:
            hook_output = os.path.join(video_output_dir, "hook.mp4")
            hook_path = cut_segment(
                mp4_path, structure.hook_segment, hook_output, config
            )
            result["hook_path"] = hook_path
            if hook_path:
                logger.info(
                    f"  ✓ Hook视频: hook.mp4 "
                    f"({structure.hook_duration:.1f}s)"
                )
            else:
                logger.warning("  Hook视频剪切失败")

        # 剪切Gameplay视频
        if structure.gameplay_segment and structure.gameplay_duration > 0:
            gameplay_output = os.path.join(video_output_dir, "gameplay.mp4")
            gameplay_path = cut_segment(
                mp4_path, structure.gameplay_segment, gameplay_output, config
            )
            result["gameplay_path"] = gameplay_path
            if gameplay_path:
                logger.info(
                    f"  ✓ Gameplay视频: gameplay.mp4 "
                    f"({structure.gameplay_duration:.1f}s)"
                )
            else:
                logger.warning("  Gameplay视频剪切失败")

        # ── 步骤5: 分析Hook元素 + 保存元数据 ──
        logger.info(f"[5/5] Hook元素分析: {video.filename}")

        hook_description = ""
        hook_emotion = ""
        hook_transition = ""
        if config.hook_description_enabled and structure.hook_segment:
            try:
                if use_video_mode:
                    # 视频直传模式分析Hook描述
                    desc_result = analyzer.describe_hook_from_file(
                        mp4_path,
                        hook_start=structure.hook_segment.start,
                        hook_end=structure.hook_segment.end,
                    )
                    hook_description = desc_result.description
                    hook_emotion = desc_result.emotion
                    hook_transition = desc_result.transition
                    result["hook_description"] = hook_description
                    result["hook_emotion"] = hook_emotion
                    result["hook_transition"] = hook_transition
                else:
                    # 帧采样模式分析Hook描述
                    hook_frames = extract_segment_frames(
                        mp4_path,
                        start=structure.hook_segment.start,
                        end=structure.hook_segment.end,
                        num_frames=3,
                    )
                    if hook_frames:
                        desc_result = analyzer.describe_hook(hook_frames)
                        hook_description = desc_result.description
                        hook_emotion = desc_result.emotion
                        hook_transition = desc_result.transition
                        result["hook_description"] = hook_description
                        result["hook_emotion"] = hook_emotion
                        result["hook_transition"] = hook_transition
            except Exception as e:
                logger.warning(f"Hook描述生成失败: {e}")
                hook_description = f"描述生成失败: {e}"

        # 保存JSON元数据
        metadata = {
            "filename": video.filename,
            "product": video.product,
            "has_hook": structure.has_hook,
            "hook_description": hook_description,
            "hook_emotion": hook_emotion,
            "hook_transition": hook_transition,
            "segments": {
                "hook": {
                    "start": structure.hook_segment.start if structure.hook_segment else 0,
                    "end": structure.hook_segment.end if structure.hook_segment else 0,
                    "duration": structure.hook_duration,
                },
                "gameplay": {
                    "start": structure.gameplay_segment.start if structure.gameplay_segment else 0,
                    "end": structure.gameplay_segment.end if structure.gameplay_segment else 0,
                    "duration": structure.gameplay_duration,
                },
                "trademark": {
                    "start": structure.trademark_segment.start if structure.trademark_segment else 0,
                    "end": structure.trademark_segment.end if structure.trademark_segment else 0,
                    "duration": structure.trademark_duration,
                } if structure.trademark_segment else None,
            },
            "video_info": {
                "width": video.width,
                "height": video.height,
                "fps": video.fps,
                "total_duration": video.duration,
            },
            "ai_confidence": structure.ai_confidence,
            "processing_time": 0.0,  # 后面更新
        }

        metadata_path = os.path.join(video_output_dir, "analysis.json")
        metadata["processing_time"] = time.time() - start_time
        save_metadata(metadata_path, metadata)
        result["metadata_path"] = metadata_path

        result["success"] = True
        logger.info(f"✓ 处理完成: {video.filename}")

    except Exception as e:
        result["error"] = str(e)
        result["success"] = False
        logger.error(f"✗ 处理失败: {video.filename} - {e}", exc_info=True)

    result["duration_sec"] = time.time() - start_time
    return result


def process_serial(videos: List[VideoInfo], config: Config) -> List[dict]:
    """串行处理所有视频

    Args:
        videos: 视频信息列表
        config: 全局配置

    Returns:
        处理结果列表
    """
    results = []
    total = len(videos)

    # 在循环外创建共享的 analyzer（避免每个视频重复初始化）
    analyzer = MultimodalAnalyzer(config)

    for i, video in enumerate(videos, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"处理视频 [{i}/{total}]: {video.filename}")
        logger.info(f"{'='*60}")

        result = process_video(video, config, analyzer=analyzer)
        results.append(result)

        status = "✓" if result["success"] else "✗"
        discarded = " (已丢弃)" if result.get("discarded") else ""
        logger.info(
            f"{status} [{i}/{total}] {video.filename}{discarded} - "
            f"耗时 {result['duration_sec']:.1f}s"
        )

    return results


def main():
    """命令行主入口"""
    parser = argparse.ArgumentParser(
        description="竞品广告素材分析工作流 - Videoprecut V2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 使用阿里DashScope（Qwen系列模型，推荐）
  python -m src.main --ai-provider dashscope --ai-api-key sk-xxx

  # 使用OpenAI GPT-4o
  python -m src.main --ai-provider openai --ai-api-key sk-xxx

  # 使用Anthropic Claude
  python -m src.main --ai-provider anthropic --ai-model claude-sonnet-4-20250514 --ai-api-key sk-xxx

  # 使用本地模型
  python -m src.main --ai-provider local --ai-base-url http://localhost:8000/v1

  # 并行处理
  python -m src.main --parallel --workers 4 --ai-api-key sk-xxx

  # 不丢弃无Hook素材
  python -m src.main --no-discard-no-hook --ai-api-key sk-xxx
        """,
    )

    # 路径参数
    parser.add_argument("--input", type=str, default="input", help="输入视频目录")
    parser.add_argument("--output", type=str, default="output", help="输出根目录")

    # AI参数（从环境变量读取默认值，.env文件已加载）
    parser.add_argument("--ai-provider", type=str,
                        default=os.environ.get("AI_PROVIDER", "dashscope"),
                        choices=["openai", "anthropic", "dashscope", "local"],
                        help="AI提供商")
    parser.add_argument("--ai-api-key", type=str,
                        default=(os.environ.get("DASHSCOPE_API_KEY", "")
                                 or os.environ.get("OPENAI_API_KEY", "")
                                 or os.environ.get("ANTHROPIC_API_KEY", "")),
                        help="AI API密钥（也可通过环境变量/.env设置）")
    parser.add_argument("--ai-model", type=str,
                        default=os.environ.get("AI_MODEL", "qwen-plus-latest"),
                        help="AI模型名称")
    parser.add_argument("--ai-base-url", type=str,
                        default=os.environ.get("AI_BASE_URL", ""),
                        help="自定义AI API地址")
    parser.add_argument("--ai-temperature", type=float, default=0.3,
                        help="AI生成温度")

    # 采样参数
    parser.add_argument("--sample-count", type=int, default=8,
                        help="视频结构分析采样帧数")

    # 剪辑参数
    parser.add_argument("--crf", type=int, default=18, help="视频质量CRF值")
    parser.add_argument("--preset", type=str, default="medium", help="编码预设")
    parser.add_argument("--buffer", type=float, default=0.3,
                        help="商标片段前后缓冲时长(秒)")

    # Hook参数
    parser.add_argument("--hook-max-duration", type=float, default=10.0,
                        help="Hook最大时长(秒)")
    parser.add_argument("--hook-safety-buffer", type=float, default=0.5,
                        help="Hook安全缓冲(秒)，从AI判断的hook_end提前此值，确保Hook不含Gameplay")
    parser.add_argument("--hook-min-duration", type=float, default=0.5,
                        help="Hook最小时长(秒)，低于此值视为无Hook")
    parser.add_argument("--no-discard-no-hook", action="store_true",
                        help="不丢弃无Hook的素材")
    parser.add_argument("--no-discard-no-trademark", action="store_true",
                        help="不丢弃无商标/结束画面的素材")
    parser.add_argument("--trademark-min-duration", type=float, default=2.0,
                        help="商标最小时长(秒)，低于此值视为AI误判不算商标")
    parser.add_argument("--no-hook-description", action="store_true",
                        help="禁用Hook元素描述")
    parser.add_argument("--ffmpeg-timeout", type=int, default=300,
                        help="FFmpeg操作超时时间(秒)")


    # 并行参数
    parser.add_argument("--parallel", action="store_true", default=True,
                        help="启用并行处理")
    parser.add_argument("--no-parallel", action="store_true",
                        help="禁用并行处理")
    parser.add_argument("--workers", type=int, default=4,
                        help="最大工作进程数")
    parser.add_argument("--gpus", type=str, default="0",
                        help="可用GPU列表(逗号分隔)")

    # Google Drive 参数
    parser.add_argument("--gdrive", action="store_true",
                        help="启用 Google Drive 上传")
    parser.add_argument("--gdrive-folder", type=str, default="",
                        help="Google Drive 根文件夹 ID")
    parser.add_argument("--gdrive-creds", type=str, default="credentials.json",
                        help="Google Drive 服务账号密钥文件路径")
    parser.add_argument("--limit", type=int, default=0,
                        help="限制处理视频数量(0=全部)")

    args = parser.parse_args()

    # ── 初始化配置 ──
    # 生成批次目录名（时间戳）
    from datetime import datetime
    batch_name = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    config = Config(
        input_dir=args.input,
        output_dir=args.output,
        batch_dir=os.path.join(args.output, batch_name),
        buffer_before_sec=args.buffer,
        buffer_after_sec=args.buffer,
        crf=args.crf,
        preset=args.preset,
        use_parallel=not args.no_parallel and args.parallel,
        max_workers=args.workers,
        gpu_ids=[int(g.strip()) for g in args.gpus.split(",")],
        ai_provider=args.ai_provider,
        ai_api_key=args.ai_api_key,
        ai_model=args.ai_model,
        ai_base_url=args.ai_base_url,
        ai_temperature=args.ai_temperature,
        frame_sample_count=args.sample_count,
        hook_max_duration=args.hook_max_duration,
        hook_safety_buffer=args.hook_safety_buffer,
        hook_min_duration=args.hook_min_duration,
        hook_description_enabled=not args.no_hook_description,
        discard_no_hook=not args.no_discard_no_hook,
        discard_no_trademark=not args.no_discard_no_trademark,
        trademark_min_duration=args.trademark_min_duration,
        ffmpeg_timeout=args.ffmpeg_timeout,
        gdrive_enabled=args.gdrive or bool(os.environ.get("GDRIVE_ENABLED", "").lower() in ("true", "1", "yes")),
        gdrive_credentials_path=args.gdrive_creds or os.environ.get("GDRIVE_CREDENTIALS_PATH", "credentials.json"),
        gdrive_root_folder_id=args.gdrive_folder or os.environ.get("GDRIVE_ROOT_FOLDER_ID", ""),
    )

    # 确保目录存在
    config.ensure_dirs()

    # ── 初始化日志 ──
    setup_logging()

    logger.info("=" * 60)
    logger.info("竞品广告素材分析工作流 - Videoprecut V2")
    logger.info("=" * 60)

    # ── 环境检查 ──
    if not check_ffmpeg():
        logger.error(
            "FFmpeg 不可用，请安装 FFmpeg (brew install ffmpeg) "
            "或安装 imageio-ffmpeg 包 (pip install imageio-ffmpeg)"
        )
        sys.exit(1)
    logger.info("✓ FFmpeg 环境检查通过")

    # ── AI配置检查 ──
    api_key_env_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "dashscope": "DASHSCOPE_API_KEY",
    }
    if config.ai_provider in api_key_env_map and not config.ai_api_key:
        env_key = api_key_env_map[config.ai_provider]
        if not os.environ.get(env_key):
            logger.warning(
                f"AI API密钥未设置，请通过 --ai-api-key 参数或 "
                f"{env_key} 环境变量提供"
            )

    # ── 扫描输入目录 ──
    videos = scan_input_dir(config)
    if not videos:
        logger.warning("输入目录中没有找到视频文件")
        sys.exit(0)

    # 限制处理数量
    if args.limit > 0 and len(videos) > args.limit:
        logger.info(f"限制处理数量: {len(videos)} → {args.limit}")
        videos = videos[:args.limit]

    # ── 处理视频 ──
    total_start = time.time()

    if config.use_parallel and len(videos) > 1:
        # 并行模式
        logger.info(f"使用并行模式处理 {len(videos)} 个视频")
        video_paths = [v.filepath for v in videos]
        parallel_results = run_parallel(video_paths, config)
        summary = summarize_results(parallel_results)
    else:
        # 串行模式
        logger.info(f"使用串行模式处理 {len(videos)} 个视频")
        results = process_serial(videos, config)

        # 汇总结果
        success = sum(1 for r in results if r["success"])
        failed = len(results) - success
        discarded = sum(1 for r in results if r.get("discarded"))
        discarded_no_hook = sum(1 for r in results if r.get("discard_reason") == "no_hook")
        discarded_no_trademark = sum(1 for r in results if r.get("discard_reason") == "no_trademark")
        total_time = time.time() - total_start

        logger.info("=" * 60)
        logger.info("处理结果汇总")
        logger.info("=" * 60)
        logger.info(f"  总视频数: {len(results)}")
        logger.info(f"  成功: {success}")
        logger.info(f"  失败: {failed}")
        logger.info(f"  丢弃(无Hook): {discarded_no_hook}")
        logger.info(f"  丢弃(无商标): {discarded_no_trademark}")
        logger.info(f"  总处理时间: {total_time:.1f}s")

        if failed > 0:
            logger.info("")
            logger.info("失败视频:")
            for r in results:
                if not r["success"]:
                    logger.info(f"  {r['video']}: {r['error']}")

        logger.info("=" * 60)

    # 清理格式转换产生的临时文件
    cleanup_converted(config)

    # ── Google Drive 上传 ──
    if config.gdrive_enabled and config.gdrive_root_folder_id:
        logger.info("")
        logger.info("=" * 60)
        logger.info("Google Drive 上传")
        logger.info("=" * 60)
        try:
            from .gdrive_uploader import GDriveUploader

            uploader = GDriveUploader(
                credentials_path=config.gdrive_credentials_path,
                root_folder_id=config.gdrive_root_folder_id,
            )
            upload_summary = uploader.upload_batch(config.batch_dir, results)
            logger.info(
                f"Google Drive 上传完成: "
                f"{upload_summary['uploaded']} 成功, "
                f"{upload_summary['failed']} 失败, "
                f"{upload_summary['skipped']} 跳过"
            )
        except Exception as e:
            logger.error(f"Google Drive 上传失败: {e}", exc_info=True)

    logger.info("工作流执行完毕")


if __name__ == "__main__":
    main()