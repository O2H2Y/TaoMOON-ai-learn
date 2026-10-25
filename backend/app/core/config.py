"""桃MOON智能闯关挑战小程序 - 后端配置"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # DeepSeek
    deepseek_api_key: str = "sk-xxx"
    deepseek_base_url: str = "https://api.deepseek.com"
    # deepseek-flash = 当前便宜档（DeepSeek-V4.1-Flash）；旧名 deepseek-chat 官方已宣布下线
    deepseek_model: str = "deepseek-flash"
    # deepseek-flash 默认开启思考模式；出题/报告/搜索 agent 都显式关闭（更快更省，且 agent 工具调用无需回传 reasoning_content）
    deepseek_thinking: str = "disabled"

    # Tavily (Web Search)
    tavily_api_key: str = ""
    enable_web_search: bool = True

    # DashScope (百炼 Embedding，用于知识库 RAG)
    dashscope_api_key: str = ""
    dashscope_embedding_model: str = "text-embedding-v4"
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 知识库 / 向量存储
    chroma_persist_dir: str = "./data/chroma"
    kb_upload_dir: str = "./data/uploads"
    kb_max_documents_per_user: int = 10
    kb_max_file_size_mb: int = 10
    kb_chunk_size: int = 1000
    kb_chunk_overlap: int = 150
    kb_retrieve_top_k: int = 4

    # 题目配图（DashScope 千问-文生图 qwen-image）
    dashscope_image_model: str = "qwen-image-2.0"
    # 生图专用 API Key（留空时回退使用 dashscope_api_key）。
    # 注意：部分 sk-ws- 开头的工作空间 Key 按用途限定权限范围，Embedding 与生图可能需要各自的 Key。
    dashscope_image_api_key: str = ""
    # 图像生成使用百炼「原生同步接口」
    #   POST {base}/api/v1/services/aigc/multimodal-generation/generation
    # 可填 /api/v1 根地址、compatible-mode 地址或裸域名，代码会自动归一化为 /api/v1。
    # 留空时回退使用 dashscope_base_url。
    dashscope_image_base_url: str = ""
    image_gen_size: str = "512*512"
    image_gen_daily_limit: int = 20
    image_gen_max_concurrency: int = 5
    # 限流控制：两次生图请求「启动」之间的最小间隔（秒）。
    # qwen-image-2.0 限流为 2 次/秒，0.7s ≈ 1.43 次/秒，既不超秒级 RPS 也避开突发保护。
    image_gen_min_interval_seconds: float = 0.7
    # 单张图片的最大尝试次数（含首次），仅对 429 / 5xx / 网络错误重试。
    image_gen_max_attempts: int = 4
    # 指数退避的基准时长与上限（秒）；服务端返回 Retry-After 时优先遵循该值。
    image_gen_retry_base_delay_seconds: float = 2.0
    image_gen_retry_max_delay_seconds: float = 30.0

    # 腾讯云 COS（用于持久化存储 AI 生成的题目配图、用户头像与分享小程序码）
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = ""
    cos_bucket: str = ""
    cos_upload_prefix: str = "quiz-images/"
    # 可选：自定义访问域名（如 CDN 加速域名），留空则使用 COS 默认域名
    cos_domain: str = ""

    # 用户头像
    avatar_max_size_mb: int = 5

    # 微信小程序分享（小程序码）
    wechat_miniprogram_env_version: str = "release"
    share_qrcode_page: str = "pages/share/index"
    share_qrcode_width: int = 430

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = True

    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_expire_minutes: int = 43200  # 30 天

    # 微信小程序
    wechat_app_id: str = ""
    wechat_app_secret: str = ""

    # MySQL
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "123456"
    mysql_database: str = "yu_ai_learn"
    mysql_charset: str = "utf8mb4"
    mysql_pool_minsize: int = 1
    mysql_pool_maxsize: int = 10
    mysql_auto_init: bool = True

    # Log
    log_level: str = "INFO"

    # 可观测性：Langfuse tracing（未配置 key 时自动关闭，不影响业务）
    enable_tracing: bool = True
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_environment: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()