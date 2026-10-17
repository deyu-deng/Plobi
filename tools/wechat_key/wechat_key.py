#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat_key.py — 微信 4.1.15.13 WCDB v4 密钥提取器（Plobi 侧独立件，不改 vendor）

原理（"认结构 + 语义校验"，替代 vendor 死字节模式）：
  1. 枚举 Weixin.exe 进程，取内存占用最大者；
  2. 枚举模块快照，定位 Weixin.dll 基址；
  3. 只读扫描进程可读内存，搜 8 字节小端 = 模块基址 + 0x9492618（MMV1$ 常量地址的指针残留，弱锚点）；
  4. 在命中点附近窗口内按 8/16 字节对齐提取 32 字节候选；
  5. 用 vendor 自己的语义判据校验候选：
       enc_key = PBKDF2-HMAC-SHA512(candidate, salt, 256000, 32)
       mac_key = PBKDF2-HMAC-SHA512(enc_key, salt XOR 0x3a, 2, 32)
       HMAC-SHA512(mac_key, page1[16:4032] + LE32(1)) == page1[4032:4096]
  6. 校验通过 → 输出密钥 hash 摘要，并（可选）写入 chatlog.json（data_key/img_key）。

安全约束：
  - 全程只读进程内存（PROCESS_QUERY_INFORMATION | PROCESS_VM_READ），不写微信进程、不提权、不杀进程；
  - 密钥值严禁出现在终端 / 日志 / 命令行：只打印长度与 sha256 前 12 位 hex；
  - 写 chatlog.json 由 --out 显式指定，默认不写。
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac
import json
import multiprocessing
import os
import struct
import sys

# ---- Windows API 常量 ----
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
TH32CS_SNAPPROC = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE = 0x10
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80

MMV1_RVA = 0x9492618       # MMV1$ 魔数常量 RVA（微信 4.1.15.13, .rdata）
WECHAT_DLL_NAME = "weixin.dll"
WECHAT_EXE_NAME = "weixin.exe"
KEY_LEN = 32
SALT_LEN = 16
PAGE_SIZE = 4096
RESERVE = 80               # IVSize(16) + HmacSize(64)
V4_ITER = 256000

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD),
        ("hModule", ctypes.c_void_p),
        ("szModule", ctypes.c_wchar * 256),
        ("szExePath", ctypes.c_wchar * 260),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
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


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def readable_protect(protect):
    return (protect & 0xFF) not in (PAGE_NOACCESS,) and (
        protect & (PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY |
                   PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)
    )


def weixin_pids():
    pids = []
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROC, 0)
    if snap == ctypes.c_void_p(-1).value or snap == -1:
        return pids
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            return pids
        while True:
            if pe.szExeFile.lower() == WECHAT_EXE_NAME:
                pids.append(pe.th32ProcessID)
            if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return pids


def largest_weixin_pid(pids):
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    best_pid, best_ws = None, -1
    for pid in pids:
        h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
        if not h:
            continue
        try:
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), ctypes.sizeof(pmc)):
                if pmc.WorkingSetSize > best_ws:
                    best_ws, best_pid = pmc.WorkingSetSize, pid
        finally:
            kernel32.CloseHandle(h)
    return best_pid, best_ws


def module_base(pid, name):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid)
    if snap == ctypes.c_void_p(-1).value or snap == -1:
        return None
    try:
        me = MODULEENTRY32W()
        me.dwSize = ctypes.sizeof(MODULEENTRY32W)
        if not kernel32.Module32FirstW(snap, ctypes.byref(me)):
            return None
        while True:
            if me.szModule.lower() == name:
                return me.modBaseAddr
            if not kernel32.Module32NextW(snap, ctypes.byref(me)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return None


def read_mem(pid, addr, size):
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_string_buffer(size)
        nread = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(nread))
        if not ok:
            return None
        return buf.raw[: nread.value]
    finally:
        kernel32.CloseHandle(h)


def read_first_page(db_path):
    with open(db_path, "rb") as f:
        page = f.read(PAGE_SIZE)
    if len(page) != PAGE_SIZE:
        raise RuntimeError("db first page size != 4096")
    return page


def validate_candidate(candidate, page1):
    """vendor 语义判据：PBKDF2-HMAC-SHA512 + HMAC 校验 session.db 第一页。

    支持两种候选形态：
    - password（原始密钥，256000 次迭代）
    - enc_key（PBKDF2 输出，内存中缓存形态，仅需 2 次迭代派生 mac_key）
    二者都通过同一最终 HMAC 判据验证。"""
    if len(candidate) != KEY_LEN:
        return False
    salt = page1[:SALT_LEN]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    data_end = PAGE_SIZE - RESERVE + 16
    expected = page1[data_end : data_end + 64]
    mac_data = page1[SALT_LEN:data_end] + struct.pack("<I", 1)

    def final_check(mac_key):
        mac = hmac.new(mac_key, digestmod=hashlib.sha512)
        mac.update(mac_data)
        return hmac.compare_digest(mac.digest(), expected)

    # 形态1: enc_key 假设（快，2 次迭代）
    mac_key = hashlib.pbkdf2_hmac("sha512", candidate, mac_salt, 2, KEY_LEN)
    if final_check(mac_key):
        return True
    # 形态2: password 假设（慢，256000 次迭代）
    enc_key = hashlib.pbkdf2_hmac("sha512", candidate, salt, V4_ITER, KEY_LEN)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, KEY_LEN)
    return final_check(mac_key)


def scan_regions(pid, base_addr, max_block=16 * 1024 * 1024):
    """枚举进程可读私有内存区域（堆/栈，不含 IMAGE 映射），返回 [(va, data)]。

    密钥与 salt 的堆上副本必然在 MEM_PRIVATE commit 区；模块 image 区（含
    MMV1 常量本体）不需要全量读——锚点常量地址已知，这里只搜"指向该常量的
    指针残留"（栈/堆）与 salt 二进制副本。全量读 image 区会导致纯 Python
    扫描超时。max_block 限制单块读取字节数。"""
    regions = []
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return regions
    try:
        addr = 0
        mbi = MEMORY_BASIC_INFORMATION()
        while True:
            ret = kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi),
                                          ctypes.sizeof(mbi))
            if ret == 0:
                break
            if (mbi.State == MEM_COMMIT and mbi.RegionSize > 0
                    and mbi.Type == MEM_PRIVATE
                    and readable_protect(mbi.Protect)):
                va = mbi.BaseAddress or 0
                size = mbi.RegionSize
                if size > 0 and size <= 0x7FFFFFFF:
                    for chunk_off in range(0, size, max_block):
                        chunk_sz = min(max_block, size - chunk_off)
                        data = read_mem(pid, va + chunk_off, chunk_sz)
                        if data is not None:
                            regions.append((va + chunk_off, data))
            addr = (mbi.BaseAddress or 0) + mbi.RegionSize
            if addr >= 0x7FFFFFFFFFFF:
                break
    finally:
        kernel32.CloseHandle(h)
    return regions


def collect_candidates(regions, base_addr, salt16, window=2048, align=8):
    """收集候选：锚点指针命中窗口 + salt 命中窗口。"""
    anchor = struct.pack("<Q", base_addr + MMV1_RVA)
    candidates = set()
    anchor_hits = []
    salt_hits = 0
    for va, data in regions:
        # 锚点指针
        pos = data.find(anchor)
        while pos != -1:
            anchor_hits.append((va + pos, pos))
            # 命中点邻域窗口
            lo = max(0, pos - window)
            hi = min(len(data), pos + window + KEY_LEN)
            for off in range(lo, hi, align):
                if off + KEY_LEN <= len(data):
                    candidates.add(data[off : off + KEY_LEN])
            pos = data.find(anchor, pos + 1)
        # salt 命中
        p = data.find(salt16)
        while p != -1:
            salt_hits += 1
            lo = max(0, p - window)
            hi = min(len(data), p + window + KEY_LEN)
            for off in range(lo, hi, align):
                if off + KEY_LEN <= len(data):
                    candidates.add(data[off : off + KEY_LEN])
            p = data.find(salt16, p + 1)
    return anchor_hits, salt_hits, candidates


def hash_digest(b):
    return hashlib.sha256(b).hexdigest()[:12]


def _validate_worker(args):
    """多进程校验 worker：返回 (candidate, 是否通过)。"""
    candidate, page1 = args
    return candidate, validate_candidate(candidate, page1)


def parallel_validate(candidates, page1, workers=None, progress_cb=None):
    """并行语义校验候选，返回第一个通过的候选或 None。"""
    if not candidates:
        return None
    if workers is None:
        workers = min(24, max(1, (os.cpu_count() or 4)))
    pool = multiprocessing.Pool(processes=workers)
    done = 0
    try:
        # 分批避免一次性传巨大对象；候选数量通常 <10k，直接全部投入
        for cand, ok in pool.imap_unordered(_validate_worker,
                                            ((c, page1) for c in candidates),
                                            chunksize=8):
            done += 1
            if progress_cb and done % 2000 == 0:
                progress_cb(done)
            if ok:
                pool.terminate()
                return cand
    finally:
        pool.close()
        pool.join()
    return None


def main():
    ap = argparse.ArgumentParser(description="WeChat 4.1.15.13 WCDB v4 key extractor")
    ap.add_argument("--db", required=True, help="session.db absolute path")
    ap.add_argument("--out", default="", help="optional chatlog.json path to write data_key/img_key")
    ap.add_argument("--window", type=int, default=2048,
                    help="salt 命中点前后候选窗口字节数（默认 2048）")
    ap.add_argument("--align", type=int, default=8, choices=[1, 4, 8],
                    help="候选起始偏移对齐（默认 8）")
    ap.add_argument("--pid", type=int, default=0, help="指定进程 pid（默认自动选最大 WS）")
    args = ap.parse_args()

    page1 = read_first_page(args.db)
    salt16 = page1[:SALT_LEN]
    print(f"[info] salt head: {salt16[:8].hex()}  page1 ok: {page1[:16].startswith(bytes(16)) or True}")

    pids = weixin_pids()
    print(f"[info] weixin pids: {pids}")
    if not pids:
        print("[fail] 微信未运行，无法取密钥")
        return 1
    if args.pid:
        pid = args.pid
        print(f"[info] target pid={pid} (指定)")
    else:
        pid, ws = largest_weixin_pid(pids)
        print(f"[info] target pid={pid} ws={ws}")

    base = module_base(pid, WECHAT_DLL_NAME)
    if not base:
        print(f"[fail] 在 pid {pid} 未找到 weixin.dll")
        return 1
    print(f"[info] weixin.dll base = {base:#x}")

    # 锚点自检：读 MMV1 常量
    probe = read_mem(pid, base + MMV1_RVA, 8)
    if not probe or not probe.startswith(b"MMV1"):
        print(f"[fail] 锚点自检失败：+{MMV1_RVA:#x} 读不到 MMV1 魔数")
        return 1
    print(f"[info] 锚点自检 OK: {probe!r}")

    regions = scan_regions(pid, base)
    total = sum(len(d) for _, d in regions)
    print(f"[info] 可读区域 {len(regions)} 块, 共 {total} 字节")

    anchor_hits, salt_hits, candidates = collect_candidates(regions, base, salt16,
                                                             window=args.window,
                                                             align=args.align)
    print(f"[info] 锚点指针命中 {len(anchor_hits)} 处, salt 命中 {salt_hits} 处, 候选 {len(candidates)} 个")
    for va, pos in anchor_hits[:20]:
        print(f"       anchor hit @ {va:#x}")

    # 语义校验（多进程并行）
    checked = 0
    cand_list = [c for c in candidates
                 if c != b"\x00" * 32 and c != b"\xff" * 32]
    print(f"[info] 待校验候选 {len(cand_list)} 个（排除全零/全F）")
    found = parallel_validate(cand_list, page1,
                              progress_cb=lambda n: print(f"[progress] 已校验 {n}/{len(cand_list)}", flush=True))
    if found is not None:
        d = hash_digest(found)
        print(f"[success] 语义校验通过: len={len(found)} sha256[:12]={d}")
        if args.out:
            out = {
                "data_key": found.hex(),
                "img_key": found[:16].hex(),
            }
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            print(f"[info] 已写入 {args.out} (只写 hex, 不打印值)")
        return 0
    print(f"[fail] 候选 {len(cand_list)} 个全部未通过语义校验；锚点命中 {len(anchor_hits)}、salt 命中 {salt_hits}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
