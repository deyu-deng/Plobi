"""「学在浙大」采集器的行为参数 —— 落点 ``$PLOBI_HOME/plobi/zju.json``。

口径来自两处既有规矩，这里只落它们要求的几件：

- **``apply`` 由配置读，不许在信源里写死**（裁定 98 三判据②）。默认 ``"pending"``：
  作业是机器采来的，走 ADR-0009 的待确认 + ``prev_value``，不静默覆盖人改过的条目。
- **拼错的那一格不许静默改回默认档**（判据 7 那颗雷的形状）：按现行口径跑，
  同时把原值留在 ``apply_as_written``，面上说得出「你写的 X 不认识」。
- **礼貌面上限是默认值，不是实测结论**：这台对 zju 的轮询上限**未实测**（治理线只打了
  2 成功 + 3 失败，原话「不许当『可以每 5 分钟一轮』的依据」）。所以默认压到 45 分钟，
  并且**刻意不做成"每轮都登录"**——``min_minutes_between_pulls`` 之外的一轮直接不去碰学校。
  谁实测出上限，谁改这个默认，别拿它当站方承诺。

单飞（同账号同时只许一把在打）不在这个文件里：这一层没有触发方，而 cron 的
``.tick.lock`` 已经是它的单飞（见 ``Docs/`` 与本包 :mod:`.collector` 顶部注释）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

APPLY_PENDING = "pending"
APPLY_CONFIRMED = "confirmed"
VALID_APPLIES = (APPLY_PENDING, APPLY_CONFIRMED)
#: 现行口径：机器采来的作业先进待确认，不直接坐实。
CURRENT_APPLY = APPLY_PENDING

DEFAULT_MIN_MINUTES_BETWEEN_PULLS = 45
DEFAULT_JITTER_MINUTES = 7


def config_path() -> Path:
    override = (os.environ.get("PLOBI_ZJU_CONFIG") or "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / "zju.json"


def state_path() -> Path:
    """节流用的那一份读数（上一轮什么时候真打过），与配置同目录。"""
    return config_path().with_name("zju_state.json")


@dataclass
class ZjuConfig:
    enabled: bool = True
    apply: str = CURRENT_APPLY
    min_minutes_between_pulls: int = DEFAULT_MIN_MINUTES_BETWEEN_PULLS
    jitter_minutes: int = DEFAULT_JITTER_MINUTES
    apply_as_written: str = ""

    def pull_allowed_now(self, last_pull_at: float | None, now: float) -> bool:
        """距离上一轮够不够久（``None`` = 从没打过，当然允许）。

        抖动加在**上界**：窗口 = 最短间隔 + ``[0, jitter_minutes]`` 的一次随机量。
        目的是别让轮询变成一个固定节拍（站方看到的就是一把同步的锤子）；
        它永远不小于 ``min_minutes_between_pulls``，所以"更礼貌"这条不会反过来咬人。
        """
        if last_pull_at is None:
            return True
        import random

        window = self.min_minutes_between_pulls + random.uniform(0, max(self.jitter_minutes, 0))
        return (now - last_pull_at) >= window * 60

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ZjuConfig":
        target = Path(path) if path else config_path()
        data: dict = {}
        if target.exists():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}

        apply_raw = str(data.get("apply") or "").strip().lower() or CURRENT_APPLY
        apply_as_written = "" if apply_raw in VALID_APPLIES else apply_raw
        if apply_raw not in VALID_APPLIES:
            apply = CURRENT_APPLY
        else:
            apply = apply_raw
        if not apply_as_written:
            # 上一次纠正过的值也要留得住：save/load 往返还是一次，面上那句话就没了。
            persisted = str(data.get("apply_as_written") or "").strip().lower()
            if persisted and persisted not in VALID_APPLIES:
                apply_as_written = persisted

        def _minutes(key: str, default: int) -> int:
            try:
                value = int(data.get(key))
            except (TypeError, ValueError):
                return default
            return value if value >= 0 else default

        return cls(
            enabled=bool(data.get("enabled", True)),
            apply=apply,
            apply_as_written=apply_as_written,
            min_minutes_between_pulls=_minutes(
                "min_minutes_between_pulls", DEFAULT_MIN_MINUTES_BETWEEN_PULLS
            ),
            jitter_minutes=_minutes("jitter_minutes", DEFAULT_JITTER_MINUTES),
        )

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path else config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": self.enabled,
            "apply": self.apply,
            "apply_as_written": self.apply_as_written,
            "min_minutes_between_pulls": self.min_minutes_between_pulls,
            "jitter_minutes": self.jitter_minutes,
        }
        tmp = target.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(target)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return target


def read_state(path: Path | str | None = None) -> dict:
    """上一轮的读数（时刻 + 状态 + 条数）；没有记录就 ``{}``。

    面上那句「上次是什么时候采的、什么结果」只能来自这份记录 —— 它不在，就得说"没采过"，
    不许拿"看起来正常"顶上去（判据 2 那颗雷的同一形状）。
    """
    target = Path(path) if path else state_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_last_pull_at(path: Path | str | None = None) -> float | None:
    try:
        return float(read_state(path).get("last_pull_at_epoch"))
    except (TypeError, ValueError):
        return None


def write_state(record: dict, path: Path | str | None = None) -> Path:
    target = Path(path) if path else state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target
