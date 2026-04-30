"""Google Drive 自动上传模块

将处理后的视频素材（hook.mp4 / gameplay.mp4 / analysis.json）自动上传至
指定 Google Drive 文件夹，按产品名称分目录组织。

认证方式：Service Account（服务账号）
前提：目标 Google Drive 文件夹需共享给服务账号邮箱

用法:
    # 通过 .env 配置
    GDRIVE_ENABLED=True
    GDRIVE_CREDENTIALS_PATH=credentials.json
    GDRIVE_ROOT_FOLDER_ID=1aBcDeFgHiJkLmNoPqRsTuVwXyZ

    # 或命令行参数
    python -m src.main --gdrive --gdrive-folder 1aBcDeFgHiJkLmNoPqRsTuVwXyZ
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("videoprecut.gdrive_uploader")


@dataclass
class UploadResult:
    """单个视频的上传结果"""

    video_stem: str
    product: str
    files: Dict[str, str] = field(default_factory=dict)  # {本地文件名: Drive file_id}
    errors: List[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0 and len(self.files) > 0


class GDriveUploader:
    """Google Drive 上传器

    文件夹结构:
        [root_folder]/
        ├── 产品A/
        │   ├── video_stem_1/
        │   │   ├── hook.mp4
        │   │   ├── gameplay.mp4
        │   │   └── analysis.json
        │   └── video_stem_2/
        └── 产品B/
    """

    # 上传重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0  # 秒

    # 上传文件类型（从每个视频目录上传的文件）
    UPLOAD_FILES = ["hook.mp4", "gameplay.mp4", "analysis.json"]

    def __init__(self, credentials_path: str, root_folder_id: str):
        """初始化上传器

        Args:
            credentials_path: 服务账号密钥文件路径
            root_folder_id: Google Drive 根文件夹 ID
        """
        self.root_folder_id = root_folder_id
        self._folder_cache: Dict[str, str] = {}  # {路径: folder_id}
        self._service = None

        self._init_service(credentials_path)

    def _init_service(self, credentials_path: str) -> None:
        """初始化 Google Drive API 服务"""
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError:
            raise ImportError(
                "Google Drive 依赖未安装，请运行: "
                "pip install google-api-python-client google-auth google-auth-httplib2"
            )

        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"服务账号密钥文件不存在: {credentials_path}")

        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/drive"],
        )

        self._service = build("drive", "v3", credentials=credentials)
        logger.info(f"Google Drive 服务初始化成功, 根文件夹: {self.root_folder_id}")

        # 验证根文件夹可访问
        try:
            root_folder = self._service.files().get(
                fileId=self.root_folder_id,
                fields="id, name",
                supportsAllDrives=True,
            ).execute()
            logger.info(f"根文件夹验证成功: {root_folder['name']} ({root_folder['id']})")
        except Exception as e:
            raise RuntimeError(
                f"无法访问根文件夹 {self.root_folder_id}，"
                f"请确认已将该文件夹共享给服务账号邮箱（Shared Drive 需将服务账号添加为成员）。错误: {e}"
            )

    def _get_batch_folder_name(self) -> str:
        """生成批次文件夹名称: Hooks_YYMMDD 或 Hooks_YYMMDD_N

        如果同一天已有同名文件夹，自动追加序号。

        Returns:
            批次文件夹名称
        """
        from datetime import datetime

        date_str = datetime.now().strftime("%y%m%d")
        base_name = f"Hooks_{date_str}"

        # 检查根文件夹下是否已有同名文件夹
        existing = self._find_folder(base_name, self.root_folder_id)
        if not existing:
            return base_name

        # 同名已存在，查找最大序号
        # 列出所有 Hooks_YYMMDD 开头的文件夹
        try:
            query = (
                f"name contains 'Hooks_{date_str}' and "
                f"'{self.root_folder_id}' in parents and "
                f"mimeType='application/vnd.google-apps.folder' and "
                f"trashed=false"
            )
            response = self._service.files().list(
                q=query,
                spaces="drive",
                fields="files(id, name)",
                pageSize=100,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()

            existing_names = [f["name"] for f in response.get("files", [])]
            max_seq = 1
            for name in existing_names:
                # 匹配 Hooks_YYMMDD_N 格式
                if name == base_name:
                    max_seq = max(max_seq, 1)
                elif name.startswith(f"{base_name}_"):
                    try:
                        seq = int(name.split("_")[-1])
                        max_seq = max(max_seq, seq)
                    except ValueError:
                        pass

            return f"{base_name}_{max_seq + 1}"

        except Exception:
            return f"{base_name}_2"

    def upload_batch(self, batch_dir: str, results: List[dict]) -> dict:
        """上传整个批次的处理结果

        Args:
            batch_dir: 批次输出目录
            results: 处理结果列表（来自 process_video）

        Returns:
            上传汇总 {"uploaded": N, "failed": N, "skipped": N, "details": [...]}
        """
        # 创建批次文件夹: Hooks_YYMMDD 或 Hooks_YYMMDD_N
        batch_folder_name = self._get_batch_folder_name()
        batch_folder_id = self._ensure_folder(batch_folder_name, self.root_folder_id)
        logger.info(f"批次文件夹: {batch_folder_name} ({batch_folder_id})")

        upload_results = []
        uploaded = 0
        failed = 0
        skipped = 0

        for result in results:
            # 跳过丢弃和失败的视频
            if result.get("discarded") or not result.get("success"):
                skipped += 1
                continue

            video_stem = result.get("video_stem", "")
            product = result.get("product", "未分类")
            video_dir = os.path.join(batch_dir, video_stem)

            if not os.path.isdir(video_dir):
                logger.warning(f"视频目录不存在，跳过: {video_dir}")
                skipped += 1
                continue

            logger.info(f"上传: {product}/{video_stem}")
            upload_result = self.upload_video(video_dir, product, batch_folder_id)
            upload_results.append(upload_result)

            if upload_result.success:
                uploaded += 1
                logger.info(f"  ✓ 上传成功: {len(upload_result.files)} 个文件")
            else:
                failed += 1
                for err in upload_result.errors:
                    logger.error(f"  ✗ {err}")

        summary = {
            "uploaded": uploaded,
            "failed": failed,
            "skipped": skipped,
            "total": len(results),
            "details": upload_results,
        }

        logger.info(
            f"Google Drive 上传完成: {uploaded} 成功, {failed} 失败, {skipped} 跳过"
        )
        return summary

    def upload_video(self, video_dir: str, product: str, batch_folder_id: str = None) -> UploadResult:
        """上传单个视频的所有输出文件

        Args:
            video_dir: 视频输出目录（包含 hook.mp4, gameplay.mp4, analysis.json）
            product: 产品名称（用于创建子文件夹）
            batch_folder_id: 批次文件夹 ID（默认使用根文件夹）

        Returns:
            UploadResult
        """
        video_stem = os.path.basename(video_dir)
        result = UploadResult(video_stem=video_stem, product=product)

        parent_id = batch_folder_id or self.root_folder_id

        try:
            # 确保产品文件夹存在（在批次文件夹下）
            product_folder_id = self._ensure_folder(product, parent_id)

            # 确保视频子文件夹存在
            video_folder_id = self._ensure_folder(video_stem, product_folder_id)

            # 上传每个文件
            for filename in self.UPLOAD_FILES:
                local_path = os.path.join(video_dir, filename)
                if not os.path.exists(local_path):
                    logger.debug(f"  文件不存在，跳过: {filename}")
                    continue

                file_id = self._upload_file(local_path, video_folder_id)
                if file_id:
                    result.files[filename] = file_id
                else:
                    result.errors.append(f"上传失败: {filename}")

        except Exception as e:
            result.errors.append(f"上传异常: {e}")
            logger.error(f"上传异常 {product}/{video_stem}: {e}", exc_info=True)

        return result

    def _ensure_folder(self, name: str, parent_id: str) -> str:
        """确保文件夹存在，不存在则创建

        Args:
            name: 文件夹名称
            parent_id: 父文件夹 ID

        Returns:
            文件夹 ID
        """
        cache_key = f"folder:{parent_id}:{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        folder_id = self._find_folder(name, parent_id)
        if not folder_id:
            folder_id = self._create_folder(name, parent_id)
            logger.info(f"创建文件夹: {name} ({folder_id})")

        self._folder_cache[cache_key] = folder_id
        return folder_id

    def _find_folder(self, name: str, parent_id: str) -> Optional[str]:
        """在指定父文件夹下查找同名文件夹

        Args:
            name: 文件夹名称
            parent_id: 父文件夹 ID

        Returns:
            文件夹 ID，不存在返回 None
        """
        try:
            query = (
                f"name='{name}' and "
                f"'{parent_id}' in parents and "
                f"mimeType='application/vnd.google-apps.folder' and "
                f"trashed=false"
            )
            response = self._service.files().list(
                q=query,
                spaces="drive",
                fields="files(id, name)",
                pageSize=1,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()

            files = response.get("files", [])
            return files[0]["id"] if files else None

        except Exception as e:
            logger.warning(f"查找文件夹失败 '{name}': {e}")
            return None

    def _create_folder(self, name: str, parent_id: str) -> str:
        """在指定父文件夹下创建子文件夹

        Args:
            name: 文件夹名称
            parent_id: 父文件夹 ID

        Returns:
            新建文件夹 ID
        """
        file_metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }

        folder = self._service.files().create(
            body=file_metadata,
            fields="id",
            supportsAllDrives=True,
        ).execute()

        return folder["id"]

    def _upload_file(self, local_path: str, parent_id: str) -> Optional[str]:
        """上传文件到 Google Drive（支持断点续传和覆盖）

        Args:
            local_path: 本地文件路径
            parent_id: 目标文件夹 ID

        Returns:
            文件 ID，失败返回 None
        """
        filename = os.path.basename(local_path)
        file_size = os.path.getsize(local_path)
        logger.info(f"  上传: {filename} ({file_size / 1024 / 1024:.1f}MB)")

        # 检查同名文件是否已存在
        existing_id = self._find_file(filename, parent_id)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                from googleapiclient.http import MediaFileUpload

                # 大文件使用 resumable 上传
                chunk_size = 10 * 1024 * 1024  # 10MB chunks
                media = MediaFileUpload(
                    local_path,
                    resumable=True,
                    chunksize=chunk_size,
                )

                if existing_id:
                    # 覆盖已有文件
                    file_id = self._update_file(existing_id, media, filename)
                else:
                    # 创建新文件
                    file_id = self._create_file(media, parent_id, filename)

                return file_id

            except Exception as e:
                logger.warning(
                    f"  上传重试 {attempt}/{self.MAX_RETRIES}: {filename} - {e}"
                )
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY * attempt)

        logger.error(f"  上传失败（已重试 {self.MAX_RETRIES} 次）: {filename}")
        return None

    def _find_file(self, name: str, parent_id: str) -> Optional[str]:
        """在指定文件夹下查找同名文件

        Args:
            name: 文件名
            parent_id: 父文件夹 ID

        Returns:
            文件 ID，不存在返回 None
        """
        try:
            query = (
                f"name='{name}' and "
                f"'{parent_id}' in parents and "
                f"trashed=false"
            )
            response = self._service.files().list(
                q=query,
                spaces="drive",
                fields="files(id, name)",
                pageSize=1,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()

            files = response.get("files", [])
            return files[0]["id"] if files else None

        except Exception as e:
            logger.warning(f"查找文件失败 '{name}': {e}")
            return None

    def _create_file(self, media, parent_id: str, filename: str) -> str:
        """创建新文件

        Args:
            media: MediaFileUpload 对象
            parent_id: 目标文件夹 ID
            filename: 文件名

        Returns:
            文件 ID
        """
        file_metadata = {
            "name": filename,
            "parents": [parent_id],
        }

        request = self._service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        )

        return self._execute_resumable(request, filename)

    def _update_file(self, file_id: str, media, filename: str) -> str:
        """更新已有文件（覆盖内容）

        Args:
            file_id: 已有文件 ID
            media: MediaFileUpload 对象
            filename: 文件名

        Returns:
            文件 ID
        """
        request = self._service.files().update(
            fileId=file_id,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        )

        return self._execute_resumable(request, filename)

    def _execute_resumable(self, request, filename: str) -> str:
        """执行断点续传上传

        Args:
            request: 上传请求对象
            filename: 文件名（用于日志）

        Returns:
            文件 ID
        """
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                if progress % 25 == 0:  # 每 25% 打印一次
                    logger.debug(f"  上传进度 {filename}: {progress}%")

        return response["id"]
