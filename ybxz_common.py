# -*- coding: utf-8 -*-
"""UI与worker共享的零副作用工具(只依赖标准库, 双进程都可安全import;
解决历史上"两文件各写一份、修一处漏一处"的问题: 150%缩放截图修复当年
就是UI的capture_hwnd和worker的_pw_capture_arr各修了一遍)"""
import ctypes
import os
import sys
from ctypes import wintypes

# 断网重启标记文件名前缀(UI与worker的字符串协议, 收敛到一处防止改漏)
RESTART_MARKER_PREFIX = 'ybxz_restarting_'


def _exe_dir():
    """程序所在目录: 打包版=exe旁, 源码模式=当前目录"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath('.')


def data_dir():
    """update_data 目录(更新日志/共享帧/备份/重启标记等衍生文件的统一收纳处):
    exe旁(打包)或当前目录(源码); 创建失败(如只读位置)退回exe旁。
    (原UI版带退回逻辑, 原worker版不带——统一采用UI版语义, 对worker只是
    极罕见只读目录下写文件的位置变化, 且worker的写全有try兜底)"""
    d = os.path.join(_exe_dir(), 'update_data')
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = _exe_dir()
    return d


def restart_marker_path(hwnd):
    """断网重启标记文件路径(UI靠它区分'重启中要保留日志页'和'窗口没了照旧清理')"""
    return os.path.join(data_dir(), '%s%d.flag' % (RESTART_MARKER_PREFIX, hwnd or 0))


def client_phys_size(user32, hwnd):
    """返回(逻辑w, h, 物理pw, ph): 非基准缩放下DPI虚拟化使逻辑尺寸≠物理尺寸,
    PrintWindow按物理渲染, 截图缓冲必须按物理客户区开, 再缩回逻辑尺寸,
    否则截图被裁掉一块(实测150%缩放)。新增截图代码必须经由本探测。"""
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    pw, ph = w, h
    if hasattr(user32, 'SetThreadDpiAwarenessContext'):
        old_ctx = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PMv2
        if old_ctx:
            pr = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(pr))
            user32.SetThreadDpiAwarenessContext(old_ctx)
            if pr.right > 0 and pr.bottom > 0:
                pw, ph = pr.right, pr.bottom
    return w, h, pw, ph


class BMIH(ctypes.Structure):
    """BITMAPINFOHEADER: PrintWindow截图GetDIBits取像素用(UI缩略图与worker识别共用)"""
    _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
                ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
                ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
                ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
                ('biClrImportant', ctypes.c_uint32)]


def desktop_dirs():
    """桌面目录候选(兼容OneDrive重定向/公共桌面), 佣兵lnk枚举用"""
    home = os.path.expanduser('~')
    return [os.path.join(home, 'Desktop'),
            os.path.join(home, 'OneDrive', 'Desktop'),
            os.path.join(os.environ.get('ONEDRIVE', '') or home, 'Desktop'),
            os.path.join(os.environ.get('PUBLIC', ''), 'Desktop')]
