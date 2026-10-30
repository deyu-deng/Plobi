"""按需取消正在跑的一趟 cron（``plobi cron cancel`` / 桌面上的「停止这一趟」）。

**为什么是文件而不是内存里的 Event**：这一趟真正在跑的线程，和接到"停"这个请求的那一端
不一定在同一个进程（ticker 可能在网关里，桌面是走 HTTP 过来的）。所以请求落成一个**标记
文件**，由运行路径自己去读。

**为什么按 job id + 请求时刻比**：残留的标记不能误杀下一趟。写入时记下 epoch，运行开始时
记下自己的 epoch，只有"这一趟开始之后才写下的"标记才算数；不匹配的旧标记当场清掉。

上游用"每次执行一个 id"来做这件事（它有执行账本）；我们没有执行账本，因此用**开始时刻**
做同一件事，效果一样而不用背一本新账。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

#: 一趟运行被 SIGKILL 之后留下的标记没人清；取消本来就罕见，顺手在写入时按 TTL 清一次。
_MARKER_TTL_SECONDS = 24 * 3600

_DEFAULT_REASON = "cancelled on request"


def _cancels_dir() -> Path:
    """每次现解析：同一个进程可能给多个 profile 干活。"""
    from plobi_constants import get_plobi_home

    return Path(get_plobi_home()).resolve() / "cron" / "cancels"


def _marker_path(job_id: str) -> Optional[Path]:
    """job id 会变成文件名 ⇒ 先校验字符集，不认识的就**不猜**。"""
    if not job_id:
        return None
    if any(not (c.isalnum() or c in "-_") for c in job_id):
        return None
    return _cancels_dir() / f"{job_id}.json"


def request_cancel(job_id: str, reason: str = _DEFAULT_REASON) -> bool:
    """记下"停这一趟"的请求。返回是否写成功（写不了不冒充成功）。"""
    path = _marker_path(job_id)
    if path is None:
        return False
    prune()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"job_id": job_id, "requested_at": time.time(), "reason": reason or _DEFAULT_REASON}
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent), delete=False, prefix=".cancel-", suffix=".tmp",
        )
        with handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(handle.name, path)
        return True
    except OSError:
        return False


def read_cancel(job_id: str) -> Optional[dict[str, Any]]:
    """读标记；坏文件当成没有（读不懂不等于"这一趟要被停"）。"""
    path = _marker_path(job_id)
    if path is None:
        return None
    try:
        with path.open(encoding="utf-8-sig") as handle:  # 有人会用 BOM 写文件
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def cancel_requested(job_id: str, *, since: Optional[float] = None) -> bool:
    """这一趟要不要停。``since`` 是这趟的开始时刻——比它旧的标记不算数。"""
    payload = read_cancel(job_id)
    if not payload:
        return False
    if since is None:
        return bool(payload.get("requested_at"))
    try:
        return float(payload.get("requested_at") or 0.0) >= float(since)
    except (TypeError, ValueError):
        return False


def pop_cancel(job_id: str, *, since: Optional[float] = None) -> Optional[dict[str, Any]]:
    """读并**收走**标记（一趟只认一次）；不是这一趟的就先留着，交给它自己的那一趟。"""
    payload = read_cancel(job_id)
    if not payload:
        return None
    if since is not None:
        try:
            if float(payload.get("requested_at") or 0.0) < float(since):
                return None
        except (TypeError, ValueError):
            return None
    clear(job_id)
    return payload


def clear(job_id: str) -> None:
    path = _marker_path(job_id)
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def prune(now: Optional[float] = None) -> None:
    """清掉过期的标记（进程被杀后留下的那种）。失败不拦调用方。"""
    directory = _cancels_dir()
    if not directory.is_dir():
        return
    cutoff = (now or time.time()) - _MARKER_TTL_SECONDS
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.suffix != ".json":
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue
