# -*- coding: utf-8 -*-
# @Description : 项目配置（Python 3.12 + pydantic-settings v2）
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import dotenv
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录
PROJECT_DIR: Path = Path(__file__).parents[1]
if PROJECT_DIR.name == "lib":  # cx_Freeze 打包后根目录会变
    PROJECT_DIR = PROJECT_DIR.parent

# 加载 .env / project_env
dotenv.load_dotenv(PROJECT_DIR / "project_env")

# 读取 pyproject.toml
with (PROJECT_DIR / "pyproject.toml").open("rb") as _f:
    _toml = tomllib.load(_f)

VERSION: str = _toml["tool"]["commitizen"]["version"]
VERSION_FORMAT: str = _toml["tool"]["commitizen"]["tag_format"].replace("$version", VERSION)
_PROJECT_CONFIG: dict = _toml["myproject"]


# ---------- 配置模型 ----------
class DatabasePoolConfig(BaseModel):
    minsize: int = 24
    maxsize: int = 24


class RedisSentinelsConfig(BaseModel):
    service: list[tuple[str, int]] = Field(default_factory=list)
    service_name: str = "mymaster"


class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    user: str = ""
    password: str = ""
    namespace: str = "FastSample"
    retry_interval: int = 10
    sentinels: RedisSentinelsConfig = Field(default_factory=RedisSentinelsConfig)
    more_config: dict = Field(default_factory=dict)


class MQConfig(BaseModel):
    bootstrap_servers: list[str] = Field(default_factory=lambda: ["localhost:9092"])
    user: str = ""
    password: str = ""
    retry_interval: int = 5
    num_partitions: int = 1
    replication_factor: int = 1
    topic_configs: dict = Field(default_factory=dict)
    topics: dict = Field(default_factory=dict)


class AppSettings(BaseSettings):
    """
    优先级：环境变量 > project_env > pyproject.toml > 默认值
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_DIR / "project_env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    DEV: bool = bool(_PROJECT_CONFIG.get("DEV", False))
    PROD: bool = not bool(_PROJECT_CONFIG.get("DEV", False)) and bool(_PROJECT_CONFIG.get("PROD", True))
    LOG_LEVEL: str = str(_PROJECT_CONFIG.get("LOG_LEVEL", "info")).upper()

    HTTP_API_LISTEN_HOST: str = _PROJECT_CONFIG.get("HTTP_API_LISTEN_HOST", "0.0.0.0")
    HTTP_API_LISTEN_PORT: int = 8080
    HTTP_BASE_URL: str = _PROJECT_CONFIG.get("HTTP_BASE_URL", "/api/sample")

    # 数据库
    DATABASE_HOST: str = Field(default="localhost", alias="FASTSAMPLE_DATABASE_HOST")
    DATABASE_PORT: int = Field(default=3306, alias="FASTSAMPLE_DATABASE_PORT")
    DATABASE_USER: str = Field(default="root", alias="FASTSAMPLE_DATABASE_USER")
    DATABASE_PASSWORD: str = Field(default="123456", alias="FASTSAMPLE_DATABASE_PASSWORD")
    DATABASE_NAME: str = Field(default="FastSample", alias="FASTSAMPLE_DATABASE_NAME")

    # Redis / Kafka 环境变量覆盖
    FS_REDIS_HOST: str | None = None
    FS_REDIS_PORT: int | None = None
    FS_REDIS_SENTINEL_SERVICE: str | None = None
    FS_REDIS_SENTINEL_SERVICE_NAME: str | None = None
    FS_KAFKA_SERVICE: str | None = None


settings = AppSettings()

DEV = settings.DEV
PROD = settings.PROD
HTTP_API_LISTEN_HOST = settings.HTTP_API_LISTEN_HOST
HTTP_API_LISTEN_PORT = settings.HTTP_API_LISTEN_PORT
HTTP_BASE_URL = settings.HTTP_BASE_URL

DEV and logger.info("[DEV] Server")
PROD and logger.info("[PROD] Server")

# 数据库配置池
_db_pool = DatabasePoolConfig(**_PROJECT_CONFIG.get("database", {}))

# Redis 配置
_redis_raw = _PROJECT_CONFIG.get("redis", {})
# tuple 化 sentinel service
if "sentinels" in _redis_raw and "service" in _redis_raw["sentinels"]:
    _redis_raw["sentinels"]["service"] = [tuple(s) for s in _redis_raw["sentinels"]["service"]]
_redis_cfg = RedisConfig(**_redis_raw)

# 环境变量覆盖
if settings.FS_REDIS_HOST:
    _redis_cfg.host = settings.FS_REDIS_HOST
if settings.FS_REDIS_PORT:
    _redis_cfg.port = settings.FS_REDIS_PORT
if settings.FS_REDIS_SENTINEL_SERVICE:
    _redis_cfg.sentinels.service = [
        (host.strip(), int(port)) for host, port in (item.split(":") for item in settings.FS_REDIS_SENTINEL_SERVICE.split(","))
    ]
if settings.FS_REDIS_SENTINEL_SERVICE_NAME:
    _redis_cfg.sentinels.service_name = settings.FS_REDIS_SENTINEL_SERVICE_NAME

REDIS_CONFIG = _redis_cfg.model_dump()

# MQ 配置
_mq_cfg = MQConfig(**_PROJECT_CONFIG.get("mq", {}))
if settings.FS_KAFKA_SERVICE:
    _mq_cfg.bootstrap_servers = [item.strip() for item in settings.FS_KAFKA_SERVICE.split(",")]
MQ_CONFIG = _mq_cfg.model_dump()

# 项目级配置（兼容老代码）
PROJECT_CONFIG = _PROJECT_CONFIG

# ---------- 日志 ----------
LOGGER_CONFIG = {
    "handlers": [
        {
            "sink": sys.stdout,
            "level": "DEBUG" if DEV else settings.LOG_LEVEL,
            "enqueue": True,
            "backtrace": True,
            "diagnose": True,
            "catch": True,
        },
        {
            "sink": PROJECT_DIR / "logs" / "project.log",
            "rotation": "3 MB",
            "retention": "30 days",
            "level": "INFO",
            "enqueue": True,
            "backtrace": True,
            "diagnose": True,
            "encoding": "utf-8",
            "catch": True,
        },
    ]
}
logger.configure(**LOGGER_CONFIG)

# ---------- 时区 / 数据库 ----------
DEFAULT_TIMEZONE = "UTC"
LOCAL_TIMEZONE = "Asia/Shanghai"

DATABASE_CONFIG: dict = {
    "connections": {
        "default": {
            "engine": "tortoise.backends.mysql",
            "credentials": {
                "host": settings.DATABASE_HOST,
                "port": settings.DATABASE_PORT,
                "user": settings.DATABASE_USER,
                "password": settings.DATABASE_PASSWORD,
                "database": settings.DATABASE_NAME,
                "minsize": _db_pool.minsize,
                "maxsize": _db_pool.maxsize,
                "charset": "utf8mb4",
                "pool_recycle": 3600,
            },
        },
    },
    "apps": {
        # 真正使用时取消注释：
        # "user": {
        #     "models": ["src.faster.routers.users.models"],
        #     "default_connection": "default",
        # },
        # "resource": {
        #     "models": ["src.faster.routers.resource.models"],
        #     "default_connection": "default",
        # },
    },
    "use_tz": True,
    "timezone": DEFAULT_TIMEZONE,
}

if DEV:
    DATABASE_CONFIG["apps"]["aerich"] = {
        "models": ["aerich.models"],
        "default_connection": "default",
    }
