# -*- coding: utf-8 -*-
"""在线更新子系统(从YBXZ_UI.py整体平移, 2026-10-28结构优化):
版本检查/分片下载/exe替换/备份清理。纯函数+模块级状态, 不依赖tkinter,
仅被YBXZ_UI import; APP_VER留在YBXZ_UI.py(publish_release.py按一行式
正则替换它, 抽走会破坏发版流程)。"""
import ctypes
import glob
import os
import subprocess
import sys
import time

from ybxz_common import data_dir

GITEE_REPO = 'li-bin970228/ybxz'  # 更新源: gitee公开仓库(纯国内, 不用github)
GITEE_BRANCH = 'master'
VER_URL_GITEE = 'https://gitee.com/%s/raw/%s/version.json' % (GITEE_REPO, GITEE_BRANCH)
CHUNK_URL_GITEE = 'https://gitee.com/%s/releases/download/%%s/%%s' % GITEE_REPO
VER_URL_GITEE_API = 'https://gitee.com/api/v5/repos/%s/contents/version.json?ref=%s' % (
    GITEE_REPO, GITEE_BRANCH)
GITEE_API_BASE = 'https://gitee.com/api/v5/repos/%s' % GITEE_REPO

_update_opener = None


class _UpdateCancelled(Exception):
    """用户在进度窗口点了取消"""


class SafeStdout(object):
    """stdout安全封装: 管道编码统一为UTF-8(见UI._spawn), 万一遇到编不了的
    字符直接print会UnicodeEncodeError崩溃, 这里替换后重写"""


    def __init__(self, target):
        self.target = target

    def write(self, s):
        try:
            return self.target.write(s)
        except UnicodeEncodeError:
            return self.target.write(s.encode('utf-8', 'replace').decode('utf-8'))

    def flush(self):
        try:
            self.target.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self.target, name)


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
                        json_loads_utf8(data)['content'])
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


def json_loads_utf8(data):
    import json
    return json.loads(data.decode('utf-8'))


def download_exe(info, tmp_path, progress_cb, cancel_evt=None):
    """下载新版exe到tmp_path并校验md5。cancel_evt置位=用户取消(抛_UpdateCancelled)。
    压缩分片3路并发下载(实测gitee单连接1.4MB/s, 并发提速2倍+);
    每片完成即落盘(md5校验过的才留), 失败重试时跳过已完成片=断点续传;
    按序重组 -> 解压 -> 整体校验; 进度按完成字节数跨线程聚合"""
    import hashlib
    import threading
    import zlib
    from concurrent.futures import ThreadPoolExecutor
    chunks = info['chunks']
    tag = info.get('tag', '')
    total = sum(c['size'] for c in chunks)
    parts = [None] * len(chunks)
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

    with ThreadPoolExecutor(max_workers=3) as ex:
        # map迭代会把worker异常抛到这里, 由调用方提示
        list(ex.map(worker, range(len(chunks)), chunks))
    raw = zlib.decompress(b''.join(parts))
    if hashlib.md5(raw).hexdigest() != info.get('md5'):
        # 校验失败保留断点文件: 用户重试时已完成的分片直接复用(断点续传)
        raise RuntimeError('校验失败(下载不完整), 请重试')
    with open(tmp_path, 'wb') as f:
        f.write(raw)
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
    dd = data_dir()
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


def _ulog(msg):
    """更新过程日志(打包版无控制台, update_log.txt是远程排查唯一线索)"""
    try:
        f = open(os.path.join(data_dir(), 'update_log.txt'), 'a')
        f.write('%s %s\n' % (time.strftime('%m-%d %H:%M:%S'), msg))
        f.close()
    except Exception:
        pass


SKIP_VER_FILE = os.path.join(data_dir(), 'update_skip.txt')


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


def _cleanup_update_parts(tmp_path):
    """取消更新后清理全部断点分片文件"""
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
    scan_dirs = [(data_dir(), False), (_exe_dir(), True)]
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
