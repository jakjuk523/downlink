# -*- coding: utf-8 -*-
"""
DOWNLINK — backend
Flask + yt-dlp. Работает как локальный сервер (для personal use) или как
задеплоенный сервис (Render / Railway / VPS), к которому обращается
статический PWA-фронтенд, размещённый, например, на GitHub Pages.

Локальный запуск:
    python app.py
    -> сервер поднимется на http://127.0.0.1:8642 и сам откроет браузер.

Запуск в облаке (Render/Railway и т.п.):
    платформа сама выставляет переменную окружения PORT — сервер это
    определяет и переходит в "серверный" режим (без авто-открытия браузера,
    слушает 0.0.0.0).
"""

import os
import sys
import json
import time
import queue
import shutil
import threading
import webbrowser
from pathlib import Path
from uuid import uuid4

from flask import Flask, request, jsonify, Response, send_file, send_from_directory
from flask_cors import CORS

try:
    import yt_dlp
except ImportError:
    print("Не найден модуль yt-dlp. Установите его командой: pip install yt-dlp")
    sys.exit(1)

APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR.parent / "frontend"
DOWNLOAD_DIR = Path(os.environ.get("DOWNLINK_STORAGE", str(Path(os.path.expanduser("~/Downloads")) / "Downlink")))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_MODE = "PORT" not in os.environ  # облачные платформы всегда задают PORT

app = Flask(__name__)
# В проде лучше сузить до конкретного домена фронтенда:
# CORS(app, resources={r"/api/*": {"origins": "https://<твой-юзернейм>.github.io"}})
CORS(app, resources={r"/api/*": {"origins": "*"}})

JOBS = {}
JOBS_LOCK = threading.Lock()

# Соответствие пунктов меню качества параметрам yt-dlp
QUALITY_MAP = {
    "240": 240,
    "360": 360,
    "480": 480,
    "720": 720,
    "1080": 1080,
    "2k": 1440,
    "4k": 2160,
}


def new_job(urls, quality):
    job_id = uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "urls": urls,
            "quality": quality,
            "events": queue.Queue(),
            "cancelled": False,
            "paused": False,
            "done": False,
            "files": {},   # index -> абсолютный путь к готовому файлу
        }
    return job_id


def emit(job, event_type, payload):
    payload = dict(payload)
    payload["type"] = event_type
    job["events"].put(payload)


def human_size(num_bytes):
    if not num_bytes:
        return "—"
    for unit in ["Б", "КБ", "МБ", "ГБ"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} ТБ"


def human_eta(seconds):
    if seconds is None:
        return "—"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}ч {m:02d}м {s:02d}с"
    if m:
        return f"{m}м {s:02d}с"
    return f"{s}с"


def make_hook(job, index, total):
    start_time = time.time()

    def hook(d):
        while job["paused"] and not job["cancelled"]:
            time.sleep(0.2)
        if job["cancelled"]:
            raise yt_dlp.utils.DownloadError("Отменено пользователем")

        if d["status"] == "downloading":
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            percent = round((downloaded / total_bytes) * 100, 1) if total_bytes else 0
            speed = d.get("speed") or 0
            eta = d.get("eta")
            emit(job, "progress", {
                "index": index, "total": total, "percent": percent,
                "downloaded": human_size(downloaded),
                "total_size": human_size(total_bytes),
                "speed": human_size(speed) + "/с" if speed else "—",
                "eta": human_eta(eta),
                "elapsed": human_eta(time.time() - start_time),
            })
        elif d["status"] == "finished":
            emit(job, "progress", {
                "index": index, "total": total, "percent": 100,
                "downloaded": human_size(d.get("total_bytes") or 0),
                "total_size": human_size(d.get("total_bytes") or 0),
                "speed": "—", "eta": "0с",
                "elapsed": human_eta(time.time() - start_time),
                "stage": "merging",
            })

    return hook


def build_format(quality):
    if quality == "audio":
        return "bestaudio/best", [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    height = QUALITY_MAP.get(quality, 1080)
    return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best", []


def locate_output_file(info, ydl):
    """Достаём путь к готовому файлу после скачивания — сначала из
    requested_downloads (современный yt-dlp), иначе ищем по id видео."""
    rds = info.get("requested_downloads") or []
    for rd in rds:
        fp = rd.get("filepath") or rd.get("_filename")
        if fp and os.path.exists(fp):
            return fp
    vid = info.get("id", "")
    if vid:
        for f in DOWNLOAD_DIR.glob(f"*[{vid}]*"):
            if f.is_file():
                return str(f)
    return None


def worker(job_id):
    job = JOBS[job_id]
    urls = job["urls"]
    total = len(urls)
    success = 0

    base_opts = {
        "retries": 10, "fragment_retries": 10, "socket_timeout": 30,
        "file_access_retries": 5, "nocheckcertificate": True,
        "quiet": True, "no_warnings": True, "noprogress": True,
    }

    for i, url in enumerate(urls, 1):
        if job["cancelled"]:
            break
        try:
            emit(job, "status", {"index": i, "total": total, "message": f"Получаю информацию ({i}/{total})…"})

            with yt_dlp.YoutubeDL(base_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            duration = info.get("duration") or 0
            emit(job, "info", {
                "index": i, "total": total,
                "title": info.get("title", "???"),
                "author": info.get("uploader") or info.get("uploader_id") or "???",
                "duration": f"{int(duration // 60)} мин {int(duration % 60)} сек",
                "views": f'{info.get("view_count", 0):,}'.replace(",", " "),
                "likes": f'{info.get("like_count", 0):,}'.replace(",", " ") if info.get("like_count") else "скрыто",
                "source": info.get("extractor_key", ""),
            })

            fmt, postprocessors = build_format(job["quality"])
            outtmpl = str(DOWNLOAD_DIR / "%(title).150B [%(id)s].%(ext)s")

            opts = {
                **base_opts, "format": fmt, "outtmpl": outtmpl,
                "postprocessors": postprocessors,
                "progress_hooks": [make_hook(job, i, total)],
            }
            if job["quality"] != "audio":
                opts["merge_output_format"] = "mp4"

            with yt_dlp.YoutubeDL(opts) as ydl:
                result_info = ydl.extract_info(url, download=True)
                filepath = locate_output_file(result_info, ydl)
                if filepath:
                    job["files"][i] = filepath

            success += 1
            emit(job, "item_done", {
                "index": i, "total": total,
                "title": info.get("title", "???"),
                "downloadable": i in job["files"],
            })

        except Exception as e:
            emit(job, "item_error", {"index": i, "total": total, "message": str(e)[:200]})
            continue

    job["done"] = True
    emit(job, "job_done", {"cancelled": job["cancelled"], "success": success, "total": total})


# ---------------------------------------------------------------------------
# Маршруты API (фронтенд — статические файлы, отдаются отдельно: локально
# из /frontend, либо через GitHub Pages)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    # В локальном режиме сервер сам раздаёт папку frontend/ — так двойной
    # клик по .bat/.sh открывает готовую страницу на 127.0.0.1:8642.
    # В облаке эта же папка обычно живёт отдельно (например, GitHub Pages),
    # а сюда достаточно ходить только за /api/*.
    if LOCAL_MODE and (FRONTEND_DIR / "index.html").exists():
        return send_from_directory(FRONTEND_DIR, "index.html")
    return jsonify({"service": "downlink-api", "status": "ok"})


@app.route("/<path:filename>")
def frontend_files(filename):
    if LOCAL_MODE and (FRONTEND_DIR / filename).exists():
        return send_from_directory(FRONTEND_DIR, filename)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "mode": "local" if LOCAL_MODE else "server",
        "storage": str(DOWNLOAD_DIR),
    })


@app.route("/api/queue", methods=["POST"])
def queue_download():
    data = request.get_json(force=True)
    raw = (data.get("urls") or "").strip()
    quality = data.get("quality", "1080")
    urls = raw.split()
    if not urls:
        return jsonify({"error": "Не передано ни одной ссылки"}), 400
    if quality != "audio" and quality not in QUALITY_MAP:
        return jsonify({"error": "Неизвестное качество"}), 400

    job_id = new_job(urls, quality)
    threading.Thread(target=worker, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id, "total": len(urls)})


@app.route("/api/stream/<job_id>")
def stream(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Задача не найдена"}), 404

    def gen():
        yield "retry: 1000\n\n"
        while True:
            try:
                event = job["events"].get(timeout=1)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "job_done":
                    break
            except queue.Empty:
                if job["done"]:
                    break
                yield ": keep-alive\n\n"

    return Response(gen(), mimetype="text/event-stream")


@app.route("/api/file/<job_id>/<int:index>")
def get_file(job_id, index):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Задача не найдена"}), 404
    filepath = job["files"].get(index)
    if not filepath or not os.path.exists(filepath):
        return jsonify({"error": "Файл не найден или ещё готовится"}), 404
    return send_file(filepath, as_attachment=True, download_name=os.path.basename(filepath))


@app.route("/api/pause/<job_id>", methods=["POST"])
def pause(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    job["paused"] = not job["paused"]
    return jsonify({"paused": job["paused"]})


@app.route("/api/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    job["cancelled"] = True
    job["paused"] = False
    return jsonify({"cancelled": True})


@app.route("/api/open-folder", methods=["POST"])
def open_folder():
    if not LOCAL_MODE:
        return jsonify({"ok": False, "error": "Недоступно в серверном режиме"}), 403
    try:
        if sys.platform.startswith("win"):
            os.startfile(DOWNLOAD_DIR)  # noqa
        elif sys.platform == "darwin":
            os.system(f'open "{DOWNLOAD_DIR}"')
        else:
            os.system(f'xdg-open "{DOWNLOAD_DIR}"')
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/clear-folder", methods=["POST"])
def clear_folder():
    try:
        removed = 0
        for name in os.listdir(DOWNLOAD_DIR):
            p = DOWNLOAD_DIR / name
            if p.is_file() or p.is_symlink():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)
            removed += 1
        return jsonify({"ok": True, "removed": removed})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def open_browser():
    time.sleep(1.0)
    webbrowser.open("http://127.0.0.1:8642")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8642))
    if LOCAL_MODE:
        threading.Thread(target=open_browser, daemon=True).start()
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
    else:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
