# Bitable Video Import 模块设计方案

## 1. 概述

将飞书多维表格中的视频附件自动下载到本地 `input/` 目录，按产品（应用名称）分文件夹，支持去重和增量导入。

### 核心需求
- **数据源**: 飞书多维表格，附件字段存储视频（`video/mp4` 等）
- **产品字段**: 「应用名称」
- **去重依据**: 飞书 `file_token`（稳定不变）
- **增量导入**: 每次运行仅下载新增视频，放入 `import_{YYYYMMDD}/` 子文件夹
- **技术栈**: 纯 Python，独立 CLI 命令

---

## 2. 模块架构

```
src/bitable_import.py          ← 单文件模块（~400行）
├── BitableConfig              ← 配置 dataclass
├── FeishuClient               ← 飞书 API 封装
│   ├── get_tenant_access_token()
│   ├── parse_base_url()
│   ├── list_tables()
│   ├── list_fields()
│   ├── get_records()
│   └── download_attachment()
├── ImportManager              ← 导入逻辑
│   ├── load_manifest()
│   ├── save_manifest()
│   ├── find_new_videos()
│   └── run()
└── main()                     ← CLI 入口
```

---

## 3. 数据流

```
┌──────────────────────────────────────────────────────────────┐
│                     CLI / .env 配置                           │
│  APP_ID, APP_SECRET, BASE_URL, USER_ACCESS_TOKEN,            │
│  DOWNLOAD_PATH=input, PRODUCT_FIELD=应用名称                   │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  FeishuClient                                                │
│                                                              │
│  ① get_tenant_access_token(app_id, app_secret)               │
│     → tenant_access_token                                    │
│                                                              │
│  ② parse_base_url(tenant_token, base_url)                    │
│     → { app_token, table_id, view_id }                       │
│                                                              │
│  ③ list_fields(tenant_token, app_token, table_id)            │
│     → 筛选 type=17 的附件字段                                 │
│                                                              │
│  ④ get_records(user_token, app_token, table_id)              │
│     → 分页获取所有记录（page_size=500）                        │
│                                                              │
│  ⑤ download_attachment(user_token, file_token, extra, path)  │
│     → 流式下载到本地文件                                       │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  ImportManager                                               │
│                                                              │
│  ⑥ 按「应用名称」分组记录                                     │
│     records_by_product = {                                    │
│       "产品A": [record1, record2, ...],                       │
│       "产品B": [record3, ...],                                │
│     }                                                        │
│                                                              │
│  ⑦ 对每个产品：                                               │
│     ├── load_manifest(product) → {file_token: filename}       │
│     ├── 比对 file_token → 找出新增                             │
│     ├── 创建 input/{product}/import_{YYYYMMDD}/               │
│     ├── 逐个下载新增视频（控制 5 QPS）                         │
│     └── save_manifest(product, updated_manifest)              │
│                                                              │
│  ⑧ 输出汇总报告                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. 目录结构设计

```
input/
├── 产品A/                          ← 产品专属根目录
│   ├── manifest.json               ← 已导入文件清单
│   │   {                            {
│   │     "file_token_aaa": "vid1.mp4",
│   │     "file_token_bbb": "vid2.mp4"
│   │   }                            }
│   ├── import_20260428/             ← 2026-04-28 批次新增
│   │   ├── vid3.mp4
│   │   └── vid4.mp4
│   └── import_20260501/             ← 2026-05-01 批次新增
│       └── vid5.mp4
│
├── 产品B/
│   ├── manifest.json
│   └── import_20260428/
│       └── vid6.mp4
│
└── 产品C/
    ├── manifest.json
    └── import_20260428/
        └── vid7.mp4
```

**设计要点**:
- `manifest.json` 是扁平映射表，记录所有历史导入的 `file_token → filename`
- 每次运行创建新的 `import_{YYYYMMDD}/` 子文件夹，仅放入本次新增
- 历史批次的文件夹保留不删，方便追溯
- 文件名使用飞书附件原始名称（`attachment.name`）

---

## 5. 核心类设计

### 5.1 BitableConfig

```python
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
    timeout: int = 120            # 下载超时（秒）
```

### 5.2 FeishuClient

```python
class FeishuClient:
    """飞书 Open API 客户端"""
    
    BASE = "https://open.feishu.cn/open-apis"
    
    def __init__(self, config: BitableConfig):
        self.config = config
        self._tenant_token: Optional[str] = None
    
    # ── 认证 ──
    def get_tenant_access_token(self) -> str:
        """获取 tenant_access_token（自动缓存，过期刷新）"""
    
    # ── URL 解析 ──
    def parse_base_url(self) -> dict:
        """解析多维表格 URL → {app_token, table_id, view_id}"""
    
    def get_wiki_node_info(self, node_token: str) -> dict:
        """知识库节点 → 获取 obj_token"""
    
    # ── 数据读取 ──
    def list_tables(self) -> list:
        """列出所有数据表"""
    
    def list_fields(self, table_id: str) -> list:
        """列出字段（分页），筛选 type=17 附件字段"""
    
    def get_records(self, table_id: str) -> list:
        """分页获取所有记录（page_size=500）"""
    
    # ── 下载 ──
    def download_attachment(
        self, file_token: str, extra: str, 
        save_path: str, filename: str
    ) -> bool:
        """流式下载单个附件，返回成功/失败"""
```

### 5.3 ImportManager

```python
class ImportManager:
    """导入管理器：去重 + 增量下载"""
    
    def __init__(self, config: BitableConfig, client: FeishuClient):
        self.config = config
        self.client = client
    
    # ── Manifest 管理 ──
    def _manifest_path(self, product: str) -> str:
        """产品 manifest.json 路径"""
        return f"{self.config.download_dir}/{product}/manifest.json"
    
    def load_manifest(self, product: str) -> dict:
        """加载已导入清单 {file_token: filename}"""
    
    def save_manifest(self, product: str, manifest: dict):
        """保存更新后的清单"""
    
    # ── 去重 ──
    def find_new_videos(
        self, records: list, attachment_fields: list
    ) -> dict:
        """
        按产品分组，比对 manifest，返回新增视频。
        
        Returns:
            {
              "产品A": [
                {"file_token": "xxx", "filename": "a.mp4", "extra": "..."},
                ...
              ],
              "产品B": [...]
            }
        """
    
    # ── 主流程 ──
    def run(self) -> dict:
        """
        执行完整导入流程，返回汇总报告。
        
        Returns:
            {
              "total_products": 3,
              "total_new_videos": 12,
              "downloaded": 12,
              "failed": 0,
              "products": {
                "产品A": {"new": 5, "downloaded": 5, "failed": 0},
                ...
              }
            }
        """
```

---

## 6. CLI 接口设计

```bash
# 基本用法（从 .env 读取配置）
python -m src.bitable_import

# 指定参数
python -m src.bitable_import \
    --app-id cli_xxx \
    --app-secret xxx \
    --base-url https://xxx.feishu.cn/base/xxx?table=tblxxx \
    --user-access-token u-xxx \
    --download-dir input \
    --product-field "应用名称"

# 仅预览（dry-run，不实际下载）
python -m src.bitable_import --dry-run

# 指定产品（仅导入特定产品）
python -m src.bitable_import --product "产品A"
```

### 环境变量（.env）

```bash
# 飞书多维表格导入配置
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_BASE_URL=https://xxx.feishu.cn/base/xxx
FEISHU_USER_ACCESS_TOKEN=u-xxx
FEISHU_DOWNLOAD_DIR=input
FEISHU_PRODUCT_FIELD=应用名称
```

---

## 7. 去重逻辑详解

```
对每个产品 P：

  1. 读取 manifest.json → known_tokens = {token1, token2, ...}
  
  2. 遍历该产品的所有记录：
     对每条记录的每个附件字段：
       对每个附件（mime_type 以 "video/" 开头）：
         if attachment.file_token NOT IN known_tokens:
           → 标记为「新增」
  
  3. 下载所有新增视频到 input/{P}/import_{YYYYMMDD}/
  
  4. 更新 manifest.json：
     known_tokens[file_token] = filename
  
  5. 如果某产品无新增，跳过（不创建空文件夹）
```

**边界情况**:
- 首次导入（无 manifest.json）→ 全部视为新增
- manifest.json 损坏 → 警告并重建（全部重新下载）
- 同一 file_token 出现在多条记录 → 仅下载一次
- 产品名含特殊字符 → 做文件名安全处理（替换 `/` `\` `:` 等）

---

## 8. 速率控制

飞书 API 限制 **5 QPS**，留余量按 **4 QPS** 设计：

```python
import time

class RateLimiter:
    def __init__(self, qps: float = 4.0):
        self.interval = 1.0 / qps  # 0.25s
        self._last_call = 0.0
    
    def wait(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.time()
```

仅对 API 调用限速（`list_fields`、`get_records`、`download_attachment`），本地文件操作不限。

---

## 9. 错误处理

| 场景 | 处理策略 |
|------|---------|
| 认证失败 | 退出，提示检查 APP_ID/APP_SECRET |
| app_token 无效 (1254040) | 退出，提示检查 BASE_URL |
| 权限不足 (403) | 退出，提示检查应用权限和协作者 |
| 单文件下载失败 | 记录错误，继续下载其余文件 |
| 网络超时 | 重试 3 次（指数退避），仍失败则跳过 |
| 磁盘空间不足 | 退出，提示清理空间 |
| 产品字段缺失 | 跳过该记录，警告日志 |

---

## 10. 依赖

```
# 已有依赖（无需新增）
requests>=2.28       # HTTP 请求
python-dotenv>=1.0   # .env 加载

# 无需额外安装
```

---

## 11. 实现步骤

| 步骤 | 内容 | 产出 |
|------|------|------|
| 1 | 创建 `src/bitable_import.py` 骨架 | 模块文件 + BitableConfig |
| 2 | 实现 `FeishuClient` | API 认证 + 数据读取 + 下载 |
| 3 | 实现 `ImportManager` | Manifest 管理 + 去重 + 增量导入 |
| 4 | 实现 CLI (`main()`) | argparse + .env 集成 |
| 5 | 更新 `.env` 模板 | 新增飞书配置项 |
| 6 | 端到端测试 | 连接真实多维表格验证 |

---

## 12. 与现有系统的集成

```
                    ┌──────────────────┐
                    │   .env           │
                    │   FEISHU_*       │
                    │   DASHSCOPE_*    │
                    └──────┬───────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
     bitable_import.py  main.py    parallel.py
     (新增)             (现有)      (现有)
              │            │            │
              │  下载到    │  读取自    │
              │  input/    │  input/    │
              └────────────┼────────────┘
                           │
                           ▼
                       input/
                    (共享目录)
```

- `bitable_import.py` 写入 `input/{product}/import_{date}/`
- `main.py` 的 `scan_input_dir()` 递归扫描 `input/`，自动发现新视频
- 两个模块完全解耦，可独立运行

---

## 13. 输出报告示例

```
============================================================
飞书多维表格视频导入报告
============================================================
  多维表格: https://xxx.feishu.cn/base/xxx
  数据表: tblxxx
  附件字段: 视频素材 (field_id: fldxxx)
  总记录数: 156
  产品数: 5
  本次新增视频: 23
  下载成功: 23
  下载失败: 0

  产品明细:
    产品A: 新增 8, 下载 8, 失败 0 → input/产品A/import_20260428/
    产品B: 新增 5, 下载 5, 失败 0 → input/产品B/import_20260428/
    产品C: 新增 6, 下载 6, 失败 0 → input/产品C/import_20260428/
    产品D: 新增 4, 下载 4, 失败 0 → input/产品D/import_20260428/
    产品E: 新增 0 (无新增)

  总耗时: 45.2s
============================================================
```
