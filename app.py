"""
AI Web Tester - Web 服务端 v6
精简版：仅包含 FastAPI 路由 + 任务管理
业务逻辑已拆分到 config.py / llm_engine.py / action_executor.py / evidence.py / test_runner.py
"""

import json
import uuid
import datetime
import asyncio
import threading
import queue

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
import uvicorn

from openai import OpenAI

from config import (
    BASE_DIR, SCREENSHOT_DIR, REPORT_DIR, LOG_DIR, SPECS_DIR,
    SNAPSHOT_DIR, VIDEO_DIR, MAX_TASKS,
)
from test_runner import run_test_task, resume_test_task


# ============================================================
# App 初始化
# ============================================================

app = FastAPI(title="AI Web Tester")

# 全局任务状态
tasks = {}


def _cleanup_old_tasks():
    """清理过期任务及其关联文件，保留最近 MAX_TASKS 个"""
    if len(tasks) <= MAX_TASKS:
        return
    removable = [
        (tid, t) for tid, t in tasks.items()
        if t.get("status") in ("completed", "error", "cancelled")
    ]
    removable.sort(key=lambda x: x[1].get("start_time", ""))
    to_remove = len(tasks) - MAX_TASKS
    for tid, _ in removable[:to_remove]:
        # 清理关联文件
        for pattern_dir, pattern in [
            (SCREENSHOT_DIR, f"*_{tid}.*"),
            (SNAPSHOT_DIR, f"snapshot_{tid}.json"),
            (LOG_DIR, f"log_{tid}.json"),
        ]:
            for f in pattern_dir.glob(pattern):
                try:
                    f.unlink()
                except Exception:
                    pass
        del tasks[tid]
        print(f"[INFO] 清理过期任务及文件: {tid}")


# ============================================================
# API 路由
# ============================================================

@app.post("/api/test/start")
async def start_test(request: Request):
    body = await request.json()
    spec_content = body.get("spec_content", "")
    target_url = body.get("target_url", "")
    if not spec_content.strip():
        return JSONResponse({"error": "功能预期文档内容不能为空"}, status_code=400)
    if not target_url.strip():
        return JSONResponse({"error": "目标网址不能为空"}, status_code=400)

    _cleanup_old_tasks()

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "id": task_id, "status": "starting", "start_time": datetime.datetime.now().isoformat(),
        "log_queue": queue.Queue(), "human_input_queue": queue.Queue(), "config": body,
        "pause_event": threading.Event(), "cancel_event": threading.Event(),
        "event_history": [],  # SSE 断连重连：保留事件历史
        "event_counter": 0,
    }
    tasks[task_id]["pause_event"].set()  # initially not paused
    thread = threading.Thread(target=run_test_task, args=(task_id, body, tasks), daemon=True)
    thread.start()
    return JSONResponse({"task_id": task_id, "status": "started"})


@app.post("/api/test/{task_id}/human-input")
async def submit_human_input(task_id: str, request: Request):
    if task_id not in tasks:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    body = await request.json()
    value = body.get("value", "")
    if not value:
        return JSONResponse({"error": "输入值不能为空"}, status_code=400)
    tasks[task_id]["human_input_queue"].put(value)
    return JSONResponse({"status": "ok", "message": "输入已提交"})


@app.post("/api/test/{task_id}/pause")
async def pause_test(task_id: str):
    if task_id not in tasks:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    task = tasks[task_id]
    if task.get("status") not in ("running", "waiting_input"):
        return JSONResponse({"error": "任务未在运行中"}, status_code=400)
    task["pause_event"].clear()
    task["status"] = "paused"
    task["log_queue"].put({"type": "status", "data": {"status": "paused", "message": "测试已暂停"}, "timestamp": datetime.datetime.now().isoformat()})
    return JSONResponse({"status": "paused", "message": "测试已暂停"})


@app.post("/api/test/{task_id}/resume")
async def resume_test(task_id: str):
    if task_id not in tasks:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    task = tasks[task_id]
    if task.get("status") != "paused":
        return JSONResponse({"error": "任务未处于暂停状态"}, status_code=400)
    task["status"] = "running"
    task["pause_event"].set()
    task["log_queue"].put({"type": "status", "data": {"status": "running", "message": "测试已恢复"}, "timestamp": datetime.datetime.now().isoformat()})
    return JSONResponse({"status": "running", "message": "测试已恢复"})


@app.post("/api/test/{task_id}/cancel")
async def cancel_test(task_id: str):
    if task_id not in tasks:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    task = tasks[task_id]
    task["cancel_event"].set()
    task["pause_event"].set()  # unblock if paused
    task["status"] = "cancelled"
    task["log_queue"].put({"type": "status", "data": {"status": "cancelled", "message": "测试已终止"}, "timestamp": datetime.datetime.now().isoformat()})
    return JSONResponse({"status": "cancelled", "message": "测试已终止"})


@app.post("/api/test/{task_id}/resume-snapshot")
async def resume_from_snapshot(task_id: str, request: Request):
    """从快照恢复测试"""
    snapshot_path = SNAPSHOT_DIR / f"snapshot_{task_id}.json"
    if not snapshot_path.exists():
        return JSONResponse({"error": "未找到该任务的快照文件"}, status_code=404)

    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception as e:
        return JSONResponse({"error": f"快照文件读取失败: {str(e)}"}, status_code=500)

    body = await request.json()
    rollback_steps = body.get("rollback_steps", 1)

    _cleanup_old_tasks()

    new_task_id = str(uuid.uuid4())[:8]
    original_config = snapshot.get("config", {})
    if body.get("llm_api_key"):
        original_config["llm_api_key"] = body["llm_api_key"]
    if body.get("llm_base_url"):
        original_config["llm_base_url"] = body["llm_base_url"]
    if body.get("llm_model"):
        original_config["llm_model"] = body["llm_model"]

    tasks[new_task_id] = {
        "id": new_task_id, "status": "starting",
        "start_time": datetime.datetime.now().isoformat(),
        "log_queue": queue.Queue(), "human_input_queue": queue.Queue(),
        "config": original_config,
        "pause_event": threading.Event(), "cancel_event": threading.Event(),
        "resumed_from": task_id,
        "event_history": [],
        "event_counter": 0,
    }
    tasks[new_task_id]["pause_event"].set()

    thread = threading.Thread(
        target=resume_test_task,
        args=(new_task_id, snapshot, rollback_steps, tasks),
        daemon=True,
    )
    thread.start()
    return JSONResponse({
        "task_id": new_task_id,
        "status": "resumed",
        "resumed_from": task_id,
        "resume_step": snapshot.get("step", 0) - rollback_steps,
    })


@app.get("/api/test/{task_id}/stream")
async def stream_logs(task_id: str, request: Request):
    if task_id not in tasks:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    task = tasks[task_id]
    log_queue_obj = task["log_queue"]

    # SSE 断连重连：检查 Last-Event-ID
    last_event_id = request.headers.get("Last-Event-ID", "")
    replay_from = 0
    if last_event_id:
        try:
            replay_from = int(last_event_id) + 1
        except ValueError:
            pass

    async def event_generator():
        # 先重放断连期间错过的事件
        history = task.get("event_history", [])
        for evt_id, evt_data in history:
            if evt_id >= replay_from:
                yield f"id: {evt_id}\ndata: {json.dumps(evt_data, ensure_ascii=False)}\n\n"

        # 然后继续实时推送
        while True:
            try:
                msg = log_queue_obj.get_nowait()
                # 分配递增 event ID 并存入历史
                evt_id = task.get("event_counter", 0)
                task["event_counter"] = evt_id + 1
                event_history = task.get("event_history", [])
                event_history.append((evt_id, msg))
                # 只保留最近 500 条事件，避免内存爆炸
                if len(event_history) > 500:
                    task["event_history"] = event_history[-500:]
                yield f"id: {evt_id}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg["type"] in ("complete", "error", "cancelled"):
                    break
            except queue.Empty:
                await asyncio.sleep(0.3)
                if task.get("status") in ("completed", "error", "cancelled") and log_queue_obj.empty():
                    break
                yield f": keepalive\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.get("/api/test/{task_id}/status")
async def get_task_status(task_id: str):
    if task_id not in tasks:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    task = tasks[task_id]
    return JSONResponse({"id": task_id, "status": task.get("status"), "report_file": task.get("report_file"), "total_steps": task.get("total_steps"), "issues_count": task.get("issues_count"), "token_usage": task.get("token_usage")})


@app.get("/api/report/{filename}")
async def get_report(filename: str):
    path = REPORT_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "报告不存在"}, status_code=404)
    return JSONResponse({"content": path.read_text(encoding="utf-8")})


@app.get("/api/screenshot/{filename}")
async def get_screenshot(filename: str):
    path = SCREENSHOT_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "截图不存在"}, status_code=404)
    media = "image/jpeg" if filename.endswith(".jpg") else "image/png"
    return FileResponse(str(path), media_type=media)


@app.get("/api/video/{filename}")
async def get_video(filename: str):
    path = VIDEO_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "视频不存在"}, status_code=404)
    return FileResponse(str(path), media_type="video/webm", filename=filename)


@app.get("/api/snapshots")
async def list_snapshots():
    """列出所有可用的快照"""
    snapshots = []
    if SNAPSHOT_DIR.exists():
        for f in sorted(SNAPSHOT_DIR.glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                snapshots.append({
                    "task_id": data.get("task_id", ""),
                    "step": data.get("step", 0),
                    "last_url": data.get("last_url", ""),
                    "timestamp": datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "config": {k: v for k, v in data.get("config", {}).items() if k != "password"},
                })
            except Exception:
                pass
    return JSONResponse({"snapshots": snapshots})


@app.get("/api/specs/default")
async def get_default_spec():
    path = SPECS_DIR / "core-flow.md"
    if path.exists():
        return JSONResponse({"content": path.read_text(encoding="utf-8")})
    return JSONResponse({"content": ""})


@app.post("/api/llm/test")
async def test_llm_connection(request: Request):
    """测试 LLM API 连通性"""
    body = await request.json()
    api_key = body.get("api_key", "").strip()
    base_url = body.get("base_url", "").strip()
    model = body.get("model", "").strip()

    if not api_key:
        return JSONResponse({"success": False, "error": "请填写 API Key"}, status_code=400)
    if not base_url:
        return JSONResponse({"success": False, "error": "请填写 Base URL"}, status_code=400)
    if not model:
        return JSONResponse({"success": False, "error": "请填写模型名称"}, status_code=400)

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "请回复'连接成功'四个字"}],
            max_tokens=20,
            timeout=15
        )
        reply = response.choices[0].message.content.strip() if response.choices else ""
        token_info = ""
        if response.usage:
            token_info = f"（输入 {response.usage.prompt_tokens} + 输出 {response.usage.completion_tokens} tokens）"
        return JSONResponse({
            "success": True,
            "message": f"连接成功！模型回复: {reply} {token_info}",
            "model": model,
            "reply": reply
        })
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg or "invalid_api_key" in error_msg:
            hint = "API Key 无效或已过期，请检查后重试"
        elif "404" in error_msg or "model_not_found" in error_msg:
            hint = f"模型 '{model}' 不存在，请检查模型名称是否正确"
        elif "429" in error_msg or "rate_limit" in error_msg:
            hint = "API 调用频率超限，请稍后重试"
        elif "timeout" in error_msg.lower() or "connect" in error_msg.lower():
            hint = f"无法连接到 {base_url}，请检查 Base URL 是否正确"
        else:
            hint = error_msg
        return JSONResponse({"success": False, "error": hint}, status_code=200)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = BASE_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
