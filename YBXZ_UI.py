#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2026/9/29
# @Author  : lb
# @File    : YBXZ_UI.py
# 佣兵小镇挂机界面: 卡片式窗口管理 + 多标签页日志
# 每个检测到的游戏窗口一张卡片: 缩略图/状态/功能切换/启动停止
# 日志: 每个窗口一个标签页
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
import tkinter.messagebox as mb
from tkinter import scrolledtext
from tkinter import ttk

import numpy as np
from PIL import Image, ImageTk

MODE_NAME = {'4': '远征刷眩晕', '5': '精灵秘境挂机', '6': '地下城挂机', '7': '网络监控重启'}
MODE_ID = {v: k for k, v in MODE_NAME.items()}
SHORT_NAME = {'4': '远征', '5': '秘境', '6': '地下城', '7': '监控'}
GAME_CLASS = 'Chrome_WidgetWin_0'
GAME_TITLE = '佣兵小镇'
PW_RENDERFULLCONTENT = 2
RESAMPLE = getattr(Image, 'Resampling', Image).LANCZOS  # 兼容新旧Pillow
CARD_REFRESH_MS = 5000  # 卡片自动刷新周期


def _is_win7():
    """Win7检测: 注册表CurrentVersion=6.1(Win10是10.x, Win8是6.2/6.3)"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r'SOFTWARE\Microsoft\Windows NT\CurrentVersion')
        return winreg.QueryValueEx(k, 'CurrentVersion')[0].startswith('6.1')
    except Exception:
        return False


_IS_WIN7 = _is_win7()

# ================== 在线更新 ==================
# 发布仓库: github.com/Mr-LB1/YBXZ (公开仓库, 只放version.json+YBXZ.exe, 无源码)
# 发版流程: 打包后跑 publish_release.py, 客户端启动/点按钮自动检测下载
# 多镜像原因: 国内raw.githubusercontent常被墙, api.github.com/jsDelivr一般可达
APP_VER = '202610070001'          # 本程序版本(发版时由publish脚本同步更新此常量)
BUILD_TAG = ''                    # 构建标识(显示在标题栏): 正式版为空, Win7测试包构建时置'【Win7测试专用】'
GITEE_REPO = 'li-bin970228/ybxz'  # 更新源: gitee公开仓库(纯国内, 不用github)
GITEE_BRANCH = 'master'
VER_URL_GITEE = 'https://gitee.com/%s/raw/%s/version.json' % (GITEE_REPO, GITEE_BRANCH)
CHUNK_URL_GITEE = 'https://gitee.com/%s/releases/download/%%s/%%s' % GITEE_REPO


_update_opener = None


def _direct_opener(ssl_ctx=None):
    """直连opener: 无视系统代理。VPN软件关掉后常在系统里残留死代理,
    urllib默认走系统代理 -> 全部连接失败误报网络异常(实测)"""
    import urllib.request
    handlers = [urllib.request.ProxyHandler({})]
    if ssl_ctx is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_ctx))
    return urllib.request.build_opener(*handlers)


def _http_open(req, timeout):
    """打开请求: 优先正常证书校验; 失败且是SSL证书错误(Win7根证书陈旧,
    gitee证书链在老系统校验不过, 实测检查更新全部失败)则退非严格校验重试
    (下载内容仍有md5整体校验兜底), 并留痕到update_log.txt"""
    import ssl
    import urllib.request
    req = urllib.request.Request(req, headers={'User-Agent': 'YBXZ-updater'}) \
        if isinstance(req, str) else req
    try:
        return _direct_opener().open(req, timeout=timeout)
    except urllib.error.URLError as e:
        reason = getattr(e, 'reason', None)
        if not isinstance(reason, ssl.SSLCertVerificationError) \
                and 'CERTIFICATE_VERIFY_FAILED' not in str(reason):
            raise
        _ulog('HTTPS证书校验失败(%r), 退非严格校验重试' % reason)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return _direct_opener(ctx).open(req, timeout=timeout)


def _http_get(url, timeout=8):
    return _http_open(url, timeout).read()


VER_URL_GITEE_API = 'https://gitee.com/api/v5/repos/%s/contents/version.json?ref=%s' % (
    GITEE_REPO, GITEE_BRANCH)


def fetch_remote_version():
    """拉远程version.json, 双通道: gitee raw(302跳转附件域名) -> gitee API
    contents(base64, 同主机不跳转, raw被墙/被DNS污染时仍可用)。
    加时间戳参数防CDN缓存导致永远检查不到新版; 全失败留痕后返回None(静默)"""
    import base64
    import json
    import time
    # API通道URL已带?ref=参数, 必须用&拼接(实测拼成?ref=master?t=xxx后
    # gitee把ref解析成不存在的分支, 返回空列表[], 下标访问content直接TypeError)
    bust = int(time.time())
    channels = (VER_URL_GITEE + '?t=%d' % bust, VER_URL_GITEE_API + '&t=%d' % bust)
    last_err = None
    for url in channels:
        for _ in range(2):
            try:
                if '/contents/' in url:
                    wrap = json.loads(_http_get(url, timeout=10).decode('utf-8'))
                    data = json.loads(base64.b64decode(wrap['content']).decode('utf-8'))
                else:
                    data = json.loads(_http_get(url, timeout=10).decode('utf-8'))
                if int(data.get('ver', 0)) > 0:
                    _ulog('版本检查成功: 远端ver=%s (通道: %s)' % (
                        data.get('ver'), 'gitee-api' if '/contents/' in url else 'gitee-raw'))
                    return data
            except Exception as e:
                last_err = e
                _ulog('拉取version.json失败 %s: %r' % (url.split('?')[0], e))
            time.sleep(1)
    _ulog('检查更新: 全部通道失败, 最后错误 %r' % last_err)
    return None


def info_ver(data):
    return data.get('ver', 0)


def _md5_file(path, progress_cb=None, total=0):
    import hashlib
    h = hashlib.md5()
    done = 0
    with open(path, 'rb') as f:
        while 1:
            chunk = f.read(262144)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if progress_cb:
                progress_cb(done, total)
    return h.hexdigest()


GITEE_API_BASE = 'https://gitee.com/api/v5/repos/%s' % GITEE_REPO


def _download_chunk(c, tag, progress=None, cancel_evt=None):
    """下载单个分片, 双通道各重试一次: release附件直链(302跳转到附件CDN,
    部分网络/杀软会重置) -> gitee API contents(base64, 同主机不跳转兜底),
    流式读取实时回报进度(md5校验), 每次尝试都留痕update_log.txt;
    cancel_evt置位时抛_UpdateCancelled(用户取消)"""
    import hashlib
    import time
    urls = [(CHUNK_URL_GITEE % (tag, c['name']), False),
            (GITEE_API_BASE + '/contents/chunks/%s?ref=%s' % (c['name'], tag), True)]
    last_err = None
    for attempt in range(2):
        for idx, (url, is_api) in enumerate(urls):
            try:
                # 必须走_http_open: 带SSL证书降级(实测Win7分片下载全因
                # CERTIFICATE_VERIFY_FAILED失败, 版本检查的降级逻辑没覆盖到这里)
                r = _http_open(url, timeout=120)
                data, got = [], 0
                while 1:
                    if cancel_evt is not None and cancel_evt.is_set():
                        raise _UpdateCancelled()
                    b = r.read(262144)
                    if not b:
                        break
                    data.append(b)
                    got += len(b)
                    if progress:
                        progress(got)
                data = b''.join(data)
                if is_api:
                    # API contents返回base64包裹的JSON
                    import base64
                    data = base64.b64decode(
                        json.loads(data.decode('utf-8'))['content'])
                if hashlib.md5(data).hexdigest() != c['md5']:
                    raise RuntimeError('分片校验失败')
                _ulog('分片%s 通道%d下载成功(%dKB)' % (c['name'], idx + 1, len(data) // 1024))
                return data
            except _UpdateCancelled:
                raise  # 用户取消: 立即上抛, 严禁被当成下载失败吞掉重试(实测按钮卡灰)
            except Exception as e:
                last_err = e
                _ulog('分片%s 通道%d尝试%d失败: %s' % (c['name'], idx + 1, attempt + 1, e))
                time.sleep(0 if (cancel_evt is not None and cancel_evt.is_set()) else 1)
    raise RuntimeError('分片%s下载失败: %s' % (c['name'], last_err))


def download_exe(info, tmp_path, progress_cb, cancel_evt=None):
    """下载新版exe到tmp_path并校验md5。cancel_evt置位=用户取消(抛_UpdateCancelled)。
    压缩分片3路并发下载(实测gitee单连接1.4MB/s, 并发提速2倍+);
    每片完成即落盘(md5校验过的才留), 失败重试时跳过已完成片=断点续传;
    按序重组 -> 解压 -> 整体校验; 进度按完成字节数跨线程聚合"""
    import hashlib
    import zlib
    chunks = info['chunks']
    tag = info.get('tag', '')
    total = sum(c['size'] for c in chunks)
    parts = [None] * len(chunks)
    import threading
    done_lock = threading.Lock()
    state = {'done_map': [0] * len(chunks)}

    def report(i, n):
        with done_lock:
            state['done_map'][i] = n
            progress_cb(sum(state['done_map']), total)

    def worker(i, c):
        if cancel_evt is not None and cancel_evt.is_set():
            raise _UpdateCancelled()
        pfile = '%s.%s.part' % (tmp_path, c['name'])
        data = None
        if os.path.exists(pfile):
            try:
                data = open(pfile, 'rb').read()
                if hashlib.md5(data).hexdigest() != c['md5']:
                    data = None  # 残片不完整, 重下
            except OSError:
                data = None
        if data is None:
            data = _download_chunk(c, tag, lambda n: report(i, n), cancel_evt)
        else:
            report(i, len(data))  # 缓存命中也要计入进度
        try:
            f = open(pfile, 'wb')
            f.write(data)
            f.close()
        except OSError:
            pass
        parts[i] = data

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        # map迭代会把worker异常抛到这里, 由调用方提示
        list(ex.map(worker, range(len(chunks)), chunks))
    raw = zlib.decompress(b''.join(parts))
    if hashlib.md5(raw).hexdigest() != info.get('md5'):
        # 校验失败保留断点文件: 用户重试时已完成的分片直接复用(断点续传)
        raise RuntimeError('校验失败(下载不完整), 请重试')
    with open(tmp_path, 'wb') as f:
        f.write(raw)
    import glob
    for p in glob.glob(tmp_path + '.*.part'):
        try:
            os.remove(p)  # 全部成功, 清理断点文件
        except OSError:
            pass


def apply_update(tmp_path, exe_name):
    """替换运行中的exe并统一命名为云端下发的名字(version.json的exe_name;
    客户从微信收到的文件名五花八门, 更新完成后统一命名, 名字缺失则沿用当前名)。
    关键顺序: 先把运行中的exe改名进update_data腾位(本地磁盘允许改名运行中的
    exe, 但禁止覆盖运行中的exe——实测客户exe恰好叫标准名时os.replace直接
    PermissionError导致静默失败), 再放新版到标准名位置
    :return: 新exe完整路径(重启时拉起它)"""
    d = _exe_dir()
    cur = sys.executable
    dd = _data_dir()
    std = os.path.join(d, exe_name)
    # 标准名位置被其他文件占用(如同目录还有一份旧副本): 挪进update_data腾位
    if os.path.abspath(std) != os.path.abspath(cur) and os.path.exists(std):
        try:
            os.replace(std, os.path.join(dd, '被替换.%d.exe' % os.getpid()))
        except OSError as e:
            _ulog('apply: 标准名被占用挪不进update_data(%r), 新版换名落位' % e)
            std = os.path.join(d, '%s_%d.exe' % (exe_name[:-4], os.getpid()))  # 挪不动就换名
    # 运行中的旧exe改名进update_data(名字进备份名, 60秒定期清理回收)
    try:
        os.rename(cur, os.path.join(dd, '%s.%d.old.exe' % (os.path.basename(cur), os.getpid())))
    except OSError as e:
        # 失败不阻断: 多数情况是旧exe恰为标准名(改名腾位失败)时os.replace会报错,
        # 由调用方弹错误框; 这里留一行日志供远程定位
        _ulog('apply: 旧exe改名进update_data失败: %r' % e)
    os.replace(tmp_path, std)
    _cleanup_old_backups()
    try:
        os.remove(SKIP_VER_FILE)  # 新版本生效, 清掉"忽略此版本"标记
    except OSError:
        pass
    return std


def _exe_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath('.')


def _data_dir():
    """更新衍生文件统一目录(exe旁的update_data): update_log/update_skip/
    旧exe备份/下载临时文件都收在这里, 不散落exe旁"""
    d = os.path.join(_exe_dir(), 'update_data')
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = _exe_dir()  # 目录创建失败(如只读位置)退回exe旁
    return d


SKIP_VER_FILE = os.path.join(_data_dir(), 'update_skip.txt')


def _ulog(msg):
    """更新过程日志(打包版无控制台, update_log.txt是远程排查唯一线索)"""
    try:
        f = open(os.path.join(_data_dir(), 'update_log.txt'), 'a')
        f.write('%s %s\n' % (time.strftime('%m-%d %H:%M:%S'), msg))
        f.close()
    except Exception:
        pass


def _rm_query_lockers(path):
    """Restart Manager查询占用文件的进程, 返回[(pid, 应用名, 镜像路径)]。
    安装器级官方API, 杀毒/资源管理器/残留进程都能查到; 失败返回None"""
    import ctypes
    from ctypes import wintypes

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [('dwProcessId', wintypes.DWORD),
                    ('ProcessStartTime', wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [('Process', RM_UNIQUE_PROCESS),
                    ('strAppName', wintypes.WCHAR * 256),
                    ('strServiceShortName', wintypes.WCHAR * 64),
                    ('ApplicationType', wintypes.DWORD),
                    ('AppStatus', wintypes.DWORD),
                    ('TSSessionId', wintypes.DWORD),
                    ('bRestartable', wintypes.BOOL)]

    try:
        rm = ctypes.WinDLL('rstrtmgr')
        k32 = ctypes.WinDLL('kernel32')
        session = wintypes.DWORD()
        sesskey = ctypes.create_unicode_buffer(33)
        if rm.RmStartSession(ctypes.byref(session), 0, sesskey) != 0:
            return None
        try:
            pathbuf = ctypes.c_wchar_p(path)
            arr = (wintypes.LPCWSTR * 1)(pathbuf)
            if rm.RmRegisterResources(session, 1, arr, 0, None, 0, None) != 0:
                return None
            needed = wintypes.DWORD(0)
            count = wintypes.DWORD(0)
            reasons = wintypes.DWORD(0)
            proc_info = (RM_PROCESS_INFO * 1)()
            code = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                                proc_info, ctypes.byref(reasons))
            if code == 234:  # ERROR_MORE_DATA: 按实际数量重取
                count = needed
                proc_info = (RM_PROCESS_INFO * needed.value)()
                code = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                                    proc_info, ctypes.byref(reasons))
            if code != 0:
                return None
            out = []
            for i in range(count.value):
                pi = proc_info[i]
                pid = pi.Process.dwProcessId
                img = ''
                h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if h:
                    buf = ctypes.create_unicode_buffer(1024)
                    bsz = wintypes.DWORD(1024)
                    if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(bsz)):
                        img = buf.value
                    k32.CloseHandle(h)
                out.append((pid, pi.strAppName, img))
            return out
        finally:
            rm.RmEndSession(session)
    except Exception as e:
        _ulog('RM占用查询失败(降级为直接删除): %r' % e)
        return None


def _pid_cmdline(pid):
    """查进程命令行(判断是否挂机worker用), 失败返回''"""
    try:
        r = subprocess.run('wmic process where processid=%d get commandline' % pid,
                           capture_output=True, timeout=15)
        return r.stdout.decode('gbk', 'replace')
    except Exception:
        return ''


def _force_release_and_delete(path, name):
    """删除被占用的旧备份, 三层强制手段:
    1.Restart Manager找到占用者; 2.占用者是自己程序的残留实例(非--worker挂机
    进程)则结束之再删; 3.杀毒/挂机worker不动(记录名字), 计划重启电脑后删除
    (MoveFileEx需管理员). 只对同一文件尝试一次, 防定期清理反复跑重活"""
    import ctypes
    from ctypes import wintypes
    lockers = _rm_query_lockers(path)
    if not lockers:
        _ulog('force[%s]: 查不到占用者(可能已释放或API不可用)' % name)
    for pid, app, img in lockers or []:
        if pid == os.getpid():
            continue
        is_worker = '--worker' in _pid_cmdline(pid)
        _ulog('force[%s]: 占用者 pid=%d app=%r worker=%s img=%r'
              % (name, pid, app, is_worker, os.path.basename(img)))
        if is_worker:
            _ulog('force[%s]: 占用者是挂机worker, 不中断任务, 等待其退出后自动清理' % name)
            continue
        try:
            subprocess.run('taskkill /f /pid %d' % pid, capture_output=True)
            _ulog('force[%s]: 已结束残留进程 %d' % (name, pid))
        except Exception as e:
            _ulog('force[%s]: 结束进程失败 %r' % (name, e))
    try:
        os.remove(path)
        _ulog('force[%s]: 强制删除成功' % name)
        return
    except Exception as e:
        _ulog('force[%s]: 仍删不掉: %r' % (name, e))
    try:
        MOVEFILE_DELAY_UNTIL_REBOOT = 4
        if ctypes.windll.kernel32.MoveFileExW(path, None,
                                              MOVEFILE_DELAY_UNTIL_REBOOT):
            _ulog('force[%s]: 已计划重启电脑后自动删除(需管理员权限)' % name)
        else:
            _ulog('force[%s]: 计划重启删除失败(需管理员), 留给60秒定期清理重试' % name)
    except Exception as e:
        _ulog('force[%s]: MoveFileEx异常 %r' % (name, e))


_toolbox_done = set()  # 已跑过强制释放工具箱的备份文件(每文件只跑一次防定期清理反复重活)
_fail_count = {}       # 备份文件 -> 累计删除失败次数(>=3次视为杀软顽固拦截, 提示用户手动删)
_manual_prompted = set()  # 已弹窗提示过手动删除的文件(每文件每次运行只弹一次)


class _UpdateCancelled(Exception):
    """用户在进度窗口点了取消"""


def _cleanup_update_parts(tmp_path):
    """取消更新后清理全部断点分片文件"""
    import glob
    for p in glob.glob(tmp_path + '.*.part'):
        try:
            os.remove(p)
        except OSError:
            pass


def _cleanup_old_backups(when='', quiet=False):
    """清理更新遗留的旧exe备份(*.old.exe)。
    备份统一放update_data子目录, 历史版本可能留在exe旁——两个位置都扫
    (当前正在运行的exe本体除外)。
    挂机worker是同exe镜像的独立进程, UI更新重启后它们仍握着备份文件——
    所以界面常态化定期清理(60秒一次), worker一退1分钟内备份必被清掉。
    quiet=True时无遗留不打日志(定期模式防update_log.txt膨胀)。
    :return: 顽固备份文件名列表(删除失败>=3次, 杀软拦截, 需提示用户手动删)"""
    me = os.path.basename(sys.executable)
    k32 = ctypes.WinDLL('kernel32')
    scan_dirs = [(_data_dir(), False), (_exe_dir(), True)]
    found = []
    for d, legacy in scan_dirs:
        try:
            names = os.listdir(d)
        except OSError as e:
            _ulog('cleanup%s listdir失败 %s: %r' % (when, d, e))
            continue
        for n in names:
            if n == me or not n.endswith('.old.exe'):
                continue
            if legacy and not n.startswith('佣兵小镇'):
                continue  # exe旁只清本程序的历史遗留, 不碰用户其他文件
            found.append((d, n))
    if not found:
        if not quiet:
            _ulog('cleanup%s: 无遗留备份' % when)
        return []
    stubborn = []
    for d, n in found:
        p = os.path.join(d, n)
        try:
            os.remove(p)
            _ulog('cleanup%s: 已删除备份 %s (%s)' % (when, n, d))
            _fail_count.pop(n, None)
            continue
        except Exception as e:
            _fail_count[n] = _fail_count.get(n, 0) + 1
            _ulog('cleanup%s: 删除失败(第%d次) %s: %r (文件大小%s)'
                  % (when, _fail_count[n], n, e, os.path.getsize(p)))
        # 手段0: 清只读/系统/隐藏属性后重试(属性置位会导致同样的拒绝访问)
        try:
            attrs = k32.GetFileAttributesW(p)
            if attrs != -1 and attrs & 0x7:  # READONLY|HIDDEN|SYSTEM
                k32.SetFileAttributesW(p, attrs & ~0x7)
                os.remove(p)
                _ulog('cleanup%s: 已清属性并删除 %s' % (when, n))
                _fail_count.pop(n, None)
                continue
        except Exception:
            pass
        if n in _toolbox_done:
            # 改名挂"待删除"标记(保留.old.exe后缀, 定期清理持续重试),
            # 用户看到也知道是可手动删的垃圾; RM查不到占用者还删不掉=杀软拦截, 只能等
            try:
                os.rename(p, os.path.join(d, '待删除_' + n))
                _ulog('cleanup%s: 已改名待删除_%s (杀毒拦截中, 稍后自动重试或手动删除)' % (when, n))
            except Exception:
                _ulog('cleanup%s: %s 等待占用/杀毒释放后自动重试' % (when, n))
            continue
        _toolbox_done.add(n)
        try:
            _force_release_and_delete(p, n)
        except Exception as e:
            _ulog('cleanup%s: 强制工具箱异常 %r' % (when, e))
        if _fail_count.get(n, 0) >= 3:
            stubborn.append(n)
    return stubborn


def _skipped_ver():
    """用户点过"否"的版本号: 静默检查不再弹窗(手动检查更新不受限)"""
    try:
        return open(SKIP_VER_FILE).read().strip()
    except OSError:
        return ''


def _set_skipped_ver(ver):
    try:
        f = open(SKIP_VER_FILE, 'w')
        f.write(str(ver))
        f.close()
    except OSError:
        pass


class SafeStdout(object):
    """stdout安全封装: 打包后的管道是GBK编码, paddle可能认出GBK编不了的
    字符(如®), 直接print会UnicodeEncodeError崩溃, 这里替换后重写"""

    def __init__(self, target):
        self.target = target

    def write(self, s):
        try:
            return self.target.write(s)
        except UnicodeEncodeError:
            return self.target.write(s.encode('gbk', 'replace').decode('gbk'))

    def flush(self):
        try:
            self.target.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self.target, name)


def capture_hwnd(hwnd):
    """PrintWindow窗口内部截图(被遮挡也能截, 最小化不行), 返回PIL Image或None
    非基准缩放下按物理尺寸截取后缩回逻辑尺寸, 保证缩略图完整(实测150%缩放裁剪)"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    if w <= 0 or h <= 0:
        return None
    pw, ph = w, h
    if hasattr(user32, 'SetThreadDpiAwarenessContext'):
        old_ctx = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PMv2
        if old_ctx:
            pr = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(pr))
            user32.SetThreadDpiAwarenessContext(old_ctx)
            if pr.right > 0 and pr.bottom > 0:
                pw, ph = pr.right, pr.bottom

    class BMIH(ctypes.Structure):
        _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
                    ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
                    ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                    ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
                    ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
                    ('biClrImportant', ctypes.c_uint32)]

    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, pw, ph)
    old = gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    bmi = BMIH()
    bmi.biSize = ctypes.sizeof(BMIH)
    bmi.biWidth = pw
    bmi.biHeight = -ph
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    buf = ctypes.create_string_buffer(pw * ph * 4)
    gdi32.GetDIBits(mem, bmp, 0, ph, buf, ctypes.byref(bmi), 0)
    arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape(ph, pw, 4)[:, :, :3][:, :, ::-1].copy()
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if (pw, ph) != (w, h):
        arr = np.array(Image.fromarray(arr).resize((w, h), RESAMPLE))
    # Win7: PrintWindow只能拿到GPU窗口的黑图(worker进程的大漠兜底会把成功帧
    # 写到exe目录_latest_frame.jpg共享给界面), 黑帧时读它
    if arr.max() <= 8:
        try:
            fp = os.path.join(_data_dir(), '_latest_frame.jpg')
            if os.path.exists(fp) and time.time() - os.path.getmtime(fp) < 15:
                img = Image.open(fp).convert('RGB')
                if img.size != (w, h):
                    img = img.resize((w, h), RESAMPLE)
                return img
        except Exception:
            pass
    return Image.fromarray(arr)


class _CardPanelMixin(object):
    # ---------- 卡片列表 ----------
    def on_card_wheel(self, e):
        """鼠标滚轮滚动卡片列表; 指针不在列表区域时不响应(不抢日志区滚轮)"""
        try:
            w = self.card_canvas.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return  # 指针在下拉框弹出列表(popdown)等临时控件上
        while w is not None:
            if w is self.card_canvas or w is self.card_inner:
                self.card_canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units')
                return
            w = getattr(w, 'master', None)

    def jump_to_card(self, _e=None):
        """下拉选择窗口号, 卡片列表立刻滚到对应卡片(免拖动)"""
        hwnd = self.jump_hwnds.get(self.jump_var.get())
        card = self.cards.get(hwnd)
        if card is None:
            return
        bbox = self.card_canvas.bbox('inner')
        if not bbox:
            return
        y = card['frame'].winfo_y()
        h = max(1, bbox[3] - bbox[1] - 1)
        self.card_canvas.yview_moveto(max(0.0, min(1.0, y / h)))

    def refresh_jump_box(self):
        """同步跳转下拉框: 选项带实时状态, 如 窗口1(运行中:地下城挂机)/窗口2(未启动)"""
        prev = self.jump_hwnds.get(self.jump_var.get())
        self.jump_hwnds = {}
        for h in sorted(self.cards, key=lambda k: self.win_num[k]):
            name = '窗口%d' % self.win_num[h]
            if self.is_running(h):
                name += '(运行中:%s)' % MODE_NAME.get(self.tabs[h]['mode'], '')
            else:
                name += '(未启动)'
            self.jump_hwnds[name] = h
        vals = list(self.jump_hwnds)
        if list(self.jump_box['values']) != vals:  # 选项没变不重设, 防下拉展开被打断
            self.jump_box['values'] = vals
        if prev is not None and prev in self.jump_hwnds.values():
            for name, h in self.jump_hwnds.items():
                if h == prev:
                    self.jump_var.set(name)
                    break
        elif self.jump_hwnds:
            self.jump_var.set(vals[0])

    def apply_default_to_cards(self):
        """默认功能变更: 同步到所有未启动的卡片(运行中的不受影响)"""
        for hwnd, card in self.cards.items():
            if not self.is_running(hwnd):
                card['mode_var'].set(MODE_NAME[self.mode.get()])
                card['mode_btn'].configure(text=SHORT_NAME[self.mode.get()])

    def desktop_lnks(self):
        """枚举桌面(用户/OneDrive/公共)上的快捷方式, 佣兵相关的排最前"""
        home = os.path.expanduser('~')
        dirs = [os.path.join(home, 'Desktop'),
                os.path.join(home, 'OneDrive', 'Desktop'),
                os.path.join(os.environ.get('ONEDRIVE', '') or home, 'Desktop'),
                os.path.join(os.environ.get('PUBLIC', ''), 'Desktop')]
        out = []
        for d in dirs:
            if d and os.path.isdir(d):
                for f in os.listdir(d):
                    if f.lower().endswith('.lnk'):
                        out.append(os.path.join(d, f))
        out.sort(key=lambda p: (0 if '佣兵' in p else 1, p))
        self.lnk_names = {os.path.basename(p): p for p in out}
        return out

    def sync_cards(self):
        """枚举游戏窗口, 同步右侧卡片(新增/移除/更新状态与缩略图)"""
        user32 = ctypes.windll.user32
        found = []
        hwnd = 0
        while True:
            hwnd = user32.FindWindowExW(0, hwnd, GAME_CLASS, GAME_TITLE)
            if not hwnd:
                break
            found.append(hwnd)
        # 稳定编号: 窗口首次发现时分配号码, 终身不变(枚举顺序跟随Z序会变, 不能用作编号)
        for hwnd in found:
            if hwnd not in self.win_num:
                self.win_num[hwnd] = max(self.win_num.values() or [0]) + 1
        found.sort(key=lambda h: self.win_num[h])

        # 移除已消失窗口的卡片
        for hwnd in list(self.cards.keys()):
            if hwnd not in found:
                self.cards[hwnd]['frame'].destroy()
                del self.cards[hwnd]
                self.win_lnk.pop(hwnd, None)
                # 游戏窗口已关闭, 分两种情况:
                # - 断网/掉线重启(worker写了重启标记)或网络监控模式: 保留日志页等
                #   新窗口出来由下方转移逻辑改挂(重启全程几分钟, 这里杀了就无法续挂,
                #   实测只有网络监控能完成断网重启, 其他功能的worker窗口刚关就被杀)
                # - 其他原因(如手动关游戏): 照旧静默结束worker并关闭日志页
                tab = self.tabs.get(hwnd)
                if tab is not None:
                    alive = tab['proc'] is not None and tab['proc'].poll() is None
                    monitor = tab['mode'] == MODE_ID['网络监控重启']
                    if alive and (monitor or os.path.exists(
                            os.path.join(_data_dir(), 'ybxz_restarting_%d.flag' % hwnd))):
                        continue  # 重启中: 保留日志页, 等改挂新窗口
                    if alive:
                        subprocess.call(['taskkill', '/F', '/T', '/PID', str(tab['proc'].pid)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    try:
                        os.remove(os.path.join(_data_dir(), 'ybxz_restarting_%d.flag' % hwnd))
                    except OSError:
                        pass
                    self.nb.forget(tab['frame'])
                    del self.tabs[hwnd]

        # 有窗口时隐藏"启动游戏"按钮, 没有时显示
        if found:
            self.launch_game_btn.pack_forget()
        else:
            self.launch_game_btn.pack(fill='x', pady=(0, 4))

        # 监控/断网重连重启后游戏窗口句柄会变:
        # 存活worker的旧日志页改挂到新窗口(否则点停止会误关日志页, 状态也不更新)
        for h in found:
            if h in self.tabs:
                continue
            for old_h in list(self.tabs):
                if old_h in found:
                    continue
                t = self.tabs[old_h]
                if t['proc'] is not None and t['proc'].poll() is None:
                    t = self.tabs.pop(old_h)
                    t['hwnd'] = h
                    t['title'] = '窗口%d·%s' % (self.win_num[h], SHORT_NAME.get(t['mode'], ''))
                    self.nb.tab(t['frame'], text=t['title'])
                    self.tabs[h] = t
                    break
        # 新增/更新卡片
        for hwnd in found:
            num = self.win_num[hwnd]
            if hwnd not in self.cards:
                self.make_card(hwnd, num)
            self.update_card(hwnd, num)
        self.refresh_jump_box()

    def make_card(self, hwnd, num):
        """创建窗口卡片: 缩略图居中, 状态/功能同一行, 启动按钮通栏"""
        frame = ttk.LabelFrame(self.card_inner, text='窗口%d' % num)
        frame.pack(fill='x', padx=4, pady=(4, 6))
        thumb = ttk.Label(frame)
        thumb.pack(anchor='center', padx=6, pady=(4, 0))
        info = ttk.Frame(frame)
        info.pack(fill='x', padx=6, pady=(4, 4))
        status = ttk.Label(info, text='未启动')
        status.pack(side='left')
        mrow = ttk.Frame(info)
        mrow.pack(side='right')
        ttk.Label(mrow, text='功能').pack(side='left', padx=(0, 4))
        mode_var = tk.StringVar(value=MODE_NAME[self.mode.get()])
        mode_btn = ttk.Button(mrow, width=7, text=SHORT_NAME[self.mode.get()])
        mode_btn.configure(command=lambda m=mode_var, b=mode_btn:
                           self.cycle_window_mode(m, b))
        mode_btn.pack(side='left')
        run_btn = ttk.Button(frame, text='启动', command=lambda: self.card_toggle(hwnd))
        run_btn.pack(fill='x', padx=6, pady=(0, 6))
        # 重启快捷方式: 多账号各配各的lnk, 自动分配未被占用的
        lnks = self.desktop_lnks()
        if len(lnks) > 1:
            lrow = ttk.Frame(frame)
            lrow.pack(fill='x', padx=6, pady=(0, 6))
            ttk.Label(lrow, text='重启lnk').pack(side='left', padx=(0, 4))
            lnk_var = tk.StringVar()
            box = ttk.Combobox(lrow, textvariable=lnk_var, state='readonly')
            box['values'] = list(self.lnk_names)
            used = {os.path.basename(v) for v in self.win_lnk.values()}
            pick = next((q for q in lnks if os.path.basename(q) not in used), lnks[0])
            lnk_var.set(os.path.basename(pick))
            self.win_lnk[hwnd] = pick
            box.bind('<<ComboboxSelected>>',
                     lambda e, h=hwnd, v=lnk_var:
                     self.win_lnk.__setitem__(h, self.lnk_names.get(v.get(), v)))
            box.pack(side='left', fill='x', expand=True)
        else:
            self.win_lnk[hwnd] = lnks[0] if lnks else ''
        card = {'frame': frame, 'thumb': thumb, 'status': status,
                'mode_var': mode_var, 'mode_btn': mode_btn, 'run_btn': run_btn,
                'num': num}
        self.cards[hwnd] = card

    def update_card(self, hwnd, num):
        """刷新卡片: 编号/缩略图/运行状态"""
        card = self.cards[hwnd]
        card['num'] = num
        card['frame'].configure(text='窗口%d' % num)
        running = self.is_running(hwnd)
        if running:
            card['status'].configure(text='运行中: ' + MODE_NAME.get(
                self.tabs[hwnd]['mode'], ''))
            card['run_btn'].configure(text='停止')
        else:
            card['status'].configure(text='未启动')
            card['run_btn'].configure(text='启动')
        # 缩略图
        img = capture_hwnd(hwnd)
        if img is not None:
            img.thumbnail((170, 300), RESAMPLE)
            photo = ImageTk.PhotoImage(img)
            card['thumb'].configure(image=photo)
            card['thumb'].image = photo

    def auto_refresh(self):
        """定时同步卡片列表(新窗口自动出现, 关闭的自动移除)"""
        try:
            self.sync_cards()
        except Exception as e:
            print('卡片刷新异常: %r' % e)
        finally:
            self.root.after(CARD_REFRESH_MS, self.auto_refresh)

    def cycle_window_mode(self, mode_var, btn):
        """点击卡片功能按钮: 循环切换 远征 -> 秘境 -> 地下城 -> 监控 -> 远征"""
        order = [MODE_NAME[k] for k in ('4', '5', '6', '7')]
        cur = order.index(mode_var.get()) if mode_var.get() in order else -1
        new = order[(cur + 1) % len(order)]
        mode_var.set(new)
        btn.configure(text=SHORT_NAME[MODE_ID[new]])

    def launch_game_clicked(self):
        """没开游戏时: 用快捷方式拉起游戏并自动登录(不绑定具体窗口)"""
        self._spawn(0, self.mode.get())
        self.sync_cards()


class _LogTabsMixin(object):
    # ---------- 标签页日志 ----------
    def make_tab(self, hwnd, title, mode):
        # 先建数据字典, 再建控件: 按钮命令引用字典, 点击时实时读当前句柄
        # (自动启动的标签页会在BOUND_HWND后转移句柄, 按钮自动跟随)
        tab = {'text': None, 'queue': queue.Queue(), 'status': None,
               'proc': None, 'title': title, 'hwnd': hwnd, 'mode': mode}
        self.tabs[hwnd] = tab
        frame = ttk.Frame(self.nb)
        tab['frame'] = frame
        bar = ttk.Frame(frame)
        bar.pack(fill='x')
        btn = ttk.Button(bar, text='停止本窗口',
                         command=lambda: self.tab_toggle(tab['hwnd']))
        btn.pack(side='left', padx=6, pady=4)
        tab['btn'] = btn
        status = ttk.Label(bar, text='运行中')
        status.pack(side='left')
        tab['status'] = status
        close_btn = ttk.Button(bar, text='×', width=2,
                               command=lambda: self.close_tab(tab['hwnd']))
        close_btn.pack(side='right', padx=6, pady=4)
        txt = scrolledtext.ScrolledText(frame, font=('Consolas', 9))
        txt.pack(fill='both', expand=True)
        tab['text'] = txt
        txt.bind('<Key>', self.log_readonly_key)
        txt.bind('<Control-a>', lambda e: self.log_select_all_widget(txt))
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label='复制', command=lambda: self.widget_copy(txt))
        menu.add_command(label='全选', command=lambda: self.log_select_all_widget(txt))
        txt.bind('<Button-3>', lambda e: menu_popup(menu, e))
        self.nb.add(frame, text=title)
        return tab

    def refresh_tab_btn(self, tab):
        """标签页按钮/状态跟随运行状态: 运行中=停止本窗口, 停止/退出=启动本窗口
        (实测: 按钮文字固定'停止本窗口', 停止后再点变成启动, 有歧义)"""
        running = tab['proc'] is not None and tab['proc'].poll() is None
        tab['btn'].configure(text='停止本窗口' if running else '启动本窗口')
        cur = tab['status'].cget('text')
        if running:
            if cur != '运行中':
                tab['status'].configure(text='运行中')
        elif cur == '运行中':
            tab['status'].configure(text='已停止')

    def tab_toggle(self, hwnd):
        """标签页按钮: 运行中=停止; 已退出/已停止=按原功能重新启动"""
        tab = self.tabs.get(hwnd)
        if tab is None:
            return
        if self.is_running(hwnd):
            self.stop_one(hwnd)
            self.refresh_tab_btn(tab)
        else:
            if self._spawn(hwnd, tab['mode']):
                self.nb.tab(tab['frame'], text=tab['title'])
                self.refresh_tab_btn(tab)

    def tab_log(self, hwnd, lines):
        tab = self.tabs.get(hwnd)
        if tab is None:
            return
        txt = tab['text']
        txt.insert('end', lines)
        end_line = int(txt.index('end-1c').split('.')[0])
        if end_line > 3000:
            txt.delete('1.0', '%d.0' % (end_line - 2000))
        txt.see('end')

    def clear_log(self):
        sel = self.nb.select()
        if not sel:
            return
        for tab in self.tabs.values():
            if str(tab['frame']) == sel:
                tab['text'].delete('1.0', 'end')
                return

    def log_tab_notice(self, text):
        mb.showwarning('提示', text)

    def poll_log(self):
        """主线程定时排空所有标签页的日志队列; 任何异常都不中断刷新链"""
        try:
            for hwnd, tab in list(self.tabs.items()):
                lines = []
                try:
                    while True:
                        lines.append(tab['queue'].get_nowait())
                except queue.Empty:
                    pass
                if lines:
                    self.tab_log(hwnd, '\n'.join(lines) + '\n')
                    if lines[-1] == '=== 进程已退出 ===':
                        tab['status'].configure(text='已退出')
                        self.nb.tab(tab['frame'], text=tab['title'] + '(已退出)')
                        self.refresh_tab_btn(tab)
                        # 退出日志不再另存文件: 标签页日志已完整呈现(用户要求)
                        code = tab['proc'].poll() if tab['proc'] is not None else None
                        if hwnd not in self.cards and code == 0:
                            # 游戏窗口已关闭且进程正常退出: 日志页自动关闭
                            # 异常退出(退出码非0, 如重启失败)的保留标签页便于看报错
                            self.nb.forget(tab['frame'])
                            del self.tabs[hwnd]
        except Exception as e:
            print('日志线程异常: %r' % e)
        finally:
            self.root.after(200, self.poll_log)

    # ---------- 日志框只读/复制 ----------
    def log_readonly_key(self, event):
        if event.state & 0x0004 and event.keysym in ('c', 'a'):
            return None
        return 'break'

    def log_select_all_widget(self, txt):
        txt.tag_add('sel', '1.0', 'end')
        return 'break'

    def widget_copy(self, txt):
        try:
            text = txt.get('sel.first', 'sel.last')
        except Exception:
            text = ''
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)



class _WorkerProcMixin(object):
    # ---------- 启动/停止 ----------
    def script_cmd(self, mode, hwnd=0):
        """打包后用--worker参数把自己拉起为脚本子进程; 源码运行则用python跑YBXZ.py"""
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, '--worker', mode]
        else:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'YBXZ.py')
            cmd = [sys.executable, script, mode]
        if hwnd:
            cmd += ['--hwnd', str(hwnd)]
        lnk = self.win_lnk.get(hwnd)
        if lnk:
            cmd += ['--lnk', lnk]
        return cmd

    def is_running(self, hwnd):
        tab = self.tabs.get(hwnd)
        return (tab is not None and tab['proc'] is not None
                and tab['proc'].poll() is None)

    def card_toggle(self, hwnd):
        """卡片启动/停止按钮; 操作后自动切到该窗口的日志标签页"""
        running = self.is_running(hwnd)
        if running:
            self.stop_one(hwnd)
        else:
            card = self.cards.get(hwnd)
            mode_id = MODE_ID[card['mode_var'].get()]
            if self._spawn(hwnd, mode_id):
                card['status'].configure(text='运行中: ' + MODE_NAME[mode_id])
                card['run_btn'].configure(text='停止')
        tab = self.tabs.get(hwnd)
        if tab is not None:
            self.nb.select(tab['frame'])

    def start_all(self):
        """启动全部未运行的窗口(按各自卡片上的功能); 没有窗口则自动启动游戏"""
        if not self.cards:
            self.launch_game_clicked()
            return
        for hwnd in list(self.cards.keys()):
            if self.is_running(hwnd):
                continue
            self.card_toggle(hwnd)

    def _spawn(self, hwnd, mode):
        """启动(或重启)某个窗口的挂机进程; hwnd=0表示自动查找/启动游戏"""
        if self.is_running(hwnd):
            return True  # 已在运行
        short = SHORT_NAME[mode]
        num = self.cards.get(hwnd, {}).get('num', 0)
        base = ('窗口%d' % num) if num else ('游戏' if hwnd == 0 else '窗口#%s' % hwnd)
        title = '%s·%s' % (base, short)
        tab = self.tabs.get(hwnd)
        if tab is None:
            tab = self.make_tab(hwnd, title, mode)
        else:
            tab['mode'] = mode
            tab['title'] = title
            tab['status'].configure(text='运行中')
            self.nb.tab(tab['frame'], text=tab['title'])
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        try:
            proc = subprocess.Popen(self.script_cmd(mode, hwnd),
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    creationflags=creationflags,
                                    env=env,
                                    bufsize=1)
        except Exception as e:
            tab['status'].configure(text='启动失败')
            self.tab_log(hwnd, '启动失败: %s\n' % e)
            return False
        tab['proc'] = proc
        tab['status'].configure(text='运行中')
        self.refresh_tab_btn(tab)  # 按钮文字切回'停止本窗口'
        threading.Thread(target=self.read_output, args=(proc, hwnd), daemon=True).start()
        self.tab_log(hwnd, '=== 启动 %s ===\n' % MODE_NAME.get(mode, mode))
        return True

    def read_output(self, proc, hwnd):
        tab = self.tabs.get(hwnd)
        for line in iter(proc.stdout.readline, b''):
            text = line.decode('gbk', errors='replace').rstrip()
            if text and tab is not None:
                tab['queue'].put(text)
        if tab is not None:
            tab['queue'].put('=== 进程已退出 ===')

    def stop_one(self, hwnd):
        tab = self.tabs.get(hwnd)
        if tab is None:
            return
        if tab['proc'] is not None and tab['proc'].poll() is None:
            # onefile有父子两层, 必须taskkill连树强杀
            subprocess.call(['taskkill', '/F', '/T', '/PID', str(tab['proc'].pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                tab['proc'].wait(timeout=3)
            except Exception:
                pass
            self.tab_log(hwnd, '=== 已停止 (PID %s) ===\n' % tab['proc'].pid)
        tab['proc'] = None
        tab['status'].configure(text='已停止')
        self.nb.tab(tab['frame'], text=tab['title'] + '(已停止)')
        self.sync_cards()

    def stop_all(self):
        for hwnd in list(self.tabs.keys()):
            self.stop_one(hwnd)

    def close_tab(self, hwnd):
        tab = self.tabs.get(hwnd)
        if tab is None:
            return
        if tab['proc'] is not None and tab['proc'].poll() is None:
            # 任务运行中: 弹确认防手滑误杀(×就在标签页边上, 容易误触)
            if not mb.askyesno('提示', '该窗口任务正在运行, 确定停止并关闭此日志页?'):
                return
            subprocess.call(['taskkill', '/F', '/T', '/PID', str(tab['proc'].pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.nb.forget(tab['frame'])
        del self.tabs[hwnd]
        self.sync_cards()  # 立刻刷新右侧卡片, 不等5秒自动刷新

    def on_close(self):
        self.stop_all()
        self.root.destroy()


class _UpdaterMixin(object):
    # ===== 在线更新 =====
    def _update_busy_begin(self):
        """更新流程互斥锁: 静默弹窗/手动检查/下载进行中互相排斥, 防双窗口双进度条"""
        if getattr(self, '_update_busy', False):
            return False
        self._update_busy = True
        try:
            self.update_btn.configure(state='disabled')
        except Exception:
            pass
        return True

    def _update_busy_end(self):
        self._update_busy = False
        try:
            self.update_btn.configure(state='normal', text='检查更新')
        except Exception:
            pass

    def check_update_clicked(self):
        if not getattr(sys, 'frozen', False):
            mb.showinfo('检查更新', '源码运行模式不支持在线更新')
            return
        if getattr(self, '_update_busy', False):
            mb.showinfo('检查更新', '更新流程进行中, 请留意已有的更新窗口')
            return
        self.update_btn.configure(state='disabled', text='检查中...')

        def work():
            info = fetch_remote_version()

            def done():
                self.update_btn.configure(state='normal', text='检查更新')
                if not info:
                    mb.showinfo('检查更新', '检查失败(网络不通或更新源不可达)')
                elif int(info.get('ver', 0)) <= int(APP_VER):
                    mb.showinfo('检查更新', '已是最新版本(%s)' % APP_VER)
                elif getattr(self, '_update_busy', False):  # 检查期间静默弹窗刚好出现
                    mb.showinfo('检查更新', '更新流程进行中, 请留意已有的更新窗口')
                else:
                    self._update_confirm(info)
            self.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def silent_update_check(self):
        """启动2.5秒后静默检查: 失败不打扰; 用户忽略过的版本不再主动弹窗;
        已有更新流程进行中时不弹(防双窗口)"""
        def work():
            info = fetch_remote_version()
            if info and int(info.get('ver', 0)) > int(APP_VER) \
                    and str(info.get('ver')) != _skipped_ver() \
                    and not getattr(self, '_update_busy', False):
                self.root.after(0, lambda: self._update_confirm(info))
        threading.Thread(target=work, daemon=True).start()

    def _update_confirm(self, info):
        if not self._update_busy_begin():  # 已有更新窗口(静默/手动/下载中), 不再弹第二个
            return
        # 列出本机版本之后每个版本的更新内容(version.json的history由发版脚本累积)
        lines = []
        for e in info.get('history') or [info]:
            try:
                if int(e.get('ver', 0)) > int(APP_VER):
                    lines.append('%s  %s' % (e.get('ver'), e.get('notes') or '-'))
            except (TypeError, ValueError):
                continue
        if not lines:
            lines.append('%s  %s' % (info.get('ver'), info.get('notes') or '-'))
        if mb.askyesno('发现新版本', '最新版本: %s\n当前版本: %s\n\n更新内容:\n%s\n\n是否现在更新?\n(下载约70MB, 完成后自动重启)'
                       % (info.get('ver'), APP_VER, '\n'.join(lines))):
            self._update_download(info)  # busy保持到下载结束(完成即退出/失败自动恢复)
        else:
            _set_skipped_ver(info.get('ver'))  # 忽略: 此版本不再主动弹窗(手动检查仍可用)
            self._update_busy_end()

    def _update_download(self, info):

        def topmost_mb(func, title, msg):
            # 客户机游戏窗口常全屏, 弹窗必须置顶否则看不见
            self.root.attributes('-topmost', True)
            try:
                return func(title, msg)
            finally:
                self.root.attributes('-topmost', False)

        win = tk.Toplevel(self.root)
        win.title('正在更新')
        win.geometry('380x140+%d+%d' % (self.root.winfo_rootx() + 300,
                                        self.root.winfo_rooty() + 250))
        win.grab_set()
        cancel_evt = threading.Event()

        cancel_btn_holder = {}

        def on_close():
            # 立即反馈: 取消按钮置灰+提示, 下载线程中止后窗口自动关闭
            if cancel_evt.is_set():
                return
            cancel_evt.set()
            try:
                cancel_btn_holder['btn'].configure(state='disabled')
                lbl.configure(text='正在取消...')
            except Exception:
                pass

        win.protocol('WM_DELETE_WINDOW', on_close)
        ttk.Label(win, text='正在下载新版本...').pack(pady=(14, 4))
        bar = ttk.Progressbar(win, length=330, maximum=100)
        bar.pack(pady=4)
        lbl = ttk.Label(win, text='正在连接更新源...')
        lbl.pack()
        cancel_btn_holder['btn'] = ttk.Button(win, text='取消更新', command=on_close)
        cancel_btn_holder['btn'].pack(pady=(4, 6))

        def progress(done, total):
            # 首包前(连接+签名跳转约3~7秒)没有字节回报, 界面显示"连接中"而非干瞪0%
            pct = min(99, int(done * 100 / total)) if total else 0
            self.root.after(0, lambda p=pct, m=done: (bar.configure(value=p),
                                              lbl.configure(text='%d%%  %.1fMB' % (p, m / 1048576.0))))

        def work():
            try:
                tmp = os.path.join(_data_dir(), '佣兵小镇一键版.new.exe')
                # 网络共享/UNC路径上运行中的exe禁止自我替换(SMB共享语义),
                # 提前拦截并指引放到本地磁盘(实测虚拟机共享目录更新必失败)
                if sys.executable.startswith('\\\\'):
                    self.root.after(0, lambda: topmost_mb(
                        mb.showwarning, '无法在当前位置更新',
                        '程序正在网络共享目录中运行, 无法自动更新。\n'
                        '请先将本程序复制到本地磁盘(如桌面)后再运行更新。'))
                    return
                _ulog('开始下载: ver=%s, %d分片, 目标%s' % (
                    info.get('ver'), len(info.get('chunks') or []), tmp))
                download_exe(info, tmp, progress, cancel_evt)
                md5_ok = _md5_file(tmp) == info.get('md5')
                _ulog('下载完成: md5校验%s' % ('通过' if md5_ok else '失败'))

                def finish():
                    win.destroy()
                    if not md5_ok:
                        topmost_mb(mb.showerror, '更新失败', '文件校验失败, 请重新检查更新重试')
                        return
                    try:
                        new_exe = apply_update(tmp, info.get(
                            'exe_name') or os.path.basename(sys.executable))
                    except Exception as e:
                        _ulog('apply_update失败: %r' % e)
                        topmost_mb(mb.showerror, '更新失败',
                                   '文件替换失败(%s)。\n请关闭本程序后, 手动将update_data里的'
                                   '佣兵小镇一键版.new.exe改名替换旧程序。' % e)
                        return
                    topmost_mb(mb.showinfo, '更新完成',
                               '新版本(%s)已就位, 程序即将自动重启' % info.get('ver'))
                    # 拉起新版(统一命名)再退出; 运行中的worker是独立进程, 新UI会自动重新认领
                    # 打包版无控制台, 重启过程写update_log.txt便于远程排查
                    _ulog('restart %s -> %s begin, new=%s' % (APP_VER, info.get('ver'), new_exe))
                    try:
                        subprocess.Popen([new_exe])
                        _ulog('new process launched')
                    except Exception as e:
                        _ulog('launch failed: %r' % e)
                        topmost_mb(mb.showerror, '更新失败',
                                   '新版本已就位, 但自动重启失败(%s), 请手动打开程序' % e)
                        return
                    try:
                        self.root.destroy()
                    except Exception as e:
                        _ulog('destroy failed: %r' % e)
                    _ulog('old process exit')
                    os._exit(0)
                self.root.after(0, finish)
            except _UpdateCancelled:
                _ulog('用户取消更新, 已清理下载临时文件')

                def cancelled():
                    win.destroy()
                    try:
                        os.remove(os.path.join(_data_dir(), '佣兵小镇一键版.new.exe'))
                    except OSError:
                        pass
                    _cleanup_update_parts(tmp)  # 断点分片一并清理(用户主动取消)
                    self._update_busy_end()
                self.root.after(0, cancelled)
            except Exception as e:
                _ulog('更新下载失败: %r' % e)

                def fail():
                    win.destroy()
                    try:
                        os.remove(os.path.join(_data_dir(), '佣兵小镇一键版.new.exe'))
                    except OSError:
                        pass
                    # 注意: 失败不清断点分片, 重试时续传; 按钮状态必须恢复
                    self._update_busy_end()
                    topmost_mb(mb.showerror, '更新失败',
                               '下载失败: %s\n可稍后点"检查更新"重试' % e)
                self.root.after(0, fail)
        threading.Thread(target=work, daemon=True).start()


class App(_CardPanelMixin, _LogTabsMixin, _WorkerProcMixin, _UpdaterMixin):
    def __init__(self, root):
        self.root = root
        # 清理上次异常退出遗留的断网重启标记(标记只在worker活着时有意义)
        try:
            for n in os.listdir(_data_dir()):
                if n.startswith('ybxz_restarting_'):
                    try:
                        os.remove(os.path.join(_data_dir(), n))
                    except OSError:
                        pass
        except OSError:
            pass
        tag_txt = ' '.join(x for x in (BUILD_TAG, 'Win7版' if _IS_WIN7 else '') if x)
        root.title('佣兵小镇挂机 %s v%s' % (tag_txt, APP_VER) if tag_txt
                   else '佣兵小镇挂机 v%s' % APP_VER)
        # 窗口尺寸按系统自动适配: 屏幕小则缩(实测Win7 1024宽也能放下);
        # Win7经典主题字体更宽, 980宽顶栏溢出(实测检查更新被裁掉), 自动加宽
        win_w = min(980, max(760, root.winfo_screenwidth() - 40))
        if _IS_WIN7:
            win_w = min(1240, max(win_w, root.winfo_screenwidth() - 40))
        win_h = min(640, max(500, root.winfo_screenheight() - 120))
        root.geometry('%dx%d+%d+%d' % (win_w, win_h,
                                       (root.winfo_screenwidth() - win_w) // 2,
                                       (root.winfo_screenheight() - win_h) // 2))
        self.tabs = {}   # hwnd -> 标签页数据(日志)
        self.cards = {}  # hwnd -> 卡片数据(右侧窗口列表)
        self.win_num = {}  # hwnd -> 窗口编号(终身制, 首次发现时分配)
        self.win_lnk = {}  # hwnd -> 各窗口的重启快捷方式路径(多账号分别重启)
        self._preview_img = None

        # ===== 顶部: 默认功能 + 全局按钮 =====
        top = ttk.Frame(root)
        top.pack(fill='x', padx=8, pady=6)
        ttk.Label(top, text='新窗口默认功能:').pack(side='left')
        self.mode = tk.StringVar(value='6')
        # 改默认功能会同步到所有未启动的卡片(运行中的不受影响)
        for val, text in (('4', '远征刷眩晕'), ('5', '精灵秘境挂机'),
                          ('6', '地下城挂机'), ('7', '网络监控重启')):
            ttk.Radiobutton(top, text=text, variable=self.mode, value=val,
                            command=self.apply_default_to_cards).pack(side='left', padx=3)
        self.start_all_btn = ttk.Button(top, text='启动全部', command=self.start_all)
        self.start_all_btn.pack(side='left', padx=(18, 6))
        self.stop_all_btn = ttk.Button(top, text='停止全部', command=self.stop_all)
        self.stop_all_btn.pack(side='left', padx=6)
        self.clear_btn = ttk.Button(top, text='清空当前页日志', command=self.clear_log)
        self.clear_btn.pack(side='left', padx=6)
        # 检查更新从右侧停靠: Win7默认字体更宽, 顶栏一行会溢出窗口导致按钮被裁掉(实测)
        self.update_btn = ttk.Button(top, text='检查更新', command=self.check_update_clicked)
        self.update_btn.pack(side='right', padx=(6, 8))
        if BUILD_TAG:
            ttk.Label(top, text=BUILD_TAG, foreground='red').pack(side='right', padx=(0, 6))
        # 打包版: 启动后静默检查更新; 顺手清掉上次更新遗留的旧exe备份
        # (客户exe名五花八门: 微信(1)(2)/浏览器重复下载/手动改名, 扫目录统一清;
        #  被残留进程占用的删不掉, 30秒/120秒后再各试一次)
        if getattr(sys, 'frozen', False):
            _ulog('程序启动 ver=%s' % APP_VER)
            _cleanup_old_backups('(启动)')
            root.after(10000, lambda: _cleanup_old_backups('(10秒)'))
            root.after(30000, lambda: _cleanup_old_backups('(30秒)'))

            def recurring_cleanup():
                # 常态化60秒一轮: 挂机worker退出后, 遗留备份1分钟内必被清掉;
                # 杀软拦截导致连续3次删不掉的, 弹一次窗指引手动删除(每次运行每文件只弹一次)
                for n in _cleanup_old_backups('(定期)', quiet=True):
                    if n not in _manual_prompted:
                        _manual_prompted.add(n)
                        _ulog('弹窗指引手动删除: %s' % n)
                        mb.showwarning('旧文件需要手动删除',
                                       '旧版本残留文件无法自动删除(被杀毒软件拦截)。\n'
                                       '文件: %s\n\n请在方便时手动删除该文件'
                                       '(如杀毒软件弹出提示, 请选择"允许")。' % n)
                root.after(60000, recurring_cleanup)
            root.after(60000, recurring_cleanup)
            # 检查在后台线程, 不挡界面, 0.6秒就启动(弹窗=0.6秒+拉取约2秒)
            root.after(600, self.silent_update_check)

        # ===== 主体: 左日志标签页 / 右窗口卡片列表 =====
        body = ttk.Frame(root)
        body.pack(fill='both', expand=True, padx=8)

        log_side = ttk.Frame(body)
        log_side.pack(side='left', fill='both', expand=True)
        style = ttk.Style()
        style.configure('TNotebook.Tab', font=('Microsoft YaHei', 9), padding=(8, 3))
        self.nb = ttk.Notebook(log_side)
        self.nb.pack(fill='both', expand=True)
        ttk.Label(log_side, text='后台挂机: 游戏窗口可被遮挡/移动, 但不要最小化').pack(
            anchor='w', pady=(4, 0))

        right = ttk.Frame(body)
        right.pack(side='right', fill='y', padx=(8, 0))
        head = ttk.Frame(right)
        head.pack(fill='x')
        ttk.Label(head, text='窗口列表').pack(side='left')
        ttk.Button(right, text='刷新', command=self.sync_cards).pack(fill='x', pady=(4, 0))
        self.jump_hwnds = {}
        self.jump_var = tk.StringVar()
        self.jump_box = ttk.Combobox(right, textvariable=self.jump_var, state='readonly')
        self.jump_box.pack(fill='x', pady=(4, 0))
        self.jump_box.bind('<<ComboboxSelected>>', self.jump_to_card)
        self.launch_game_btn = ttk.Button(right, text='启动游戏并登录',
                                          command=self.launch_game_clicked)
        self.launch_game_btn.pack(fill='x', pady=(4, 4))

        # 卡片滚动区: 滚动条必须在画布之前pack(否则画布占满整行, 滚动条被挤没了)
        listwrap = ttk.Frame(right)
        listwrap.pack(fill='both', expand=True)
        self.card_canvas = tk.Canvas(listwrap, width=320, highlightthickness=0)
        scroll = ttk.Scrollbar(listwrap, orient='vertical', command=self.card_canvas.yview)
        self.card_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.card_canvas.pack(side='left', fill='both', expand=True)
        self.card_inner = ttk.Frame(self.card_canvas)
        self.card_canvas.create_window((0, 0), window=self.card_inner, anchor='nw',
                                       tags='inner')
        self.card_inner.bind('<Configure>', lambda e: self.card_canvas.configure(
            scrollregion=self.card_canvas.bbox('inner')))
        self.card_canvas.bind('<Configure>', lambda e: self.card_canvas.itemconfigure(
            'inner', width=e.width))
        self.card_canvas.bind_all('<MouseWheel>', self.on_card_wheel)
        ttk.Label(right, text='没发现窗口? 点[启动游戏并登录]').pack(anchor='w')

        self.poll_log()  # 启动日志刷新循环(必须, 否则worker日志不显示)
        self.sync_cards()
        self.auto_refresh()  # 定时自动刷新卡片
        root.protocol('WM_DELETE_WINDOW', self.on_close)

def menu_popup(menu, event):
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
    return 'break'


def run_worker(mode, hwnd=0, lnk=''):
    """子进程模式: 在本进程内直接运行YBXZ脚本, 任何退出路径都留痕"""
    import YBXZ
    import traceback
    try:
        YBXZ.init_dm()
        # 显式参数传入(不再跨模块改YBXZ全局变量), 本worker的重启快捷方式多账号各用各的
        YBXZ.main(action=mode,
                  fixed_hwnd=int(hwnd) if hwnd else None,
                  game_lnk=lnk or None)
        print('=== main()异常返回, 进程结束 ===')
    except BaseException:
        tb = traceback.format_exc()
        print(tb)
        try:
            log = os.path.join(_data_dir(), 'crash.log')
            with open(log, 'a', encoding='utf-8') as f:
                f.write(tb + '\n')
        except Exception:
            pass
        os._exit(1)


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--worker':
        try:
            sys.stdout.reconfigure(line_buffering=True)
            sys.stderr.reconfigure(line_buffering=True)
        except Exception:
            try:
                import io
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='gbk',
                                              errors='replace', line_buffering=True)
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='gbk',
                                              errors='replace', line_buffering=True)
            except Exception:
                pass
        sys.stdout = SafeStdout(sys.stdout)
        sys.stderr = SafeStdout(sys.stderr)
        import faulthandler
        faulthandler.enable()
        hwnd = 0
        if '--hwnd' in sys.argv:
            hwnd = sys.argv[sys.argv.index('--hwnd') + 1]
        lnk = ''
        if '--lnk' in sys.argv:
            lnk = sys.argv[sys.argv.index('--lnk') + 1]
        run_worker(sys.argv[2], hwnd, lnk)
    else:
        root = tk.Tk()
        App(root)
        root.mainloop()
