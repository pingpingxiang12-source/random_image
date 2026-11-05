"""随机图片 HTTP 服务（Render 云平台部署版）。

接口：
    GET /         随机返回 image 目录中的一张图片
    GET /healthz  健康检查，Render 用它判断服务是否启动成功

本地运行：
    python server.py
Render 运行：
    gunicorn server:app --bind 0.0.0.0:$PORT ...
"""

from __future__ import annotations

import logging
import os
import random
import threading
from pathlib import Path

from flask import Flask, jsonify, send_file

# 项目根目录：所有相对路径都以 server.py 所在目录为基准，
# 这样在 Render 的任意工作目录下启动都不会找错图片。
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_IMAGE_DIR = BASE_DIR / "images"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

logger = logging.getLogger("random_image")


def _resolve_image_dir(raw_value: str | None) -> Path:
    """把 IMAGE_DIR 配置解析成绝对路径，相对路径按项目根目录计算。"""
    if not raw_value:
        return DEFAULT_IMAGE_DIR
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _scan_images(image_dir: Path) -> list[Path]:
    """列出目录中所有受支持的图片文件；目录不存在或不可读时返回空列表。"""
    try:
        entries = list(image_dir.iterdir())
    except FileNotFoundError:
        logger.warning("图片目录不存在：%s", image_dir)
        return []
    except OSError as exc:
        logger.warning("无法读取图片目录 %s：%s", image_dir, exc)
        return []

    return sorted(
        entry
        for entry in entries
        if entry.is_file() and entry.suffix.lower() in IMAGE_SUFFIXES
    )


def create_app(image_dir: str | None = None) -> Flask:
    """创建 Flask 应用。image_dir 为空时读取 IMAGE_DIR 环境变量。"""
    app = Flask(__name__)
    app.config["IMAGE_DIR"] = _resolve_image_dir(
        image_dir if image_dir is not None else os.environ.get("IMAGE_DIR")
    )
    app.json.ensure_ascii = False

    # 图片列表缓存：只在为空时重新扫描，避免每个请求都遍历目录。
    cache: list[Path] = []
    cache_lock = threading.Lock()

    def available_images() -> list[Path]:
        nonlocal cache
        with cache_lock:
            if not cache:
                cache = _scan_images(app.config["IMAGE_DIR"])
            return list(cache)

    def forget_image(path: Path) -> None:
        with cache_lock:
            if path in cache:
                cache.remove(path)

    @app.after_request
    def add_common_headers(response):
        # 允许浏览器 / 前端页面跨域调用本服务
        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        return response

    @app.get("/")
    def random_image():
        images = available_images()
        if not images:
            return (
                jsonify(
                    {
                        "error": "no images available",
                        "image_dir": str(app.config["IMAGE_DIR"]),
                        "hint": "把图片放进 IMAGE_DIR 目录，或调整 IMAGE_DIR 环境变量",
                    }
                ),
                404,
            )

        # 最多尝试两次：万一缓存里的文件刚被删除，换一张再返回。
        for _ in range(2):
            path = random.choice(images)
            if path.is_file():
                response = send_file(path, conditional=False, max_age=0)
                # 禁止浏览器缓存，保证每次刷新都重新请求，随机效果才看得见。
                response.headers["Cache-Control"] = "no-store, max-age=0"
                response.headers["X-Image-Name"] = path.name
                return response

            forget_image(path)
            images = available_images()
            if not images:
                break

        return jsonify({"error": "no images available"}), 404

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", image_count=len(available_images()))

    return app


app = create_app()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    image_dir = Path(app.config["IMAGE_DIR"])
    logger.info("图片目录：%s（共 %d 张图片）", image_dir, len(_scan_images(image_dir)))
    logger.info("服务地址：http://%s:%d/", host, port)

    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
