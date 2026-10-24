"""信源的**用户那一层**：开关与首次配置产出的非密钥参数，落在共享层的 `sources.yaml`。

为什么单独一层、而不是把 `enabled` 塞进 `SourceSpec`：那张表是**代码**（`registry.py`
的包自报形状），一个信源自带它的分类、能力位、执行方入口；这一层是**用户按过什么**
（开关翻没翻、课表指到哪个目录、教务的地址填了没）。两者形状不同、归属不同、寿命不同：
代码那一半随包走，用户那一半跟着用户走。

**落点是共享层**（裁定 42 / WP-PROFILE-SHARED-LAYER）：走
`plobi_constants.get_shared_plobi_root() / "plobi" / "sources.yaml"`，与 `projects.yaml`
同一格。**不进分身家**——二十四个分身各存一份开关就是新的漂移源，与 `models.json`
同理。解析不到共享根就**报错**，不静默退回分身家（那正是 `agenda_db_path` 记下的
那次分裂：现核 5 份各读各的空库）。

**这一层绝不落密钥。** `.env` 仍是密钥唯一面（`Docs/README.md` §0 与
`Code/AGENTS.md` 同一条纪律）。所以 `set_setup()` 按**键名**拒收疑似密钥的那一格
（见 :data:`SECRET_LIKE_KEYS`）——不是靠正则扫值，因为值可能是一段 base64，
扫不出来；靠键名才拦得住"有人顺手把 token 填进表单"这一种。

**读数优先级不是裁定，是一个回落链**：这一层**没写**就用 `SourceSpec.collecting`
（代码里的默认），**写了**就以这一层为准。想回到代码默认＝把那一行删掉。
所以 `enabled_origin` 会告诉读的人这一格的值是从哪来的——面上不需要用它，
但排障时"我明明关了怎么还在跑"这句话只有它答得上来。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from plobi.collectors.registry import SECRET_LIKE_KEYS, is_secret_like

logger = logging.getLogger(__name__)

SOURCES_FILENAME = "sources.yaml"

#: 键名里带这些片段的一律拒收。**按名字拦，不按值拦**——值可能是一段 base64，
#: 正则扫不出来；"有人把 token 填进表单"这一种只有按名字拦得住。
#: **真源在 `registry`**（声明层 import 时用同一份先拦一道）；这里只是转发——
#: 两边各留一份就会各自长出一套判定，而"哪一份说了算"是最容易查不出来的那种病。
SECRET_LIKE_KEYS = SECRET_LIKE_KEYS


class SourceConfigError(RuntimeError):
    """这一层读不到或写不进——出声，不静默回落。"""


def sources_path() -> Path:
    """共享层那一份 `sources.yaml`。**解析不到就报错**，不退回分身家。"""
    try:
        from plobi_constants import get_shared_plobi_root

        root = Path(get_shared_plobi_root()) / "plobi"
    except Exception as exc:  # noqa: BLE001 - 共享根读不到就是这一层不可用
        raise SourceConfigError(f"读不到共享家目录，信源配置层不可用：{exc}") from exc
    return root / SOURCES_FILENAME


def _is_secret_like(key: str) -> bool:
    return is_secret_like(key)


def _validate_entry(source_id: str, entry: Any) -> dict[str, Any]:
    """一格只允许两种东西：``enabled`` 布尔，和首次配置产出的标量参数。"""
    if not isinstance(entry, dict):
        raise SourceConfigError(f"{source_id} 那一格不是一张表：{entry!r}")

    for key, value in entry.items():
        if key == "enabled":
            if not isinstance(value, bool):
                raise SourceConfigError(f"{source_id}.enabled 必须是 true/false，读到 {value!r}")
            continue
        if _is_secret_like(key):
            raise SourceConfigError(
                f"{source_id}.{key} 看着像密钥：这个文件不落密钥（`.env` 才是唯一面）。"
                "请把它写进 .env，配置层只留路径 / 地址 / 账号名。"
            )
        if isinstance(value, (dict, list)):
            raise SourceConfigError(
                f"{source_id}.{key} 是嵌套结构：这一层只放标量（路径 / 地址 / 账号名），"
                "嵌套的东西该有自己的落点"
            )
    return dict(entry)


def load() -> dict[str, Any]:
    """读这一层。**没有文件 = 空配置**，那是正常读数，不是错误。

    空配置的意思是"用户一项都没动过"，于是每一条都回落到代码里的默认——不是
    "什么都没配置"（那是两回事，见 `effective_enabled`）。
    """
    path = sources_path()
    if not path.is_file():
        return {}

    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SourceConfigError(f"读不动 {path}：{exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SourceConfigError(f"{path} 顶层不是一张表：{type(raw).__name__}")

    sources = raw.get("sources", {})
    if not isinstance(sources, dict):
        raise SourceConfigError(f"{path} 里 sources 那格不是一张表：{type(sources).__name__}")

    return {str(sid): _validate_entry(str(sid), entry) for sid, entry in sources.items()}


def save(entries: dict[str, dict[str, Any]]) -> Path:
    """整份写回。**先校验再落盘**，且是同目录临时文件 + 原子换名。

    原子换名是必须的：这一份被二十四个分身共享，写到一半崩了等于**所有分身同时
    丢开关**。读-改-写整份覆盖，所以并发两个窗口同时翻开关仍会后写覆盖先写——
    那是单用户产品，不为此上锁（真要上锁得先有一把跨进程的锁，那是另一刀）。
    """
    validated = {sid: _validate_entry(sid, entry) for sid, entry in entries.items()}
    path = sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    import yaml

    body = yaml.safe_dump(
        {"sources": {sid: validated[sid] for sid in sorted(validated)}},
        allow_unicode=True,
        sort_keys=True,
    )

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{SOURCES_FILENAME}.", suffix=".tmp", delete=False,
    )
    try:
        with handle:
            handle.write(body)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise

    return path


def entry(source_id: str) -> Optional[dict[str, Any]]:
    """用户给这一条按过什么。**`None` = 一项都没按过** ⇒ 回落代码默认。"""
    return load().get(source_id)


def effective_enabled(source_id: str) -> tuple[bool, str]:
    """这一条现在到底采不采。返回 ``(值, 这个值是从哪来的)``。

    回落链（**不是裁定，是一个链条**）：这一层没写 ⇒ `SourceSpec.collecting`；
    写了 ⇒ 以这一层为准。想回到代码默认 = 把那一行删掉，不用改代码。
    """
    from plobi.collectors import registry

    override = (entry(source_id) or {}).get("enabled")

    if override is not None:
        return bool(override), "config"
    return bool(registry.spec(source_id).collecting), "spec"


def set_enabled(source_id: str, enabled: bool) -> bool:
    """翻开关。**只动这一条**，其余原样保留（整份读出来改一格再写回）。

    先验后转：`bool("yes")` 是 `True`——不先卡住类型，那一格就把 `"no"`、`0`、
    `None` 一起收成 `True`，而用户明明写了「否」。校验那一半住在 `_validate_entry`，
    所以这里只把原始值塞进去让它自己说话。
    """
    entries = load()
    row = dict(entries.get(source_id) or {})
    row["enabled"] = enabled
    entries[source_id] = row
    save(entries)
    return bool(enabled)


def setup_params(source_id: str) -> dict[str, Any]:
    """首次配置产出的**非密钥**参数（路径 / 地址 / 账号名）。没有就是空。

    密钥那一档**天生不在这里**：它压根不写进 `sources.yaml`（见 :func:`set_setup_params`
    那一段），所以这一只读口不需要"记得过滤"——它读的那个文件里就没有。
    """
    row = entry(source_id) or {}
    return {k: v for k, v in row.items() if k != "enabled"}


def secret_keys(source_id: str) -> tuple[str, ...]:
    """这一条声明成 `kind="secret"` 的那几格（值走 `.env`，不回显）。"""
    from plobi.collectors import registry

    return tuple(f.key for f in registry.spec(source_id).setup_fields if f.kind == "secret")


def secret_env_name(source_id: str, key: str) -> str:
    """`.env` 里那一格叫什么。**一个函数**，不各处拼：拼错一次就是"存进去了但没人读得到"。"""
    return f"PLOBI_SOURCE_{source_id}_{key}".upper()


def secret_configured(source_id: str) -> dict[str, bool]:
    """每一格密钥**设过没有**——只回布尔。payload 里出现的就是这一格，值永不出门。"""
    from plobi_cli.config import load_env

    env = load_env()

    return {key: bool(str(env.get(secret_env_name(source_id, key)) or "").strip())
            for key in secret_keys(source_id)}


def secret_value(source_id: str, key: str) -> Optional[str]:
    """那一格密钥**现在的值**（`None` = 没设）。采集器运行时唯一的读口。

    为什么必须有这一格而不是让各家自己 `os.environ.get(...)`：环境变量只是**落点**，
    键名规则（:func:`secret_env_name`）是这一层的。各家自己去摸一次环境，就有人会把
    `PLOBI_SOURCE_…` 拼错成"设了却读不到"，也会在别处（日志、异常信息、`--dry-run` 的
    回显）顺手打印那个值。**一处读口 = 一处能负责守住"它不进日志"的地方**。

    这一格**永不许出现在 `/api/collect/sources` 的读数里**——那一条由
    :func:`secret_configured` 与 `sources_api._enrich` 共同钉住（那里只回"设过没有"）。
    """
    from plobi_cli.config import load_env

    value = str(load_env().get(secret_env_name(source_id, key)) or "").strip()

    return value or None


def set_secret(source_id: str, key: str, value: str) -> None:
    """写一格密钥进 `.env`（唯一那面密钥的墙）。空串按"清掉"处理。"""
    from plobi_cli.config import save_env_value_secure

    save_env_value_secure(secret_env_name(source_id, key), value or "")


def declared_setup_keys(source_id: str) -> tuple[str, ...]:
    """这条信源**自己声明**要填哪几格（`SourceSpec.setup_fields`）。没声明就是空。

    读声明而不是维护一张表：那张表一变就得改 `apps/desktop/**`，而 spec §8 有一条
    验收就是「加一条新信源不需要改 `apps/desktop/**`」。
    """
    from plobi.collectors import registry

    return tuple(field.key for field in registry.spec(source_id).setup_fields)


def set_setup_params(source_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """写首次配置参数。**不动 `enabled`** ——那格有它自己的动词。

    **按声明校验**：不在 ``setup_fields`` 里的键拒收，声明里 ``required`` 的缺了拒收。
    不是洁癖——表单是照那份声明画出来的，一个不被声明承认的键写进去，下一次读取
    就成了没人认领的一行，而"这一格是谁配的"从此问不到。

    落盘成功**就算配好了**，不替任何一方去"验证能不能连上"：能不能连上是执行方
    自己的读数（`sweep` 那一轮会报它自己的状态），这一层只管存住用户填的东西。
    """
    declared = declared_setup_keys(source_id)
    secrets = set(secret_keys(source_id))
    # **密钥那一道先跑**，不是为了排版：用户把 token 填进表单时，"这个文件不收密钥"
    # 才是他需要听见的那句，"这个键没声明" 只会让他以为是自己拼错了字段名。
    # 而且这一道是更强的信号——它该赢。
    # 10-22 改过一次形状：拦的从"长得像密钥"改成"**没声明成 `kind='secret'` 却长得像密钥**"。
    # 声明过的走 `.env` 那一面墙（:func:`set_secret`），这个文件仍然一个字都不收——
    # 拦的对象没变，变的是"声明过的密钥有一条正路可走"，不用再拿"填 .env"当唯一出口。
    unclaimed = [key for key in params if _is_secret_like(key) and key not in secrets]
    if unclaimed:
        raise SourceConfigError(
            f"{source_id}.{unclaimed[0]} 看着像密钥，但这条信源没把它声明成 `kind='secret'`："
            "这个文件不落密钥（`.env` 才是唯一面）。要么在 `SPEC` 里声明它，要么别收这一格。"
        )
    unknown = [key for key in params if key not in declared]
    if unknown:
        raise SourceConfigError(
            f"{source_id} 没声明这几格：{'、'.join(unknown)}。"
            f"它声明的是：{'、'.join(declared) or '（空——首次配置不需要填任何非密钥的东西）'}"
        )

    for key in [k for k in params if k in secrets]:
        value = str(params.pop(key) or "").strip()
        if value:
            set_secret(source_id, key, value)
        # 空串**不覆盖**已经设过的那一格：界面上那个掩码框是"留空＝不改"，
        # 不是"留空＝清空"——清空要有显式的动作，否则一次没填就把凭据弄没了。

    missing = [key for key in declared
               if registry_required(source_id, key) and not str(params.get(key) or "").strip()
               and not (key in secrets and secret_configured(source_id).get(key))]
    if missing:
        raise SourceConfigError(f"{source_id} 首次配置还缺：{'、'.join(missing)}")

    entries = load()
    row = dict(entries.get(source_id) or {})
    row.update(params)
    entries[source_id] = {k: v for k, v in row.items() if k == "enabled" or v is not None}
    save(entries)

    return setup_params(source_id)


def registry_required(source_id: str, key: str) -> bool:
    """这一格声明为必填吗。单独一个出口是为了让上面那个「缺了」检查读起来像一句话。"""
    from plobi.collectors import registry

    return any(f.key == key and f.required for f in registry.spec(source_id).setup_fields)