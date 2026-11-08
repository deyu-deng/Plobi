"""从本机微信进程里取数据密钥——**只读**：不写进程内存、不注入、不 attach 调试器、不杀进程。

为什么我们自己写，而不用上游那条 `chatlog key`：

- 上游那条子命令走的是 3.x／4.0 时代那套内存特征。微信从 **4.0.3.36** 起不再长时间
  把密钥留在内存里，所以它在新版微信上取不到（上游 FAQ #197）。
- 上游仓库本身已归档停更，我们 vendor 的那份是 2026-08-30 的快照 ⇒ 它不会自己修好。

我们自己这条链，实测在**微信 4.1.15.56** 上取得到（11-08 21:2x 真机：取到 → 写 handoff →
sidecar 起 → 枚举到 189 条真实会话）。三步：

1. **锚点自寻址**：在 ``Weixin.dll`` 里**现找** ``MMV1$`` 魔数，取它的 RVA。
   为什么不写死：十月那版取钥器把 RVA 钉在 ``0x9492618``（微信 4.1.15.13 的 .rdata），
   微信一更新同一个常量就漂（4.1.15.56 上跑到 ``0x96789f8``）⇒ 取钥器卡在自检失败。
   现找 ⇒ 微信再更新也不用重铸这一格。
2. **构建常量**：从 DLL 的 .text 段静态提取（四段 ``mov rdx, imm64`` ＋ ``test rax,rax``）。
   微信 4.1.x 把内存里那把 key 与这个常量异或过；**不异或就永远校验不过**（实测：
   异或前 96 个候选全败，异或后第 26 次命中）。
3. **只读扫描 + 语义校验**：在进程私有内存里找特征块，取它前面那个指针指向的 32 字节，
   与构建常量异或，再用**真实库首屏**做 SQLCipher 4 的 PBKDF2／HMAC 校验
   （256000 轮 SHA-512；这一段与 vendor ``decrypt/common/common.go`` 的
   ``ValidateKey`` 逐字同式——口径对齐才能拿同一把尺子）。

四条硬规矩（与 :mod:`.keyhand` 同一把）：

1. **值永不出门**：不进 stdout、不进日志、不进异常文本，也不进本函数的返回值——
   外面只拿得到「取到没取到 + 那一句人话」。落盘只落引擎那一档配置（``0600``）。
2. **取不到就说是为什么**（微信没在跑／读不到它的库／微信版本读不出／扫描没命中），
   不许退化成「那你去填一格」。
3. **只在被那一次动作带着走的时候做**：他点开微信的开关那一刻（``sidecar_prepare``）。
   读数、列表、状态检查里都不碰——列一屏信源不该顺手去读任何进程。
4. **不假装**：本机没这能力时就说没这能力，不静默降级。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["fetch", "extract", "wechat_version", "write_handoff", "set_key",
           "validate_candidate", "locate_anchor_rva", "xor_constants",
           "scan_candidates", "SCAN_NOT_SUPPORTED"]

# ---- 内存特征：与 vendor `internal/wechat/key/windows/v4_windows.go` 那一段同形 ----
# 8 个 0x00 + 0x20 + 7×0x00 + 0x2F + 7×0x00；**它前面 8 字节**是指针，指向那把 key。
KEY_PATTERN = b"\x00" * 8 + b"\x20" + b"\x00" * 7 + b"\x2f" + b"\x00" * 7
KEY_LEN = 32

# ---- SQLCipher 4 的四档（与 vendor `decrypt/windows/v4.go` 逐字同式）----
PAGE_SIZE = 4096
SALT_LEN = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE = 80               # IVSize(16) + HmacSize(64)，16 对齐后不变
PBKDF2_ROUNDS = 256000

# ---- DLL 段解析 ----
MMV1_MAGIC = b"MMV1"
#: 社区那条链的特征：四段 ``mov rdx, imm64`` ＋ 一句 ``test rax,rax``。
#: 总长 = 4×(3+8) + 3×gap + 3，gap ∈ [3,8] ⇒ 56..71 字节。
_XOR_PATTERN = re.compile(
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\x85\xC0",
    re.DOTALL,
)

WECHAT_EXE = "weixin.exe"
WECHAT_DLL = "weixin.dll"

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_NOACCESS = 0x01

#: 单块读取上限：微信进程里有些私有区几百 MB，逐块读、不留副本。
MAX_BLOCK = 16 * 1024 * 1024
#: 整块跳过上限：再大的区不是放钥匙的地方（堆碎片多），读它只是烧时间。
MAX_REGION = 256 * 1024 * 1024

SCAN_NOT_SUPPORTED = "not-supported"


class _MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


@dataclass(frozen=True)
class _Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int


def _kernel32():
    if os.name != "nt":
        return None
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    lib.OpenProcess.restype = wt.HANDLE
    lib.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    lib.ReadProcessMemory.restype = wt.BOOL
    lib.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                   ctypes.POINTER(_MemoryBasicInformation), ctypes.c_size_t]
    lib.VirtualQueryEx.restype = ctypes.c_size_t
    lib.CloseHandle.argtypes = [wt.HANDLE]
    lib.CloseHandle.restype = wt.BOOL
    return lib


_K32 = _kernel32()


# --------------------------------------------------------------------------- #
# 内存那一侧（只读）
# --------------------------------------------------------------------------- #

def _open(pid: int):
    if _K32 is None:
        return None
    return _K32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)


def _read(handle, address: int, size: int) -> bytes:
    if size <= 0:
        return b""
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not _K32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size, ctypes.byref(got)):
        return b""
    return buf.raw[: got.value]


def _regions(handle) -> list[tuple[int, int]]:
    """(基址, 长度)——只列**可读**的私有区（堆/栈）。

    为什么只扫私有区：密钥与 salt 的副本必然在堆上；把映像区（dll）整片读进来
    只是烧时间（微信那个 .dll 200MB）。
    """
    out: list[tuple[int, int]] = []
    address = 0
    info = _MemoryBasicInformation()
    while True:
        if not _K32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(info),
                                   ctypes.sizeof(info)):
            break
        base = info.BaseAddress or 0
        size = info.RegionSize
        if size == 0:
            break
        if (info.State == MEM_COMMIT and info.Type == MEM_PRIVATE
                and (info.Protect & 0xFF) != PAGE_NOACCESS and size <= MAX_REGION):
            out.append((base, size))
        address = base + size
        if address <= 0:
            break
    return out


def _looks_random(candidate: bytes) -> bool:
    """熵预筛：真密钥是密码学随机，普通文本不是。

    省掉的是 PBKDF2 的 256000 轮——每个候选都要跑一遍，没有预筛就是几个小时的纯算力。
    """
    if len(candidate) != KEY_LEN:
        return False
    if len(set(candidate)) < 15:
        return False
    if sum(32 <= b <= 126 for b in candidate) > 24:
        return False
    return True


def scan_candidates(pid: int) -> list[bytes]:
    """只读扫一遍进程私有内存，取出「特征块前面那个指针」指向的 32 字节候选。"""
    if _K32 is None:
        return []
    handle = _open(pid)
    if not handle:
        return []
    found: list[bytes] = []
    seen: set[bytes] = set()
    try:
        for base, size in _regions(handle):
            offset = 0
            while offset < size:
                chunk = min(MAX_BLOCK, size - offset)
                data = _read(handle, base + offset, chunk)
                if not data:
                    break
                index = data.find(KEY_PATTERN)
                while index != -1:
                    if index >= 8:
                        pointer = struct.unpack_from("<Q", data, index - 8)[0]
                        if 0x10000 < pointer < 0x7FFFFFFFFFFF:
                            candidate = _read(handle, pointer, KEY_LEN)
                            if len(candidate) == KEY_LEN and _looks_random(candidate):
                                if candidate not in seen:
                                    seen.add(candidate)
                                    found.append(candidate)
                    index = data.find(KEY_PATTERN, index + 1)
                offset += chunk
    finally:
        _K32.CloseHandle(handle)
    return found


# --------------------------------------------------------------------------- #
# DLL 那一侧（静态）
# --------------------------------------------------------------------------- #

def _sections(dll_path: str) -> list[_Section]:
    with open(dll_path, "rb") as handle:
        head = handle.read(0x1000)
        if len(head) < 0x1000:
            return []
        pe_offset = struct.unpack_from("<I", head, 0x3C)[0]
        if head[pe_offset:pe_offset + 4] != b"PE\x00\x00":
            return []
        coff = pe_offset + 4
        count = struct.unpack_from("<H", head, coff + 2)[0]
        optional_size = struct.unpack_from("<H", head, coff + 16)[0]
        table = coff + 20 + optional_size
        out: list[_Section] = []
        for index in range(count):
            base = table + index * 40
            if base + 40 > len(head):
                break
            name = head[base:base + 8].rstrip(b"\x00").decode("ascii", "replace")
            out.append(_Section(
                name=name,
                virtual_address=struct.unpack_from("<I", head, base + 12)[0],
                virtual_size=struct.unpack_from("<I", head, base + 8)[0],
                raw_offset=struct.unpack_from("<I", head, base + 20)[0],
                raw_size=struct.unpack_from("<I", head, base + 16)[0],
            ))
        return out


def _offset_to_rva(sections: list[_Section], file_offset: int) -> Optional[int]:
    for section in sections:
        if section.raw_offset <= file_offset < section.raw_offset + section.raw_size:
            return section.virtual_address + (file_offset - section.raw_offset)
    return None


def locate_anchor_rva(dll_path: str) -> Optional[int]:
    """在 DLL 里现找 ``MMV1$`` 魔数，返回它的 RVA（优先 .rdata 那一份）。

    **不写死**是这一格的全部意义：写死的那份随微信更新漂一次就废一次
    （10 月那版钉在 4.1.15.13 的 ``0x9492618``，4.1.15.56 上同一个常量在 ``0x96789f8``）。
    """
    try:
        sections = _sections(dll_path)
        if not sections:
            return None
        fallback = None
        with open(dll_path, "rb") as handle:
            for section in sections:
                if section.name not in (".rdata", ".text"):
                    continue
                handle.seek(section.raw_offset)
                data = handle.read(section.raw_size)
                index = data.find(MMV1_MAGIC)
                while index != -1:
                    rva = _offset_to_rva(sections, section.raw_offset + index)
                    if rva is not None:
                        if section.name == ".rdata":
                            return rva
                        if fallback is None:
                            fallback = rva
                    index = data.find(MMV1_MAGIC, index + 1)
        return fallback
    except OSError:
        return None


def xor_constants(dll_path: str) -> list[bytes]:
    """从 DLL 的 .text 段静态提取构建常量（微信 4.1.x 用它把内存里那把 key 异或过）。

    这是**构建常量**，不是任何人的密钥——它跟着微信的版本走，不跟着用户走。
    """
    try:
        sections = _sections(dll_path)
    except OSError:
        return []
    text = next((s for s in sections if s.name == ".text"), None)
    if text is None:
        return []
    try:
        with open(dll_path, "rb") as handle:
            handle.seek(text.raw_offset)
            data = handle.read(text.raw_size)
    except OSError:
        return []
    out: list[bytes] = []
    seen: set[bytes] = set()
    for match in _XOR_PATTERN.finditer(data):
        blob = match.group(1) + match.group(2) + match.group(3) + match.group(4)
        if blob not in seen:
            seen.add(blob)
            out.append(blob)
    return out


# --------------------------------------------------------------------------- #
# 校验（SQLCipher 4，与 vendor 逐字同式）
# --------------------------------------------------------------------------- #

def _final_check(mac_key: bytes, page1: bytes) -> bool:
    data_end = PAGE_SIZE - RESERVE + IV_SIZE
    mac = hmac.new(mac_key, page1[SALT_LEN:data_end], hashlib.sha512)
    mac.update(struct.pack("<I", 1))
    return hmac.compare_digest(mac.digest(), page1[data_end:data_end + HMAC_SIZE])


def validate_candidate(candidate: bytes, page1: bytes) -> bool:
    """候选能不能解开那把库——两种形态都试。

    - **password**：原密钥，PBKDF2 256000 轮（微信 4.x 常态）
    - **enc_key**：已是派生结果，2 轮就够（缓存形态）
    """
    if len(candidate) != KEY_LEN or len(page1) < PAGE_SIZE:
        return False
    salt = page1[:SALT_LEN]
    mac_salt = bytes(byte ^ 0x3A for byte in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", candidate, mac_salt, 2, KEY_LEN)
    if _final_check(mac_key, page1):
        return True
    enc_key = hashlib.pbkdf2_hmac("sha512", candidate, salt, PBKDF2_ROUNDS, KEY_LEN)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, KEY_LEN)
    return _final_check(mac_key, page1)


# --------------------------------------------------------------------------- #
# 微信那一侧（进程与目录）
# --------------------------------------------------------------------------- #

def _wechat_process() -> Optional[tuple[int, str]]:
    """(pid, exe 路径)——取内存占用最大的那个 Weixin.exe。

    为什么是「最大的那个」：微信 4.x 会起好几个同名进程（``--type=wxutility`` 那些
    是工具进程），主进程才是握着库的那个。社区那条链用的是同一个选法。
    """
    try:
        import psutil
    except ImportError:
        return None
    best: Optional[tuple[int, str, int]] = None
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (process.info.get("name") or "").lower()
            if name not in (WECHAT_EXE, "wechat.exe"):
                continue
            memory = process.memory_info().rss
            exe = process.info.get("exe") or ""
            if best is None or memory > best[2]:
                best = (process.pid, exe, memory)
        except Exception:
            continue
    if best is None:
        return None
    return best[0], best[1]


def _wechat_dll_path(pid: int) -> Optional[str]:
    """这个进程加载的 ``Weixin.dll`` 在哪——从模块快照里问它自己。"""
    try:
        import psutil
    except ImportError:
        return None
    try:
        process = psutil.Process(pid)
        for module in process.memory_maps(grouped=False):
            path = getattr(module, "path", "") or ""
            if path.lower().endswith(WECHAT_DLL):
                return path
    except Exception:
        return None
    return None


def _open_databases(pid: int) -> list[str]:
    """这个进程**正在打开**的库文件——数据目录就藏在这条路径里。

    不猜路径：微信的数据目录可以被用户改到任何盘（这台就在 ``D:\\Data\\Local\\Weixin``）。
    """
    try:
        import psutil
    except ImportError:
        return []
    out: list[str] = []
    try:
        process = psutil.Process(pid)
        for opened in process.open_files():
            path = opened.path
            if path.lower().endswith(".db") and "db_storage" in path.lower():
                out.append(path)
    except Exception:
        return []
    return out


def _account_dir(db_path: str) -> str:
    """``...\\<账号>\\db_storage\\message\\message_0.db`` ⇒ 账号目录（去掉最后三段）。

    与 vendor ``internal/wechat/process/windows/detector_windows.go`` 那一刀同式：
    它把 ``data_dir`` 定在账号目录上，引擎照那个找库。
    """
    parts = Path(db_path).parts
    if len(parts) <= 3:
        return str(Path(db_path).parent)
    return str(Path(*parts[:-3]))


def wechat_version() -> str:
    """本机微信的完整版本号（读 Weixin.exe 的 FileVersion）；读不到就空串。"""
    try:
        import psutil
    except ImportError:
        return ""
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if (process.info.get("name") or "").lower() != WECHAT_EXE:
                continue
            exe = process.info.get("exe") or ""
            if not exe:
                continue
            return _file_version(exe)
        except Exception:
            continue
    return ""


def _file_version(exe_path: str) -> str:
    """读 PE 头里的版本号——不引第三方库（产品依赖表是钉死的精确版本）。"""
    class _FixedFileInfo(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wt.DWORD),
            ("dwStrucVersion", wt.DWORD),
            ("dwFileVersionMS", wt.DWORD),
            ("dwFileVersionLS", wt.DWORD),
            ("dwProductVersionMS", wt.DWORD),
            ("dwProductVersionLS", wt.DWORD),
            ("dwFileFlagsMask", wt.DWORD),
            ("dwFileFlags", wt.DWORD),
            ("dwFileOS", wt.DWORD),
            ("dwFileType", wt.DWORD),
            ("dwFileSubtype", wt.DWORD),
            ("dwFileDateMS", wt.DWORD),
            ("dwFileDateLS", wt.DWORD),
        ]

    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
    version.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.UINT)]
    try:
        handle = wt.DWORD(0)
        size = version.GetFileVersionInfoSizeW(exe_path, ctypes.byref(handle))
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(exe_path, 0, size, buffer):
            return ""
        pointer = ctypes.c_void_p()
        length = wt.UINT(0)
        if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return ""
        fixed = ctypes.cast(pointer, ctypes.POINTER(_FixedFileInfo)).contents
        ms = fixed.dwFileVersionMS
        ls = fixed.dwFileVersionLS
        return "%d.%d.%d.%d" % ((ms >> 16) & 0xFFFF, ms & 0xFFFF, (ls >> 16) & 0xFFFF, ls & 0xFFFF)
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# 对外：取一次
# --------------------------------------------------------------------------- #

def routed_away() -> str:
    """本机这版微信是不是**不归这条路取**（3.x）——是就回版本号，不是就回空串。

    为什么要有它：3.x 的密钥由上游那条（``chatlog key``）取。两条路都失败时，
    屏幕上该说的是**上游那句真话**（那一版微信上游管用），不是我们那句
    「由另一条路取」——那句话在两路都败之后就成了空话。
    """
    found = _wechat_process()
    if not found:
        return ""
    _pid, exe = found
    version = _file_version(exe) if exe else ""
    major = int(version.split(".")[0]) if version and version.split(".")[0].isdigit() else 0
    if major and major < 4:
        return version
    return ""


def extract(db_path: str = "") -> tuple[Optional[bytes], str, dict]:
    """取一次密钥。回 ``(密钥, 那一句人话, 额外读数)``。

    **密钥值只在返回值里**（调用方负责落到引擎那一档配置），本模块不写盘、不打屏。
    额外读数里有 ``data_dir``／``version``／``platform``／``type``——引擎起服务要这几档，
    缺了它就只会在 5030 上说「unsupported platform」而不说真话。

    ``db_path`` 留空时自己认（从微信进程**正在打开**的库文件里找）；给了就用那个
    （调试时用）。
    """
    if os.name != "nt":
        return None, "取密钥这条路只在这台 Windows 上有。", {}

    found = _wechat_process()
    if not found:
        return None, "本机没在跑微信——先把微信打开并登录，它开着才取得到。", {}
    pid, exe = found

    # **版本分流**：这一条只服务微信 4.x（库布局是 `db_storage`，密钥是 SQLCipher 4 那一套）。
    # 3.x 走的是另一条路（上游 `chatlog key` 那一发），我们这条路对它是**注定对不上**的——
    # 不先分流，它会拿「微信没在读本机的聊天库」这种话去回敬一个其实好好在跑微信的 3.x 用户。
    version = _file_version(exe) if exe else ""
    major = int(version.split(".")[0]) if version and version.split(".")[0].isdigit() else 0
    if major and major < 4:
        return None, "这一版微信（%s）的密钥由另一条路取。" % version, {}

    if db_path:
        chosen = db_path
        if not os.path.exists(chosen):
            return None, "指定的聊天库读不到（路径不存在）。", {}
    else:
        databases = _open_databases(pid)
        if not databases:
            return None, "微信开着，但它没在读本机的聊天库（没登录，或库还没打开）。", {}
        preferred = [p for p in databases if p.lower().endswith("message_0.db")]
        chosen = (preferred or databases)[0]
    try:
        with open(chosen, "rb") as handle:
            page1 = handle.read(PAGE_SIZE)
    except OSError:
        return None, "读不到本机微信的聊天库（权限或路径变了）。", {}
    if len(page1) < PAGE_SIZE:
        return None, "本机微信的聊天库还没成形，稍后再试。", {}

    dll_path = _wechat_dll_path(pid)
    if not dll_path:
        return None, "认不出本机微信的核心模块（版本变了或进程刚起）。", {}

    constants = xor_constants(dll_path)
    candidates = scan_candidates(pid)
    if not candidates:
        return None, "扫了一遍微信的内存，没有找到那把密钥的位置（微信版本可能又变了）。", {}

    for constant in constants:
        for candidate in candidates:
            mixed = bytes(a ^ b for a, b in zip(candidate, constant))
            if validate_candidate(mixed, page1):
                extra = {
                    "data_dir": _account_dir(chosen),
                    "version": 4,
                    "platform": "windows",
                    "type": "WeChat",
                    "full_version": wechat_version(),
                }
                return mixed, "已经替你取到密钥。", extra

    suffix = ("（你这台微信是 %s）" % version) if version else ""
    return None, ("这一版微信的密钥没取到%s：微信把密钥换了存放方式，"
                  "产品这边暂时跟不上（这是微信侧的改动，不是你哪里没配好）。" % suffix), {}


def write_handoff(directory: Path, key: bytes, extra: dict) -> None:
    """并进引擎那一档配置（已有别的键就留着），写完收到只有本用户可读。"""
    target = directory / "chatlog.json"
    merged: dict = {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            merged = loaded
    except (OSError, ValueError):
        pass
    merged.update({k: v for k, v in extra.items() if v})
    merged["data_key"] = key.hex()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        logger.debug("chatlog keyscan handoff chmod skipped: %s", target)


def set_key(directory: Path, key_hex: str) -> None:
    """把**用户手上已有的那把 key**写进同一处配置（不必再取一次）。

    上游 FAQ 里的正规用法（Docker 部署需要提前备好密钥）——**这一条不许退化成
    「你把密钥抄到屏幕上」**：值只落引擎那一档配置（``0600``），不进日志、不进返回值。
    """
    cleaned = "".join(key_hex.split()).strip().lower()
    if len(cleaned) != KEY_LEN * 2:
        raise ValueError("密钥必须是 %d 个十六进制字符" % (KEY_LEN * 2))
    try:
        key = bytes.fromhex(cleaned)
    except ValueError:
        raise ValueError("密钥不是合法的十六进制") from None
    write_handoff(directory, key, {})


def fetch(env: Optional[dict] = None, *, timeout: float = 120.0) -> tuple[bool, str]:
    """替他取一次数据密钥。回 ``(取到没有, 那一句人话)``——**那句话里永远没有密钥**。

    这是 ``sidecar_prepare`` 那一格用的；读数与列表都不碰它。
    """
    from .sidecar import data_key_present, engine_config_dir

    env = os.environ if env is None else env
    if data_key_present(env):
        return True, "解密密钥本机已经有了，这次没去动微信。"

    started = time.time()
    try:
        key, note, extra = extract()
    except Exception as exc:                       # 读不出来要说成读不出来，不许炸掉那次点击
        logger.debug("chatlog keyscan failed: %s", type(exc).__name__)
        return False, "取密钥这一步没走通（%s）。" % type(exc).__name__

    if key is None:
        return False, note

    if time.time() - started > timeout:
        return False, "取密钥花了太久，这一趟先放着，稍后再点一次开关。"

    try:
        write_handoff(engine_config_dir(env), key, extra)
    except OSError as exc:
        return False, "密钥取到了，但写不进本机那一份配置：%s。" % type(exc).__name__
    return True, note
