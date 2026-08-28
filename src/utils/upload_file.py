# 对象存储统一上传入口（配置判断 + 路由分发；具体上传与重试在 cos/oss/tos + storage_upload_retry）
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path
import shutil
import uuid

import config
from exceptions import CustomError, CustomException
from src.utils.logger import logger
from src.utils.cos import cos_upload_file
from src.utils.oss import oss_upload_file
from src.utils.tos import tos_upload_file


def _is_valid_storage_config(value: str) -> bool:
    """判断存储配置项是否有效（空串和占位符视为未配置）。"""
    normalized = (value or "").strip()
    return normalized != "" and normalized.lower() != "xxx"


def _is_cos_configured() -> bool:
    """判断 COS 配置是否完整有效。"""
    return all(
        _is_valid_storage_config(item)
        for item in (config.COS_SECRET_ID, config.COS_SECRET_KEY, config.COS_BUCKET_NAME, config.COS_REGION)
    )


def _is_oss_configured() -> bool:
    """判断 OSS 配置是否完整有效。"""
    return all(
        _is_valid_storage_config(item)
        for item in (
            config.OSS_ACCESS_KEY_ID,
            config.OSS_ACCESS_KEY_SECRET,
            config.OSS_BUCKET_NAME,
            config.OSS_ENDPOINT,
        )
    )


def _is_tos_configured() -> bool:
    """判断 TOS 配置是否完整有效（ENDPOINT 可选，未设置时按地域自动生成）。"""
    return all(
        _is_valid_storage_config(item)
        for item in (
            config.TOS_ACCESS_KEY_ID,
            config.TOS_ACCESS_KEY_SECRET,
            config.TOS_BUCKET_NAME,
            config.TOS_REGION,
        )
    )


def upload_file(file_path: str, expire_days: Optional[int] = None) -> str:
    """
    上传文件到对象存储并返回带签名的临时URL。

    Self-hosted default: copy into FastAPI's /files tree. Cloud object
    storage remains available only when explicitly selected.
    """
    if expire_days is None:
        expire_days = config.VIDEO_GEN_RETENTION_DAYS

    try:
        if config.STORAGE_BACKEND == "local":
            source = Path(file_path)
            if not source.is_file():
                raise FileNotFoundError(file_path)
            date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            target_dir = Path(config.LOCAL_STORAGE_DIR) / date_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{uuid.uuid4().hex}{source.suffix or '.mp4'}"
            shutil.copy2(source, target)
            relative = target.relative_to(Path(config.OUTPUT_DIR)).as_posix()
            return f"{config.SELF_HOST_BASE_URL}/files/{relative}"

        if config.STORAGE_BACKEND not in {"auto", "cos", "oss", "tos"}:
            raise CustomException(
                CustomError.INTERNAL_SERVER_ERROR,
                f"Unsupported STORAGE_BACKEND: {config.STORAGE_BACKEND}",
            )

        if config.STORAGE_BACKEND in {"auto", "cos"} and _is_cos_configured():
            logger.info("Detected COS config, using COS upload")
            return cos_upload_file(file_path=file_path, expire_days=expire_days)

        if config.STORAGE_BACKEND in {"auto", "oss"} and _is_oss_configured():
            logger.info("COS config not found, fallback to OSS upload")
            return oss_upload_file(file_path=file_path, expire_days=expire_days)

        if config.STORAGE_BACKEND in {"auto", "tos"} and _is_tos_configured():
            logger.info("COS/OSS config not found, fallback to TOS upload")
            return tos_upload_file(file_path=file_path, expire_days=expire_days)

        raise CustomException(
            CustomError.INTERNAL_SERVER_ERROR,
            "Neither COS, OSS nor TOS storage config is available"
        )
    except Exception as e:
        if isinstance(e, CustomException):
            raise
        logger.error(f"Storage upload failed: {e}")
        raise CustomException(CustomError.INTERNAL_SERVER_ERROR, "Storage upload failed")
