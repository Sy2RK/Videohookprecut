"""飞书多维表格视频导入模块

从飞书多维表格（Bitable）中自动下载视频附件到本地 input/ 目录，
按产品（应用名称）分文件夹，支持 file_token 去重和增量导入。

用法:
    python -m src.bitable_import                    # 从 .env 读取配置
    python -m src.bitable_import --dry-run          # 预览模式（不下载）
    python -m src.bitable_import --product "产品A"   # 仅导入指定产品
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse, quote

import requests

# 加载 .env 环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("videoprecut.bitable_import")


# ============================================================
# 配置
# ============================================================

@dataclass
class BitableConfig:
    """飞书多维表格导入配置"""

    app_id: str = ""              # 飞书应用 ID
    app_secret: str = ""          # 飞书应用密钥
    base_url: str = ""            # 多维表格 URL
    user_access_token: str = ""   # 用户访问令牌
    download_dir: str = "input"   # 下载根目录
    product_field: str = "应用名称"  # 产品名称字段
    rate_limit_qps: float = 4.0   # API 调用频率限制（留余量）
    timeout: int = 120            # 单文件下载超时（秒）
    max_retries: int = 3          # 下载失败重试次数

    @classmethod
    def from_env(cls) -> "BitableConfig":
        """从环境变量创建配置"""
        return cls(
            app_id=os.environ.get("FEISHU_APP_ID", ""),
            app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            base_url=os.environ.get("FEISHU_BASE_URL", ""),
            user_access_token=os.environ.get("FEISHU_USER_ACCESS_TOKEN", ""),
            download_dir=os.environ.get("FEISHU_DOWNLOAD_DIR", "input"),
            product_field=os.environ.get("FEISHU_PRODUCT_FIELD", "应用名称"),
        )

    def validate(self) -> List[str]:
        """验证必填字段，返回缺失项列表
        
        user_access_token 仅下载时需要，预览/去重可跳过。
        """
        missing = []
        if not self.app_id:
            missing.append("FEISHU_APP_ID")
        if not self.app_secret:
            missing.append("FEISHU_APP_SECRET")
        if not self.base_url:
            missing.append("FEISHU_BASE_URL")
        return missing
    
    @property
    def has_user_token(self) -> bool:
        """是否配置了用户访问令牌"""
        return bool(self.user_access_token)


# ============================================================
# 速率控制
# ============================================================

class RateLimiter:
    """简单的令牌桶速率限制器"""

    def __init__(self, qps: float = 4.0):
        self.interval = 1.0 / qps
        self._last_call = 0.0

    def wait(self):
        """等待直到可以发起下一次请求"""
        elapsed = time.time() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.time()


# ============================================================
# 飞书 API 客户端
# ============================================================

class FeishuClient:
    """飞书 Open API 客户端

    封装多维表格数据读取和附件下载。
    """

    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, config: BitableConfig):
        self.config = config
        self._tenant_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self.rate_limiter = RateLimiter(config.rate_limit_qps)

    # ── 认证 ──

    def get_tenant_access_token(self) -> str:
        """获取 tenant_access_token（自动缓存，过期刷新）

        token 有效期约 2 小时，提前 5 分钟刷新。
        """
        if self._tenant_token and time.time() < self._token_expires_at:
            return self._tenant_token

        url = f"{self.BASE_URL}/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.config.app_id,
            "app_secret": self.config.app_secret,
        }

        logger.info("获取 tenant_access_token...")
        resp = requests.post(url, json=payload, timeout=30)
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"获取 tenant_access_token 失败: {data.get('msg', '未知错误')} "
                f"(code={data.get('code')})"
            )

        self._tenant_token = data["tenant_access_token"]
        # 提前 5 分钟过期
        self._token_expires_at = time.time() + data.get("expire", 7200) - 300
        logger.info("tenant_access_token 获取成功")
        return self._tenant_token

    # ── URL 解析 ──

    def parse_base_url(self) -> dict:
        """解析多维表格 URL → {app_token, table_id, view_id}

        支持两种 URL 形态:
        - feishu.cn/base/xxx?table=tblxxx
        - feishu.cn/wiki/xxx (知识库中的多维表格)
        """
        token = self.get_tenant_access_token()
        parsed = urlparse(self.config.base_url)
        pathname = parsed.path
        params = parse_qs(parsed.query)

        app_token = pathname.rstrip("/").split("/")[-1]

        # 知识库形态: 需要调用 wiki API 获取真实的 app_token
        if "/wiki/" in pathname:
            logger.info(f"检测到知识库 URL，解析节点: {app_token}")
            node_info = self._get_wiki_node_info(token, app_token)
            if node_info.get("obj_type") != "bitable":
                raise RuntimeError(
                    f"知识库节点不是多维表格: obj_type={node_info.get('obj_type')}"
                )
            app_token = node_info["obj_token"]
            logger.info(f"多维表格 app_token: {app_token}")

        table_id = params.get("table", [None])[0]
        view_id = params.get("view", [None])[0]

        return {"app_token": app_token, "table_id": table_id, "view_id": view_id}

    def _get_wiki_node_info(self, tenant_token: str, node_token: str) -> dict:
        """获取知识空间节点信息"""
        url = (
            f"{self.BASE_URL}/wiki/v2/spaces/get_node"
            f"?token={quote(node_token)}"
        )
        headers = self._auth_headers(tenant_token)

        self.rate_limiter.wait()
        resp = requests.get(url, headers=headers, timeout=30)
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"获取知识库节点失败: {data.get('msg')} (code={data.get('code')})"
            )

        return data["data"]["node"]

    # ── 数据读取 ──

    def list_tables(self, app_token: str) -> list:
        """列出所有数据表"""
        token = self.get_tenant_access_token()
        url = f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables"
        headers = self._auth_headers(token)

        self.rate_limiter.wait()
        resp = requests.get(url, headers=headers, timeout=30)
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"列出数据表失败: {data.get('msg')} (code={data.get('code')})"
            )

        tables = data.get("data", {}).get("items", [])
        logger.info(f"获取数据表列表成功，数量: {len(tables)}")
        return tables

    def list_fields(self, app_token: str, table_id: str) -> list:
        """列出所有字段（分页），返回附件字段列表 (type=17)"""
        token = self.get_tenant_access_token()
        all_fields = []
        page_token = ""
        has_more = True

        while has_more:
            url = (
                f"{self.BASE_URL}/bitable/v1/apps/{app_token}"
                f"/tables/{table_id}/fields?page_size=100"
            )
            if page_token:
                url += f"&page_token={quote(page_token)}"

            headers = self._auth_headers(token)

            self.rate_limiter.wait()
            resp = requests.get(url, headers=headers, timeout=30)
            data = resp.json()

            if data.get("code") != 0:
                raise RuntimeError(
                    f"列出字段失败: {data.get('msg')} (code={data.get('code')})"
                )

            items = data.get("data", {}).get("items", [])
            all_fields.extend(items)
            has_more = data.get("data", {}).get("has_more", False)
            page_token = data.get("data", {}).get("page_token", "")

        # 筛选附件字段 (type=17)
        attachment_fields = [f for f in all_fields if f.get("type") == 17]
        logger.info(
            f"获取字段完成: 总字段={len(all_fields)}, 附件字段={len(attachment_fields)}"
        )
        for f in attachment_fields:
            logger.info(
                f"  附件字段: {f.get('field_name')} (field_id={f.get('field_id')})"
            )

        return attachment_fields

    def get_records(self, app_token: str, table_id: str) -> list:
        """分页获取所有记录（page_size=500）
        
        使用 tenant_access_token（已验证可用）。
        """
        all_records = []
        page_token = ""
        has_more = True
        
        auth_token = self.get_tenant_access_token()

        while has_more:
            url = (
                f"{self.BASE_URL}/bitable/v1/apps/{app_token}"
                f"/tables/{table_id}/records/search?page_size=500"
            )
            if page_token:
                url += f"&page_token={quote(page_token)}"

            headers = self._auth_headers(auth_token)

            self.rate_limiter.wait()
            resp = requests.post(url, json={}, headers=headers, timeout=30)
            data = resp.json()

            if data.get("code") != 0:
                raise RuntimeError(
                    f"获取记录失败: {data.get('msg')} (code={data.get('code')})"
                )

            items = data.get("data", {}).get("items", [])
            all_records.extend(items)
            has_more = data.get("data", {}).get("has_more", False)
            page_token = data.get("data", {}).get("page_token", "")

            logger.debug(
                f"获取记录: 本页={len(items)}, 累计={len(all_records)}, "
                f"has_more={has_more}"
            )

        logger.info(f"所有记录获取完成，总数: {len(all_records)}")
        return all_records

    # ── 下载 ──

    def download_attachment(
        self, file_token: str, extra: Optional[str],
        save_dir: str, filename: str,
    ) -> bool:
        """流式下载单个附件

        Args:
            file_token: 飞书文件 token
            extra: 高级权限 extra 参数
            save_dir: 保存目录
            filename: 保存文件名

        Returns:
            下载成功返回 True，失败返回 False
        """
        os.makedirs(save_dir, exist_ok=True)
        filepath = os.path.join(save_dir, filename)

        # 构建 URL
        url = (
            f"{self.BASE_URL}/drive/v1/medias/{file_token}/download"
        )
        if extra:
            url += f"?extra={quote(extra)}"

        # 使用 tenant_access_token 下载（已验证可用）
        token = self.get_tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        for attempt in range(self.config.max_retries):
            try:
                self.rate_limiter.wait()
                resp = requests.get(
                    url, headers=headers, stream=True,
                    timeout=self.config.timeout,
                )

                if resp.status_code != 200:
                    logger.warning(
                        f"下载返回非 200: {resp.status_code} - {filename}"
                    )
                    if attempt < self.config.max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return False

                # 流式写入
                with open(filepath, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                # 验证文件
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    size_mb = os.path.getsize(filepath) / (1024 * 1024)
                    logger.info(f"  下载成功: {filename} ({size_mb:.1f}MB)")
                    return True
                else:
                    logger.warning(f"  下载文件为空: {filename}")
                    return False

            except requests.Timeout:
                logger.warning(
                    f"  下载超时 (尝试 {attempt + 1}/{self.config.max_retries}): "
                    f"{filename}"
                )
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)
            except Exception as e:
                logger.warning(
                    f"  下载异常 (尝试 {attempt + 1}/{self.config.max_retries}): "
                    f"{filename} - {e}"
                )
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)

        logger.error(f"  下载失败（已重试{self.config.max_retries}次）: {filename}")
        return False

    # ── 工具方法 ──

    @staticmethod
    def _auth_headers(token: str) -> dict:
        """构建认证请求头"""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }


# ============================================================
# 导入管理器
# ============================================================

class ImportManager:
    """导入管理器：去重 + 增量下载"""

    def __init__(self, config: BitableConfig, client: FeishuClient):
        self.config = config
        self.client = client
        self.stats: Dict[str, dict] = {}  # 产品级统计

    # ── Manifest 管理 ──

    def _safe_product_name(self, name: str) -> str:
        """产品名称安全处理（替换文件系统非法字符）"""
        return re.sub(r'[/\\:*?"<>|]', '_', name).strip()

    def _manifest_path(self, product: str) -> str:
        """产品 manifest.json 路径"""
        safe_name = self._safe_product_name(product)
        return os.path.join(self.config.download_dir, safe_name, "manifest.json")

    def load_manifest(self, product: str) -> dict:
        """加载已导入清单 {file_token: filename}"""
        path = self._manifest_path(product)
        if not os.path.exists(path):
            logger.info(f"  [{product}] 首次导入，无 manifest")
            return {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            logger.info(
                f"  [{product}] 加载 manifest: {len(manifest)} 个已导入文件"
            )
            return manifest
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"  [{product}] manifest 损坏，将重建: {e}")
            return {}

    def save_manifest(self, product: str, manifest: dict):
        """保存更新后的清单"""
        path = self._manifest_path(product)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        logger.info(f"  [{product}] manifest 已更新: {len(manifest)} 个文件")

    # ── 去重 ──

    def find_new_videos(
        self, records: list, attachment_fields: list
    ) -> Dict[str, list]:
        """按产品分组，比对 manifest，返回新增视频

        Args:
            records: 所有记录
            attachment_fields: 附件字段列表

        Returns:
            {
              "产品A": [
                {"file_token": "xxx", "filename": "a.mp4", "extra": "..."},
                ...
              ],
              ...
            }
        """
        product_field = self.config.product_field
        # 收集附件字段名集合（用于快速查找）
        attachment_field_names = {f["field_name"] for f in attachment_fields}

        # 按产品分组收集所有视频附件
        product_videos: Dict[str, Dict[str, dict]] = {}
        skipped_no_product = 0
        skipped_no_video = 0

        for record in records:
            fields = record.get("fields", {})

            # 获取产品名
            product_name = fields.get(product_field, "")
            if not product_name:
                skipped_no_product += 1
                continue

            # 确保是字符串
            if isinstance(product_name, list):
                product_name = product_name[0] if product_name else ""
            product_name = str(product_name).strip()
            if not product_name:
                skipped_no_product += 1
                continue

            if product_name not in product_videos:
                product_videos[product_name] = {}

            # 遍历附件字段
            has_video = False
            for field_name in attachment_field_names:
                attachments = fields.get(field_name, [])
                if not isinstance(attachments, list):
                    continue

                for att in attachments:
                    # 判断是否为视频：优先用 mime_type，回退到文件扩展名
                    mime = att.get("mime_type", "")
                    filename = att.get("name", "")
                    is_video = mime.startswith("video/") if mime else \
                        os.path.splitext(filename)[1].lower() in {
                            ".mp4", ".webm", ".avi", ".mov", ".mkv", ".flv"
                        }
                    if not is_video:
                        continue

                    file_token = att.get("file_token", "")
                    if not file_token:
                        continue

                    # 同一 file_token 只保留一次
                    if file_token not in product_videos[product_name]:
                        product_videos[product_name][file_token] = {
                            "file_token": file_token,
                            "filename": att.get("name", f"{file_token}.mp4"),
                            "extra": att.get("extra", ""),
                        }
                        has_video = True

            if not has_video:
                skipped_no_video += 1

        if skipped_no_product > 0:
            logger.warning(
                f"{skipped_no_product} 条记录缺少「{product_field}」字段，已跳过"
            )
        if skipped_no_video > 0:
            logger.info(f"{skipped_no_video} 条记录无视频附件，已跳过")

        # 对每个产品去重
        result: Dict[str, list] = {}
        total_new = 0
        total_known = 0

        for product, videos in product_videos.items():
            manifest = self.load_manifest(product)
            new_videos = []

            for file_token, video_info in videos.items():
                if file_token in manifest:
                    total_known += 1
                else:
                    new_videos.append(video_info)
                    total_new += 1

            if new_videos:
                result[product] = new_videos
                logger.info(
                    f"  [{product}]: 总计={len(videos)}, "
                    f"已导入={len(videos) - len(new_videos)}, "
                    f"新增={len(new_videos)}"
                )
            else:
                logger.info(
                    f"  [{product}]: 总计={len(videos)}, 无新增"
                )

        logger.info(
            f"去重完成: 总视频={total_new + total_known}, "
            f"新增={total_new}, 已存在={total_known}"
        )

        return result

    # ── 主流程 ──

    def run(self, dry_run: bool = False, target_product: Optional[str] = None) -> dict:
        """执行完整导入流程

        Args:
            dry_run: 仅预览，不实际下载
            target_product: 仅导入指定产品（None=全部）

        Returns:
            汇总报告 dict
        """
        start_time = time.time()

        # 1. 解析多维表格
        parsed = self.client.parse_base_url()
        app_token = parsed["app_token"]
        table_id = parsed["table_id"]

        # 2. 获取数据表
        if not table_id:
            tables = self.client.list_tables(app_token)
            if not tables:
                raise RuntimeError("多维表格中没有数据表")
            table_id = tables[0]["table_id"]
            logger.info(f"未指定数据表，使用第一个: {tables[0].get('name', table_id)}")

        # 3. 获取附件字段
        attachment_fields = self.client.list_fields(app_token, table_id)
        if not attachment_fields:
            logger.warning("未找到附件字段，无需导入")
            return self._build_summary(0, 0, 0, 0, time.time() - start_time)

        # 4. 获取所有记录
        records = self.client.get_records(app_token, table_id)
        if not records:
            logger.warning("没有记录需要处理")
            return self._build_summary(0, 0, 0, 0, time.time() - start_time)

        # 5. 去重
        new_by_product = self.find_new_videos(records, attachment_fields)

        # 6. 过滤目标产品
        if target_product:
            if target_product in new_by_product:
                new_by_product = {target_product: new_by_product[target_product]}
            else:
                logger.warning(f"产品「{target_product}」无新增视频")
                new_by_product = {}

        if not new_by_product:
            logger.info("没有新增视频需要下载")
            return self._build_summary(
                len(records), 0, 0, 0, time.time() - start_time
            )

        # 7. 下载
        date_str = datetime.now().strftime("%Y%m%d")
        total_new = 0
        total_downloaded = 0
        total_failed = 0

        for product, videos in new_by_product.items():
            safe_name = self._safe_product_name(product)
            import_dir = os.path.join(
                self.config.download_dir, safe_name, f"import_{date_str}"
            )

            product_new = len(videos)
            product_downloaded = 0
            product_failed = 0

            logger.info(
                f"\n{'='*60}\n"
                f"导入产品: {product} ({product_new} 个新增视频)\n"
                f"目标目录: {import_dir}\n"
                f"{'='*60}"
            )

            if dry_run:
                logger.info("  [DRY-RUN] 跳过下载")
                for v in videos:
                    logger.info(f"    将下载: {v['filename']}")
                product_downloaded = 0
            else:
                for i, v in enumerate(videos, 1):
                    logger.info(
                        f"  [{i}/{product_new}] 下载: {v['filename']}"
                    )
                    success = self.client.download_attachment(
                        v["file_token"], v.get("extra"),
                        import_dir, v["filename"],
                    )
                    if success:
                        product_downloaded += 1
                    else:
                        product_failed += 1

            # 更新 manifest
            if not dry_run and product_downloaded > 0:
                manifest = self.load_manifest(product)
                for v in videos:
                    manifest[v["file_token"]] = v["filename"]
                self.save_manifest(product, manifest)

            self.stats[product] = {
                "new": product_new,
                "downloaded": product_downloaded,
                "failed": product_failed,
                "dir": import_dir,
            }

            total_new += product_new
            total_downloaded += product_downloaded
            total_failed += product_failed

        elapsed = time.time() - start_time
        return self._build_summary(
            len(records), total_new, total_downloaded, total_failed, elapsed
        )

    def _build_summary(
        self, total_records: int, total_new: int,
        downloaded: int, failed: int, elapsed: float,
    ) -> dict:
        """构建汇总报告"""
        return {
            "total_records": total_records,
            "total_products": len(self.stats),
            "total_new_videos": total_new,
            "downloaded": downloaded,
            "failed": failed,
            "elapsed_sec": elapsed,
            "products": self.stats,
        }

    def print_summary(self, summary: dict):
        """打印汇总报告"""
        logger.info("\n" + "=" * 60)
        logger.info("飞书多维表格视频导入报告")
        logger.info("=" * 60)
        logger.info(f"  多维表格: {self.config.base_url}")
        logger.info(f"  总记录数: {summary['total_records']}")
        logger.info(f"  产品数: {summary['total_products']}")
        logger.info(f"  本次新增视频: {summary['total_new_videos']}")
        logger.info(f"  下载成功: {summary['downloaded']}")
        logger.info(f"  下载失败: {summary['failed']}")

        if summary["products"]:
            logger.info("\n  产品明细:")
            for product, stats in summary["products"].items():
                logger.info(
                    f"    {product}: 新增 {stats['new']}, "
                    f"下载 {stats['downloaded']}, "
                    f"失败 {stats['failed']}"
                    f"{' → ' + stats['dir'] if stats.get('dir') else ''}"
                )

        logger.info(f"\n  总耗时: {summary['elapsed_sec']:.1f}s")
        logger.info("=" * 60)


# ============================================================
# CLI 入口
# ============================================================

def setup_logging(level: int = logging.INFO):
    """配置日志"""
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    """命令行主入口"""
    parser = argparse.ArgumentParser(
        description="飞书多维表格视频导入工具 - Videoprecut",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 从 .env 读取配置
  python -m src.bitable_import

  # 预览模式（不实际下载）
  python -m src.bitable_import --dry-run

  # 仅导入指定产品
  python -m src.bitable_import --product "产品A"

  # 手动指定参数
  python -m src.bitable_import \\
      --app-id cli_xxx \\
      --app-secret xxx \\
      --base-url https://xxx.feishu.cn/base/xxx \\
      --user-access-token u-xxx

环境变量 (.env):
  FEISHU_APP_ID           飞书应用 ID
  FEISHU_APP_SECRET       飞书应用密钥
  FEISHU_BASE_URL         多维表格 URL
  FEISHU_USER_ACCESS_TOKEN 用户访问令牌
  FEISHU_DOWNLOAD_DIR     下载根目录（默认: input）
  FEISHU_PRODUCT_FIELD    产品名称字段（默认: 应用名称）
        """,
    )

    parser.add_argument(
        "--app-id", type=str, default="",
        help="飞书应用 ID（也可通过 FEISHU_APP_ID 环境变量设置）",
    )
    parser.add_argument(
        "--app-secret", type=str, default="",
        help="飞书应用密钥（也可通过 FEISHU_APP_SECRET 环境变量设置）",
    )
    parser.add_argument(
        "--base-url", type=str, default="",
        help="多维表格 URL（也可通过 FEISHU_BASE_URL 环境变量设置）",
    )
    parser.add_argument(
        "--user-access-token", type=str, default="",
        help="用户访问令牌（也可通过 FEISHU_USER_ACCESS_TOKEN 环境变量设置）",
    )
    parser.add_argument(
        "--download-dir", type=str, default="",
        help="下载根目录（默认: input）",
    )
    parser.add_argument(
        "--product-field", type=str, default="",
        help="产品名称字段（默认: 应用名称）",
    )
    parser.add_argument(
        "--product", type=str, default=None,
        help="仅导入指定产品（默认: 全部）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="预览模式，不实际下载",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="详细日志输出",
    )

    args = parser.parse_args()

    # 配置日志
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    # 构建配置（CLI 参数优先于环境变量）
    env_config = BitableConfig.from_env()
    config = BitableConfig(
        app_id=args.app_id or env_config.app_id,
        app_secret=args.app_secret or env_config.app_secret,
        base_url=args.base_url or env_config.base_url,
        user_access_token=args.user_access_token or env_config.user_access_token,
        download_dir=args.download_dir or env_config.download_dir,
        product_field=args.product_field or env_config.product_field,
    )

    # 验证必填项
    missing = config.validate()
    if missing:
        logger.error(
            f"缺少必填配置项: {', '.join(missing)}\n"
            f"请通过命令行参数或 .env 文件设置"
        )
        sys.exit(1)

    # 执行导入
    try:
        client = FeishuClient(config)
        manager = ImportManager(config, client)
        summary = manager.run(
            dry_run=args.dry_run,
            target_product=args.product,
        )
        manager.print_summary(summary)

        if summary["failed"] > 0:
            sys.exit(1)

    except KeyboardInterrupt:
        logger.info("\n用户中断")
        sys.exit(130)
    except Exception as e:
        logger.error(f"导入失败: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
