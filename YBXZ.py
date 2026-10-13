#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2026/1/9
# @Author  : lb
# @Github  : https://github.com/Mr-LB1/CYXYAUTOSCRIPT
# @Software: PyCharm
# @File    : YBXZ.py
import ctypes
import io
import os
import re
import subprocess
import sys
import tempfile
import winreg
import threading
from ctypes import wintypes
from time import sleep
import time

import cv2
import numpy as np
from PIL import Image
from win32com.client.dynamic import Dispatch

# ================== 通用配置 ==================
GAME_LNK = '佣兵小镇.lnk'  # 桌面快捷方式文件名, 启动游戏用(模拟双击)
GAME_WINDOW_TITLE = '佣兵小镇'
CLIENT_SIZE = (451, 845)  # 游戏窗口内容区大小, 截图坐标不能超过它(超出会截到别的窗口)
dpi_scale = 96            # 系统缩放(96=100%), init_dm里按注册表检测
_fail_streak = 0         # 一队失败标记的连续命中次数(防聊天文字误判)
_fail_pos = None
_fail_t = 0


def _system_scaling():
    """读当前系统缩放(96=100%, 168=175%...)
    优先: PMv2线程上下文直读真实DPI(进程无需感知, 实测175%读到168);
    兜底: 旧版注册表LogPixels(按显示器缩放的机器上这个值不存在)"""
    u32 = ctypes.windll.user32
    if hasattr(u32, 'SetThreadDpiAwarenessContext'):
        old = u32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PMv2
        if old:
            dpi = u32.GetDpiForSystem()
            u32.SetThreadDpiAwarenessContext(old)
            if dpi:
                return dpi
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Control Panel\Desktop')
        try:
            win8 = winreg.QueryValueEx(k, 'Win8DpiScaling')[0]
        except FileNotFoundError:
            win8 = 0
        try:
            lp = winreg.QueryValueEx(k, 'LogPixels')[0]
        except FileNotFoundError:
            lp = 96
        winreg.CloseKey(k)
        return lp if win8 else 96
    except Exception:
        return 96
FIXED_HWND = 0  # 多账号模式: 界面指定的游戏窗口句柄(0=单账号自动查找/启动)
OFFLINE_RECT = (87, 402, 376, 498)  # 掉线弹窗文字的识别区域
OFFLINE_TEXT = '网络连接断开重连'  # 弹窗文字包含这些字即判定掉线
LOGIN_CLICK = (151, 130)  # 重启后登录: 每隔1秒点这个坐标
LOGIN_OK_RECT = (121, 823, 169, 841)  # 登录成功判定: 该区域文字包含"地下城"
LOADING_RECT = (175, 622, 281, 647)  # 加载进度区域, 出现进度就停止登录点击
BLANK_CLICK = (176, 824)  # 结算遮挡页的固定空白点击位置


def _data_dir():
    """update_data 目录(更新日志/共享帧/备份等全部衍生文件的统一收纳处):
    打包版在 exe 旁, 源码模式在当前工作目录; 目录不存在则创建。
    (与 YBXZ_UI._data_dir 语义一致但故意不共享: UI 进程 import 本模块
    会连带加载 win32com, 不值得为几行路径拼接引入这个依赖)"""
    base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
        else os.path.abspath('.')
    d = os.path.join(base, 'update_data')
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d

# ================== 地下城挂机(功能6)配置 ==================
# 一队失败判定色(红色X), RGB元组; 在PrintWindow截图上做像素匹配(后台可判)
FAIL_COLORS = ((0xcb, 0x1d, 0x1d), (0xce, 0x1d, 0x1d))
FAIL_COLOR_OFFSET = 0  # 每个通道允许的偏色(0=精确匹配)
FAIL_COLOR_RECT = (25, 715, 155, 790)  # 队伍失败红X检测区域: 覆盖左下角3个队伍徽章槽位(容忍机器布局偏移)
TOWN_RECT = (110, 815, 180, 850)     # 城镇: 底栏"地下城"文字(判定顺序在battle锚点之后, 不会误判)
BATTLE_TIMER_RECT = (150, 240, 330, 470)  # 战斗计时器区域(不同账号布局高度不同, 用大区域)
BATTLE_STAGE_RECT = (140, 370, 320, 440)  # 战斗关卡横幅"36-50"区域(计时器单独不可信, 须与横幅同时在场)
BATTLE_STAGE_RE = re.compile(r'^\d{1,3}-\d{1,3}$')  # 关卡横幅: 纯"数字-数字"(36-50), 城镇绝无此格式
DUNGEON_RECT = (170, 95, 290, 150)  # 地下城界面: 第X章(加高兼容不同机器的界面偏移)
DEPART_RECT = (185, 705, 280, 750)   # 队伍出发按钮
BATTLE_RECT = (365, 715, 450, 790)  # 加高容错: 不同机器弃权按钮位置会下移约24px   # 弃权按钮(战斗中标志)
CONFIRM_RECT = (275, 532, 365, 570)  # 确定(弃权后弹出)
PASS_RECT = (157, 698, 306, 747)     # 地下城通过确定
BLANK_RECT = (165, 762, 305, 800)    # 点击空白
AGAIN_RECT = (275, 695, 415, 745)    # 再次挑战
DUNGEON_ENTRY_CLICK = (223, 754)     # 地下城界面"进入战斗准备"的固定点击坐标
BATTLE_TIMEOUT = 300  # 战斗最长等待秒数

# ================== 英雄远征刷眩晕(功能4)配置 ==================
EXP_TITLE_RECT = (174, 99, 282, 132)     # "英雄远征"标题(远征界面标志)
EXP_SELECTED_RECT = (189, 730, 267, 757)  # "开始远征"按钮(已选层标志)
EXP_LEVEL_RECT = (184, 112, 267, 144)    # "第XX层"(层数关卡界面标志)
EXP_LEVEL_CLICK = (287, 605)             # 层数关卡界面的点击坐标
EXP_SUB_SEARCH = (0, 0, 451, 845)        # "前往"用全屏检测(别改小!)
# 实测: 检测模型对短边<736的图会放大, 图越小放大越狠, 深色按钮上的小字直接糊掉
# (全屏451x845只放大1.6倍每次都认出; 半屏/小框放大2.6~8倍必miss)
EXP_SUB_MIN_Y = 400                      # 只认y>400的前往, 防点到页面其他位置的同名文字
EXP_CHALLENGE_RECT = (190, 705, 267, 731)  # "开始挑战"按钮
EXP_FIGHT_RECT = (195, 789, 255, 817)    # "弃权"(战斗中标志)
EXP_BONUS_RECT = (155, 250, 292, 289)    # "选择加成"
EXP_STUN_RECT = (33, 464, 419, 514)      # 奖励技能区域(查眩晕)
EXP_LOSE_RECT = (80, 100, 380, 260)      # "战斗失败"标题(失败弹提升战力页, 无选择加成)
EXP_BONUS_CLICK = (226, 700)             # 加成页空白处(换一批下方); 原来的(223,239)点在横幅图案上关不掉页面
EXP_ABANDON_RECT = (377, 244, 413, 265)  # "放弃"按钮
EXP_CONFIRM_RECT = (281, 536, 342, 565)  # 放弃确认"确定"
EXP_SWIPE_X = 223                        # 列表滑动X坐标
EXP_SWIPE_Y = 650                        # 滑动起点Y(从下往上滑)
EXP_SWIPE_DISTANCE = 100                 # 拖拽距离: 实测100px恰好滚一行(50~200都是1行, 300起跳2行)
EXP_BATTLE_TIMEOUT = 600                 # 战斗最长等待秒数
EXP_SWIPE_MAX = 15                       # 连续滑动次数上限

# 页面偏移(锚点自校准): 地下城页面在部分机器上整体偏移, 用"第X章"标题实测
page_dx = 0
# 坐标自适应比例: 游戏窗口被微信强制最小尺寸(实测492x918)时, 截图缩放到
# 451x845标定尺寸, 点击坐标按实际/标定比值放大(bind_game里测量赋值)
coord_sx = 1.0
coord_sy = 1.0
page_dy = 0
page_calibrated = False
pos_cache = {}  # 状态 -> 上次成功点击的坐标(同会话复用, 避免重复慢速OCR)


def page_rect(rect):
    """套用页面偏移后的判定区域"""
    return (rect[0] + page_dx, rect[1] + page_dy, rect[2] + page_dx, rect[3] + page_dy)


def page_pt(pt):
    """套用页面偏移后的点击坐标"""
    return (pt[0] + page_dx, pt[1] + page_dy)


BATTLE_TIMER_RE = re.compile(r'^\d{1,3}[:：]\d{2}([:：]\d{2})?$')  # 纯时间: 03:54 / 0:03:54


def battle_timer_visible():
    """战斗计时器判定: 区域内出现"纯时间"文字(实测战斗计时器=03:54格式)。
    只认纯数字+冒号: 其他界面的活动倒计时都带后缀文字(08:34:01后开启/3天08:36:33后结束),
    旧的"含数字+冒号就认"会被它们误命中(实测: 关战斗页回town后误判battle到300秒超时)。
    注意: 城镇NPC气泡也有纯时间倒计时(金币税收/建筑, 实测04:18:30在检测区内),
    单独不可信, 必须与battle_stage_visible()同时在场才能判battle"""
    for t, s, cx, cy in paddle_find(*page_rect(BATTLE_TIMER_RECT), '时间', quick=True):
        if BATTLE_TIMER_RE.match(t.strip()):
            return True
    return False


def battle_stage_visible():
    """战斗关卡横幅判定: 顶部"36-50"格式的关卡数横幅, 战斗界面独有
    (城镇/地下城/远征界面都没有纯"数字-数字"横幅, 任务文字"通过地下城36-20"
    带前后缀, 全匹配正则不会命中)"""
    for t, s, cx, cy in paddle_find(*page_rect(BATTLE_STAGE_RECT), '关卡', quick=True):
        if BATTLE_STAGE_RE.match(t.strip()):
            return True
    return False


def battle_anchor_visible():
    """战斗锚点综合判定: 弃权按钮(最强) 或 计时器+关卡横幅同时在场(弃权被特效
    遮挡时的兜底, 实测城镇气泡纯时间会单独误报battle)"""
    if text_locate(page_rect(BATTLE_RECT), '弃权', quick=True) is not None:
        return True
    return battle_timer_visible() and battle_stage_visible()


def reset_page_calibration():
    global page_dx, page_dy, page_calibrated
    page_dx = page_dy = 0
    page_calibrated = False


def calibrate_page_offset():
    """以"第X章"标题为锚点校准页面偏移(标定机参考位置223,115)
    已校准返回False; 找到标题返回True并记录偏移; 未找到返回False"""
    global page_dx, page_dy, page_calibrated
    if page_calibrated:
        return False
    for t, s, cx, cy in paddle_find(0, 60, CLIENT_SIZE[0], 220, '第章'):
        if '第' in t and '章' in t and any(ch.isdigit() for ch in t):
            page_dx, page_dy = cx - 223, cy - 115
            page_calibrated = True
            print(time.strftime('%H:%M:%S'), '页面校准完成: 偏移 dx=%d dy=%d (标题实测 %d,%d)' % (
                page_dx, page_dy, cx, cy))
            return True
    return False


def dungeon_state():
    """地下城地图页判定: 首次出现时校准页面偏移, 再按偏移后的区域判定"""
    calibrate_page_offset()
    return text_locate(page_rect(DUNGEON_RECT), '第章', quick=True) is not None


# 状态判定表: 状态名 -> 该状态的特征文字检查
# (dungeon/battle/confirm/pass/blank/again/depart属于地下城页面族, 判定时套用页面偏移;
#  town是城镇页面, 实测不受页面偏移影响, 用原始区域; 全部quick=True只做识别不精定位)
STATE_CHECK = {
    'town': lambda: text_locate(TOWN_RECT, '地下城', quick=True) is not None,
    'dungeon': dungeon_state,
    'depart': lambda: text_locate(page_rect(DEPART_RECT), '队伍出发', quick=True) is not None,
    'battle': battle_anchor_visible,
    'confirm': lambda: text_locate(page_rect(CONFIRM_RECT), '确定', quick=True) is not None,
    'pass': lambda: text_locate(page_rect(PASS_RECT), '确定', quick=True) is not None,
    'blank': lambda: text_locate(page_rect(BLANK_RECT), '点击空白', quick=True) is not None,
    'again': lambda: text_locate(page_rect(AGAIN_RECT), '再次挑战', quick=True) is not None,
}
# 正常流程: 当前状态处理完 -> 预期出现的下一个状态
# town->dungeon->depart->battle; 战斗后分支(以全量重判实测为准):
#   失败: 一队失败->battle(弃权)->confirm(确定)->点击后全量重判(实测直接回town)
#   通关: pass->dungeon->depart
#   另有 blank->again->depart 的结算页路径, 各状态保留以兼容
FLOW_NEXT = {
    'town': 'dungeon', 'dungeon': 'depart', 'depart': 'battle',
    'battle': 'battle', 'confirm': 'blank', 'blank': 'again',
    'again': 'depart', 'pass': 'dungeon',
}

# ================== 精灵秘境挂机(功能5)配置 ==================
WANFA_RECT = (28, 818, 85, 850)  # 主界面"玩法"按钮文字区域
MIRE_TEXT = '精灵秘境'  # 玩法列表里的入口标签
MIRE_RECT = (0, 40, 110, 839)  # 玩法列表左侧标签区
CHALLENGE_RECT = (295, 570, 360, 612)  # 秘境界面"挑战"按钮文字区域
FIGHT_RECT = (165, 250, 300, 285)  # 战斗中"剩余时间"文字区域
GAIN_RECT = (179, 366, 299, 398)  # 挑战成功结算"获得物品"文字区域
GAIN_CLICK = (155, 294)  # 成功结算界面要点击的坐标
LOSE_RECT = (181, 109, 304, 147)  # 挑战失败弹窗"战斗失败"文字区域
MIRE_FAIL_MAX = 3  # 连续失败次数上限, 达到后停止脚本

# 精灵秘境单区域判定表: 每个界面只查自己的特征区域(顺序即判定顺序: 弹窗优先)
SCREEN_CHECK = {
    'gain': lambda: text_locate(GAIN_RECT, '获得物品') is not None,
    'lose': lambda: text_locate(LOSE_RECT, '战斗失败') is not None,
    'fight': lambda: elf_fighting(),
    'ready': lambda: text_locate(CHALLENGE_RECT, '挑战') is not None,
    'select': lambda: elf_find_mire() is not None,
    'main': lambda: text_locate(WANFA_RECT, '玩法') is not None,
}
# 正常流程的下一个界面: 当前界面处理完 -> 预期出现的界面(优先只查它, 省时间)
ELF_NEXT = {
    'main': 'select', 'select': 'ready', 'ready': 'fight',
    'fight': 'fight', 'gain': 'main', 'lose': 'main',
}


# ================== 资源路径与大漠初始化 ==================
def resource_path(relative_path):
    """
    获取资源文件在打包后的正确路径
    relative_path: 相对路径，例如 "auto.exe"
    """
    if getattr(sys, "_MEIPASS", False):
        # 打包后：PyInstaller 会把文件解压到 _MEIPASS 临时目录
        return os.path.join(sys._MEIPASS, relative_path)
    # 源码运行：直接在当前目录找
    return os.path.join(os.path.abspath("."), relative_path)


DM_REQUIRED_METHODS = ('Ver', 'SetPath', 'BindWindow', 'FindStr', 'MoveTo', 'LeftClick')


def dm_is_valid(dm):
    """校验拿到的dm对象是否可用: dm.dmsoft是全机共享ProgID, 别的脚本软件可能
    注册过旧版/残缺版, Dispatch会直接拿到它(实测客户机AttributeError: dm.dmsoft.SetPath)"""
    for m in DM_REQUIRED_METHODS:
        try:
            getattr(dm, m)
        except Exception:
            return False
    try:
        return bool(str(dm.Ver()).strip())
    except Exception:
        return False


def dm_reg():
    """
    获取大漠COM对象; 别人电脑第一次运行时dll未注册, 自动提权注册(会弹UAC授权框)
    已有注册但版本残缺时同样强制重注册覆盖
    :return:dm
    """
    try:
        dm = Dispatch('dm.dmsoft')
        if dm_is_valid(dm):
            return dm
        print(time.strftime('%H:%M:%S'),
              '电脑上已注册的dm.dmsoft版本残缺(缺关键方法), 重新注册随包dm.dll覆盖...')
    except Exception:
        pass
    # 未注册: 定位解压出来的dll
    dll = resource_path(os.path.join('damo', 'dm.dll'))
    if not os.path.exists(dll):
        raise RuntimeError('找不到dm.dll: %s' % dll)
    # 32位dll在64位系统上必须用SysWOW64下的32位regsvr32注册
    sysroot = os.environ.get('SystemRoot', r'C:\Windows')
    reg = os.path.join(sysroot, 'SysWOW64', 'regsvr32.exe')
    if not os.path.exists(reg):
        reg = os.path.join(sysroot, 'System32', 'regsvr32.exe')
    try:
        # runas提权: 写HKCR注册表需要管理员权限, 用户会看到UAC弹窗, 点"是"即可
        ret = ctypes.windll.shell32.ShellExecuteW(None, 'runas', reg,
                                                  u'/s "%s"' % dll, None, 0)
        if ret <= 32:
            print('注册dm.dll被取消(错误码%s), 请右键"以管理员身份运行"后重试' % ret)
    except Exception as e:
        print('提权注册dm.dll失败: %s, 请右键"以管理员身份运行"后重试' % e)
    # 注册是异步的, 轮询等待生效; 每次拿到对象都验货(防覆盖注册被跳过/失败)
    for _ in range(30):
        try:
            dm = Dispatch('dm.dmsoft')
            if dm_is_valid(dm):
                return dm
        except Exception:
            pass
        sleep(0.5)
    raise RuntimeError('dm.dll注册失败或版本残缺! 请右键"以管理员身份运行"本程序后重试')


class _DmScaled(object):
    """大漠COM代理: 仅拦截MoveTo——窗口实际内容区≠标定区451x845时(新版微信
    强制小程序窗口最小尺寸, 实测492x918), 截图已缩放到451x845,
    点击坐标按(实际宽/451, 实际高/845)反向放大; 其余属性/方法透传"""
    def __init__(self, dm):
        self._dm = dm

    def __getattr__(self, name):
        return getattr(self._dm, name)

    def MoveTo(self, x, y):
        self._dm.MoveTo(int(round(x * coord_sx)), int(round(y * coord_sy)))


def init_dm():
    """
    初始化大漠对象(必须在实际使用dm的进程里调用)
    只用找图/截图/点击, 不加载字库(字库是FindStr用的, 已改用PaddleOCR)
    """
    global dm, dm_path, dpi_scale
    dpi_scale = _system_scaling()
    if dpi_scale == 96 or os.environ.get('YBXZ_FORCE_UNAWARE'):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            # Win7没有shcore.dll(实测客户Win7旗舰版WinError 126崩溃), 退到Vista同款API
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    # 非100%缩放: 保持DPI不感知, Windows把窗口/截图/点击全部虚拟化到
    # 96dpi逻辑空间, 游戏布局与100%完全一致 -> 自适应任意缩放
    else:
        print(time.strftime('%H:%M:%S'),
              '系统缩放%d%%: 使用DPI虚拟化模式(窗口物理尺寸会按比例放大)' % (dpi_scale * 100 // 96))
    dm = dm_reg()
    dm = _DmScaled(dm)  # 坐标自适应代理: 窗口实际尺寸≠标定尺寸时MoveTo按比值放大
    dm_path = resource_path("damo")
    dm.SetPath(dm_path)
    try:
        print(time.strftime('%H:%M:%S'), 'dm.dll版本:', dm.Ver())
    except Exception:
        pass


# ================== 窗口绑定与截图 ==================
def find_render_child(hwnd):
    """找小程序内部渲染子窗口(Chrome_RenderWidgetHostHWND)"""
    user32 = ctypes.windll.user32
    result = [0]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(child, param):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(child, buf, 256)
        if 'RenderWidgetHost' in buf.value:
            result[0] = child
            return False
        return True

    user32.EnumChildWindows(hwnd, cb, None)
    return result[0]


bound_hwnd = 0  # bind_game实际绑定的窗口句柄(内部渲染子窗口), PrintWindow截图用
bound_top = 0   # 绑定的大窗口(小程序顶层)句柄, 多账号存活检查用
bound_child = 0  # 绑定的渲染子窗口句柄


def bind_game(hwnd):
    """
    后台绑定游戏窗口
    必须绑内部渲染子窗口(Chrome_RenderWidgetHostHWND): 顶层窗口的消息模拟会被
    小程序忽略(鼠标只能前台代管), 子窗口的消息能进渲染进程(鼠标可后台)
    """
    global bound_hwnd, bound_top, bound_child, coord_sx, coord_sy
    coord_sx = coord_sy = 1.0  # 每次绑定重置自适应比例
    bound_top = hwnd
    target = find_render_child(hwnd) or hwnd
    bound_child = target
    bound_hwnd = target
    # 只恢复默认大小(不移动/不置顶): OCR识别坐标按默认内容区451x845标定,
    # 窗口大小不一致会整体错位。
    # 外框尺寸不能写死: 边框/标题栏随显示缩放(DPI)和系统主题变化(自定义边框连
    # AdjustWindowRectEx都不准), 用"设→量内容区→按差值修外框"迭代校正, 任何机器都收敛
    # (实测: 别人机器点前往落在空白, 就是内容区不对导致整体错位)
    u32 = ctypes.windll.user32
    cr = wintypes.RECT()
    w, h = 467, 853  # 起点值(100%缩放机器的已知外框)
    for i in range(4):
        u32.SetWindowPos(bound_top, 0, 0, 0, w, h, 0x0002)  # SWP_NOMOVE
        u32.GetClientRect(bound_top, ctypes.byref(cr))
        dw, dh = 451 - cr.right, 845 - cr.bottom
        if abs(dw) <= 1 and abs(dh) <= 1:
            break  # ±1px容差: 高缩放下边框取整粒度粗, 1px偏差不影响布局
        w, h = w + dw, h + dh  # 内容区差多少, 外框就补多少
    else:
        # 非收敛多半是新版微信/QQ强制了小程序窗口的最小尺寸(实测492x918, 老版本无限制):
        # 缩不进去就反过来放大坐标系——截图缩放到451x845, 点击按比值放大
        cw, ch = cr.right, cr.bottom
        asp_a = float(cw) / ch
        asp_e = 451.0 / 845
        if abs(asp_a - asp_e) / asp_e > 0.06 or cw < 380 or cw > 720:
            # 比例偏差过大: 内容排版会变, 自适应不可靠, 仍然报错
            raise RuntimeError(
                '游戏内容区校正失败(%dx%d, 期望451x845)。窗口尺寸被微信强制且'
                '比例异常, 自适应坐标系不适用。请尝试: 游戏窗口还原成普通窗口'
                '(取消最大化/分屏)后重试; 或把显示缩放调回100%%(推荐1920x1080/100%%缩放)'
                % (cr.right, cr.bottom))
        coord_sx = float(cw) / 451
        coord_sy = float(ch) / 845
        _dm_cap_log('窗口尺寸%d x%d超出标定区451x845(微信强制最小尺寸), '
                    '启用坐标自适应: 缩放%.3f/%.3f' % (cw, ch, coord_sx, coord_sy))

    # ===== 基准环境检测: 窗口必须完整落在屏幕工作区内 =====
    # 所有坐标按451x845内容区标定, 与屏幕分辨率/缩放无关; 唯一硬性要求是
    # 屏幕工作区放得下467x853外框, 否则底部按钮在屏幕外/任务栏后, 真实点击会失败
    wr = wintypes.RECT()
    u32.GetWindowRect(bound_top, ctypes.byref(wr))
    # py3.7的wintypes没有MONITORINFO, 手动定义
    class _MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD),
                    ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT),
                    ('dwFlags', wintypes.DWORD)]
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    u32.GetMonitorInfoW(u32.MonitorFromWindow(bound_top, 2), ctypes.byref(mi))  # 最近显示器
    wa = mi.rcWork
    if (wr.left < wa.left or wr.top < wa.top
            or wr.right > wa.right or wr.bottom > wa.bottom):
        u32.SetWindowPos(bound_top, 0, wa.left, wa.top, 0, 0, 0x0001)  # SWP_NOSIZE: 只移动位置
        time.sleep(0.3)
    u32.GetWindowRect(bound_top, ctypes.byref(wr))
    ww, wh = wr.right - wr.left, wr.bottom - wr.top
    waw, wah = wa.right - wa.left, wa.bottom - wa.top
    mode = '感知' if (dpi_scale == 96 and not os.environ.get('YBXZ_FORCE_UNAWARE')) else '虚拟化'
    print(time.strftime('%H:%M:%S'),
          '环境: 内容区%dx%d, 窗口%dx%d@( %d,%d ), 工作区%dx%d, 系统缩放%d%%[%s]'
          % (cr.right, cr.bottom, ww, wh, wr.left, wr.top, waw, wah,
             dpi_scale * 100 // 96, mode))
    if waw < 467 or wah < 853 or wr.bottom > wa.bottom:
        scale_note = ('当前缩放%d%%下窗口物理尺寸约为%dx%d, '
                      % (dpi_scale * 100 // 96, ww * dpi_scale // 96, wh * dpi_scale // 96)
                      if mode == '虚拟化' else '')
        raise RuntimeError(
            '屏幕工作区%dx%d放不下游戏窗口(需467x853)。%s'
            '请把显示缩放调回100%%或降低分辨率; 也可以开启任务栏自动隐藏后重试'
            % (waw, wah, scale_note))
    # 机器可读标记: 界面用它把"自动启动"的日志标签页转移到真实窗口句柄
    print('BOUND_HWND:%d' % bound_top)
    return dm.BindWindow(target, 'normal', 'windows', 'windows', 0)


def bind_target(user32):
    """
    启动时绑定游戏窗口(两套挂机共用的入口逻辑):
    多账号绑界面指定句柄(窗口必须还存在), 单账号自动查找, 没找到就拉起游戏
    :return: 绑定的大窗口句柄
    """
    if FIXED_HWND:
        if not ctypes.windll.user32.IsWindow(FIXED_HWND):
            raise RuntimeError('多账号模式: 指定的游戏窗口已关闭, 请刷新列表重新选择')
        print(time.strftime('%H:%M:%S'), '多账号模式, 绑定指定窗口:', FIXED_HWND)
        bind_game(FIXED_HWND)
        return FIXED_HWND
    hwnd = dm.FindWindow('Chrome_WidgetWin_0', GAME_WINDOW_TITLE)
    if hwnd == 0:
        print(time.strftime('%H:%M:%S'), '未找到佣兵小镇运行窗口, 自动启动游戏...')
        hwnd = launch_game(user32)
    bind_game(hwnd)
    return hwnd


def keep_window(user32, hwnd_bound):
    """
    挂机主循环每轮的窗口存活检查(两套挂机共用)
    :return: (是否继续挂机, 当前绑定句柄, 是否刚重启过游戏)
             多账号: 绑定的窗口被关 -> (False, ...)
             单账号: 窗口变化重新绑定 / 窗口消失重启游戏
    """
    if FIXED_HWND:
        # 多账号: 大窗口或渲染子窗口被关闭时, 停止本账号的挂机进程
        u32 = ctypes.windll.user32
        if not u32.IsWindow(bound_top):
            print(time.strftime('%H:%M:%S'), '大窗口已关闭, 停止本账号挂机')
            return False, hwnd_bound, False
        if bound_child and not u32.IsWindow(bound_child):
            print(time.strftime('%H:%M:%S'), '渲染子窗口已关闭, 停止本账号挂机')
            return False, hwnd_bound, False
        return True, hwnd_bound, False
    hwnd_now = dm.FindWindow('Chrome_WidgetWin_0', GAME_WINDOW_TITLE)
    if hwnd_now != 0 and hwnd_now != hwnd_bound:
        print(time.strftime('%H:%M:%S'), '检测到游戏窗口变化, 重新绑定...')
        bind_game(hwnd_now)
        sleep(2)
        return True, hwnd_now, False
    if hwnd_now == 0:
        print(time.strftime('%H:%M:%S'), '游戏窗口消失, 重启游戏继续挂机...')
        return True, launch_game(user32), True
    return True, hwnd_bound, False


_frame_cache = {}  # hwnd -> (时间, 图像): 0.5秒内复用同一帧, 一轮循环里多个OCR/像素判定共享截图


_dm_cap_mode = None   # 已验证可用的大漠后台截图显示模式(Win7兜底用)
_dm_cap_tried = False  # 黑帧后是否已尝试过大漠截图(避免每帧反复尝试拖慢循环)
_dm_cap_fail_logged = 0
_pw_fail_logged = False  # PrintWindow失败只留痕一次
_pw_both_fail_logged = False  # 两种标志都失败只完整诊断一次
_lg_frame = None        # 最近一次成功捕获的画面(截图对抗期过渡用, 最长30秒)
_frame_share_ts = 0     # 上次共享帧写盘时间(限频1秒)
_lg_ts = 0
_lg_hwnd = 0
_stall_logged = False   # 过渡帧启用只留痕一次
_diag_dumped = False         # 诊断输出标记


def _diag_log(msg):
    """截图/更新故障诊断: 写入update_data/update_log.txt(客户回传的唯一渠道),
    同时打印到worker标签页日志; 追加写失败时静默(不影响主流程)"""
    try:
        f = open(os.path.join(_data_dir(), 'update_log.txt'), 'a')
        f.write('%s %s\n' % (time.strftime('%m-%d %H:%M:%S'), msg))
        f.close()
    except Exception:
        pass
    print(time.strftime('%H:%M:%S'), '[诊断]', msg)


def _dump_capture_diag(hwnd, err2, err0):
    """PrintWindow全失败时输出一次性完整诊断(够远程一次定位):
    系统版本/窗口归属进程/窗口可见性/捕获保护标志/DWM/双标志错误码"""
    global _diag_dumped
    if _diag_dumped:
        return
    _diag_dumped = True
    try:
        user32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        u32le = ctypes.WinDLL('user32', use_last_error=True)
        _diag_log('=== PrintWindow故障诊断(仅记录一次) ===')
        _diag_log('系统: %s, 当前进程PID=%d' % (_diag_osver(), os.getpid()))
        _diag_log('目标hwnd=%d IsWindow=%s IsVisible=%s IsIconic=%s'
                  % (hwnd, bool(u32le.IsWindow(hwnd)), bool(u32le.IsWindowVisible(hwnd)),
                     bool(u32le.IsIconic(hwnd))))
        # 目标窗口归属进程(安全软件/别的账号会话会导致跨会话截图失败)
        pid = wintypes.DWORD(0)
        u32le.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pname = ''
        try:
            h = k32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED
            buf = ctypes.create_unicode_buffer(1024)
            bsz = ctypes.c_ulong(1024)
            if h and k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(bsz)):
                pname = buf.value
            if h:
                k32.CloseHandle(h)
        except Exception:
            pass
        _diag_log('目标窗口 pid=%d 进程=%r' % (pid.value, pname))
        # 捕获保护: WDA_EXCLUDEFROMCAPTURE(Win10 2004+)会让PrintWindow必败
        aff = ctypes.c_ulong(0)
        if u32le.GetWindowDisplayAffinity(hwnd, ctypes.byref(aff)) == 0:
            _diag_log('窗口捕获保护 WDA=0x%x%s' % (
                aff.value, '(含EXCLUDEFROMCAPTURE, PrintWindow必失败)' if aff.value else ''))
        else:
            _diag_log('窗口捕获保护查询失败 GetLastError=%d' % ctypes.get_last_error())
        # DWM合成(关闭时Win7/老系统GPU窗口截图更受限)
        dwm_on = ctypes.c_int(0)
        try:
            ctypes.windll.dwmapi.DwmIsCompositionEnabled(ctypes.byref(dwm_on))
            _diag_log('DWM合成: %s' % ('开启' if dwm_on.value else '关闭'))
        except Exception:
            _diag_log('DWM合成: 查询失败')
        # 目标窗口DPI(跨DPI多显示器时PrintWindow有已知问题)
        try:
            _diag_log('目标窗口DPI: %d (系统DPI=%d)'
                      % (user32.GetDpiForWindow(hwnd) if hasattr(user32, 'GetDpiForWindow') else -1,
                         user32.GetDpiForSystem() if hasattr(user32, 'GetDpiForSystem') else -1))
        except Exception:
            pass
        _diag_log('PrintWindow错误码: 标志2=%d, 标志0=%d' % (err2, err0))
    except Exception as e:
        _diag_log('诊断输出异常: %r' % e)


def _diag_osver():
    """系统版本串(诊断用): Win7=6.1 Win10=10.0 Win11注册表build>=22000"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r'SOFTWARE\Microsoft\Windows NT\CurrentVersion')
        name = winreg.QueryValueEx(k, 'ProductName')[0]
        build = winreg.QueryValueEx(k, 'CurrentBuildNumber')[0]
        return '%s build=%s' % (name, build)
    except Exception:
        return '未知'


def _dm_cap_log(msg):
    """Win7截图兜底过程日志: 前5条+每50条(打印+update_log.txt), 防每帧刷屏;
    模式锁定等状态变化走独立调用, 不受此限频影响"""
    global _dm_cap_fail_logged
    _dm_cap_fail_logged += 1
    if _dm_cap_fail_logged <= 5 or _dm_cap_fail_logged % 50 == 1:
        _diag_log('[Win7截图兜底] %s' % msg)
        print(time.strftime('%H:%M:%S'), '[Win7截图兜底]', msg)


# ================== 截图(开局探测锁定唯一通道) ==================
_cap_channel = None     # 锁定的截图通道: pw2/pw0/dx2/dx3/normal
_cap_fail_streak = 0    # 锁定通道连续失败次数(>=5重新探测)


def _pw_capture_arr(hwnd, flag, pw, ph, w, h):
    """PrintWindow截图(flag=2或0), 返回(ok, arr)"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

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
    u32le = ctypes.WinDLL('user32', use_last_error=True)
    ok = u32le.PrintWindow(hwnd, mem, flag)
    err = ctypes.get_last_error() if not ok else 0
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
    if not ok:
        _diag_log('PrintWindow(flag=%d)失败 GetLastError=%d' % (flag, err))
        return False, None
    if (pw, ph) != (w, h):
        arr = cv2.resize(arr, (w, h), interpolation=cv2.INTER_AREA)
    return True, arr


def _dm_capture_arr(hwnd, mode, log_lock=True):
    """大漠dx后台截图(指定模式), 返回非黑arr或None; 模式锁定日志只在切换时打"""
    global _dm_cap_mode
    if dm is None:
        return None
    try:
        if not dm.BindWindow(hwnd, mode, 'windows', 'windows', 0):
            _diag_log('大漠BindWindow %s 失败' % mode)
            return None
        time.sleep(0.3)
        if not dm.Capture(0, 0, CLIENT_SIZE[0], CLIENT_SIZE[1], '_dm_frame.bmp'):
            _diag_log('大漠Capture %s 失败' % mode)
            return None
        p = os.path.join(dm_path, '_dm_frame.bmp')
        if not os.path.exists(p):
            _diag_log('大漠Capture %s 未产出文件' % mode)
            return None
        arr = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        try:
            os.remove(p)
        except OSError:
            pass
        if arr is None or arr.max() <= 8:
            _diag_log('大漠 %s 截图为黑帧' % mode)
            return None
        if arr.shape[:2] != (CLIENT_SIZE[1], CLIENT_SIZE[0]):
            arr = cv2.resize(arr, (CLIENT_SIZE[0], CLIENT_SIZE[1]),
                             interpolation=cv2.INTER_AREA)
        if _dm_cap_mode != mode:
            if log_lock:
                _diag_log('大漠 %s 后台截图可用, 锁定为当前通道' % mode)
        _dm_cap_mode = mode
        return arr[:, :, ::-1].copy()
    except Exception as e:
        _diag_log('大漠 %s 异常: %r' % (mode, e))
        return None


def _capture_probe(hwnd, pw, ph, w, h):
    """开局探测: 按优先级试截图通道(pw2→pw0→大漠dx2→dx3→normal),
    高优先级出真画面就立即锁定返回, 不再往下判定(省探测时间, 用户要求)"""
    for flag, name in ((2, 'pw2'), (0, 'pw0')):
        ok, arr = _pw_capture_arr(hwnd, flag, pw, ph, w, h)
        good = ok and arr is not None and arr.max() > 8
        if good:
            _diag_log('探测通道 %s: 可用, 锁定' % name)
            return name
        _diag_log('探测通道 %s: 不可用' % name)
    for mode in ('dx2', 'dx3', 'normal'):
        arr = _dm_capture_arr(hwnd, mode, log_lock=False)
        good = arr is not None and arr.max() > 8
        if good:
            _diag_log('探测通道 dm-%s: 可用, 锁定' % mode)
            return 'dm:' + mode
        _diag_log('探测通道 dm-%s: 不可用' % mode)
    _diag_log('探测完成: 所有截图通道均不可用')
    return None


def capture_game():
    """
    PrintWindow窗口内部截图: 游戏被其他窗口遮挡也能正常截到
    (注意: 最小化到任务栏不行, 遮挡可以)
    开局探测锁定唯一可用截图通道(实测Win7只有大漠dx2可用/部分Win11机器
    PrintWindow直接失败, 机器相关), 之后每帧只跑锁定通道;
    通道连续失败>=5次自动重新探测; 全部通道失败则报错并停止挂机(用户要求)
    带0.5秒帧缓存: 主循环一轮会连续做3~5次识别/像素判定, 各自截同一画面纯属浪费;
    缓存帧只读共享(调用方都是切片copy/astype, 无原地写), 点击后下一轮间隔>1秒拿新帧
    :return: (是否成功, 整窗RGB图像numpy数组), 坐标系与CLIENT_SIZE一致
    """
    global _cap_channel, _cap_fail_streak, _frame_share_ts, _stall_logged, _lg_frame, _lg_ts, _lg_hwnd
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    hwnd = bound_hwnd
    if not hwnd:
        top = user32.FindWindowW('Chrome_WidgetWin_0', GAME_WINDOW_TITLE)
        hwnd = find_render_child(top) or top
    if not hwnd:
        return False, None

    now = time.time()
    cached = _frame_cache.get(hwnd)
    if cached is not None and now - cached[0] < 0.5:
        return True, cached[1]

    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    if w <= 0 or h <= 0:
        return False, None
    # 物理客户区尺寸: 非基准缩放下DPI虚拟化会让逻辑尺寸≠物理尺寸,
    # PrintWindow按物理渲染, 缓冲区必须开物理大小, 否则截图被裁掉一块(实测150%缩放)
    pw, ph = w, h
    if hasattr(user32, 'SetThreadDpiAwarenessContext'):
        old_ctx = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PMv2
        if old_ctx:
            pr = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(pr))
            user32.SetThreadDpiAwarenessContext(old_ctx)
            if pr.right > 0 and pr.bottom > 0:
                pw, ph = pr.right, pr.bottom

    # 开局探测: 锁定唯一可用截图通道(全失败报错并停止挂机, 用户要求)
    if _cap_channel is None:
        _cap_channel = _capture_probe(hwnd, pw, ph, w, h)
        if _cap_channel is None:
            _diag_log('所有截图通道均失败, 挂机停止。'
                      '请确认游戏窗口未最小化、显示缩放100%、未开窗口捕获保护')
            return False, None
        _cap_fail_streak = 0

    # 按锁定通道截图
    if _cap_channel in ('pw2', 'pw0'):
        ok, arr = _pw_capture_arr(hwnd, 2 if _cap_channel == 'pw2' else 0, pw, ph, w, h)
    else:
        mode = _cap_channel.split(':')[1]
        arr = _dm_capture_arr(hwnd, mode)
        ok = arr is not None

    if not ok or arr is None or arr.max() <= 8:
        _cap_fail_streak += 1
        # 30秒内最近成功帧过渡(捕获保护间歇期挂机不断流)
        if (_lg_frame is not None and _lg_hwnd == hwnd
                and now - _lg_ts < 30):
            if not _stall_logged:
                _stall_logged = True
                _diag_log('通道 %s 本轮失败, 用最近成功帧过渡(最长30秒)' % _cap_channel)
            _frame_cache[hwnd] = (now, _lg_frame)
            return True, _lg_frame
        # 连续5次失败: 重新探测(通道可能恢复/切换)
        if _cap_fail_streak >= 5:
            _diag_log('通道 %s 连续失败%d次, 重新探测可用通道'
                      % (_cap_channel, _cap_fail_streak))
            _cap_channel = None
            _cap_fail_streak = 0
        if not ok or arr is None:
            return False, None
        # 黑帧: 返回黑帧(现状), 恢复后自动继续
    else:
        _cap_fail_streak = 0
        # 成功帧存为过渡帧+共享给界面缩略图(Win7/部分Win11界面PrintWindow黑图)
        _lg_frame, _lg_ts, _lg_hwnd = arr, time.time(), hwnd
        _stall_logged = False
        if now - _frame_share_ts > 1:
            _frame_share_ts = now
            try:
                ok2, jpg = cv2.imencode('.jpg', arr[:, :, ::-1],
                                        [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok2:
                    jpg.tofile(os.path.join(_data_dir(), '_latest_frame.jpg'))
            except Exception:
                pass  # 共享帧只是UI缩略图/黑帧兜底, 写失败不影响挂机, 不值得记日志
    if (coord_sx - 1.0) ** 2 + (coord_sy - 1.0) ** 2 > 1e-12:
        arr = cv2.resize(arr, (CLIENT_SIZE[0], CLIENT_SIZE[1]),
                         interpolation=cv2.INTER_AREA)
    _frame_cache[hwnd] = (now, arr)
    return True, arr


# ================== OCR(paddle_find / text_locate) ==================
paddle_engine = None  # RapidOCR(PaddleOCR PP-OCRv4)实例, 懒加载

paddle_cache = {}  # (区域,标签) -> (时间, 识别结果)
_miss_dump_ts = {}  # tag -> 上次miss调试图落盘时间(同tag 60秒只存一次, 纯调试产物限频)
# 命中缓存TTL按tag区分(秒): 弹窗类文字消失快, 长缓存会让重判"瞎答"(实测盲点3次确定的根源)
PADDLE_HIT_TTL = {'确定': 6, '再次挑战': 6, '点击空白': 6, '队伍出发': 15, '弃权': 15}
# 命中结果缓存30秒, 未命中缓存8秒: 同状态的反复检查(状态确认/点击定位/重判)直接复用,
# 省掉每轮0.6~2.3秒的重复OCR; 页面切换后最多30秒自动过期
PADDLE_CANDIDATE_LOG = False  # True时打印区域内所有候选识别(排查用), False只打印疑似命中


def paddle_find(x1, y1, x2, y2, tag='', quick=False):
    """
    用PaddleOCR识别区域文字
    小/中区域先走"纯识别"快路径(整块直接认字, 跳过最贵的检测模型和方向分类,
    单次约0.1秒); 没认出再升级到检测模式(截图外扩提供上下文, 逐档放大重试)
    :param quick: 状态识别场景传True: 检测模式只跑1档, 不逐级放大重试
                  (重判成本从~5s降到~1.5s; 精确定位点击坐标时不要用)
    :return: [(文字, 置信度, 中心x, 中心y), ...] 坐标为窗口绝对坐标, 失败返回[]
    """
    global paddle_engine
    out = []
    # 负缓存: 3秒内同一区域刚识别过且没命中, 直接跳过(界面不会在两次调用间变化)
    key = ((x1, y1, x2, y2), tag)
    cached = paddle_cache.get(key)
    if cached is not None:
        ts, res = cached
        ttl = PADDLE_HIT_TTL.get(tag, 30) if res else 3  # 命中TTL按tag区分(弹窗类短)/未命中3秒
        if time.time() - ts < ttl:
            return list(res)
    try:
        if paddle_engine is None:
            # onnxruntime导入时会从C++层直接往stderr打印无害警告(日志级别管不到),
            # 导入期间临时把stderr重定向到黑洞
            devnull = os.open(os.devnull, os.O_WRONLY)
            old_stderr = os.dup(2)
            os.dup2(devnull, 2)
            try:
                import onnxruntime
                onnxruntime.set_default_logger_severity(4)
                from rapidocr_onnxruntime import RapidOCR
                # 推理线程数按核数自适应: 推理用满所有核是CPU过高的根源(实测12核机器);
                # 实测2线程与4线程同速(451x845规模det在2~3线程即饱和), 高配也只给2,
                # 突发峰值从~240%压到~120%; 多开worker叠加时总占用同样减半
                cores = os.cpu_count() or 4
                if cores >= 4:
                    ocr_threads = 2
                else:
                    ocr_threads = 1  # 2~3核老机器: 保可用, 单次OCR慢但循环有1~3秒间隔兜底
                print('OCR引擎: %d核, 推理线程%d' % (cores, ocr_threads))
                paddle_engine = RapidOCR(intra_op_num_threads=ocr_threads)
            finally:
                os.dup2(old_stderr, 2)
                os.close(old_stderr)
                os.close(devnull)
        import cv2

        def capture(margin):
            """截取区域(margin=外扩边距, 上限为窗口内容区), 返回(图像, 截取范围)
            用PrintWindow内部截图, 游戏被遮挡也能截"""
            px1, py1 = max(x1 - margin, 0), max(y1 - margin, 0)
            px2, py2 = min(x2 + margin, CLIENT_SIZE[0]), min(y2 + margin, CLIENT_SIZE[1])
            ok, full = capture_game()
            if not ok or full is None:
                return None, (px1, py1, px2, py2)
            return full[py1:py2, px1:px2].copy(), (px1, py1, px2, py2)

        def recog(im, use_det):
            """调引擎, 统一返回[(文字, 分数, 坐标框或None), ...]; 跳过方向分类(UI文字不会倒)"""
            res, _ = paddle_engine(im, use_det=use_det, use_cls=False)
            if not res:
                return []
            if len(res[0]) == 2:
                return [(r[0], r[1], None) for r in res]   # 纯识别: [文字, 分数]
            return [(r[1], r[2], r[0]) for r in res]       # 检测: [坐标, 文字, 分数]

        small = (x2 - x1) <= 250 and (y2 - y1) <= 100
        if small:
            # ===== 快路径: 只截目标区域整块认字, 毫秒级; 原图不行放大3倍再试 =====
            # 整块识别会把场景噪音一起读进来, 结果必须沾到目标文字才算数,
            # 否则升级到检测模式(det能真正定位按钮, 噪音场景下纯识别常读出垃圾)
            for scale in (1, 3):
                img, _ = capture(0)
                if img is None:
                    break
                if scale > 1:
                    img = cv2.resize(img, None, fx=scale, fy=scale,
                                     interpolation=cv2.INTER_LANCZOS4)
                for text, score, _box in recog(img, use_det=False):
                    if sum(1 for ch in tag if ch in text) >= 1:
                        # 识别结果沾到目标文字: 区域中心即点击点
                        out.append((text, score, (x1 + x2) // 2, (y1 + y2) // 2))
                if out:
                    break
        if not out:
            # ===== 检测模式兜底: 截图外扩提供上下文, 递进放大重试 =====
            tiny = (x2 - x1) <= 100 and (y2 - y1) <= 100
            # quick模式只跑1档(状态识别够用), 完整模式小区域3档递进(要点得准)
            attempts = [(24, 1)] if (tiny and not quick) else \
                ([(24, 1), (24, 3), (64, 1)] if tiny else [(24, 1)])
            last_img = None
            for margin, scale in attempts:
                img, pos = capture(margin)
                if img is None:
                    # 截图失败(如窗口竞争), 换下一档重试
                    last_img = None
                    continue
                last_img = img
                px1, py1, px2, py2 = pos
                im = img if scale == 1 else cv2.resize(
                    img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
                for text, score, box in recog(im, use_det=True):
                    if box is None:
                        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    else:
                        # 中心坐标换算回窗口绝对坐标(放大过的先除回去)
                        cx = px1 + int(sum(p[0] / scale for p in box) / 4)
                        cy = py1 + int(sum(p[1] / scale for p in box) / 4)
                    if x1 <= cx <= x2 and y1 <= cy <= y2:
                        out.append((text, score, cx, cy))
                if out:
                    break
            if not out and last_img is not None:
                # 没认出: 保存裁剪图供排查(带tag防覆盖); 同tag 60秒只落盘一次
                # (高频miss的状态每轮存图纯属浪费, 调试图不参与识别判定)
                # 打包版不带pic目录, 存图失败直接忽略(不能因存图失败丢OCR结果)
                if time.time() - _miss_dump_ts.get(tag or 'ocr', 0) > 60:
                    _miss_dump_ts[tag or 'ocr'] = time.time()
                    try:
                        Image.open(io.BytesIO(cv2.imencode('.bmp', last_img)[1].tobytes())).save(
                            os.path.join(dm_path, 'pic', 'paddle_debug_%s.png' % (tag or 'ocr')))
                    except OSError:
                        pass
    except Exception as e:
        print('paddle ocr failed:', e)
        return []
    paddle_cache[key] = (time.time(), list(out))  # 命中与未命中都缓存(不同TTL)
    for text, score, cx, cy in out:
        # 候选过滤: 和目标文字沾边的才打日志, 否则每轮一堆"?"噪音(排查时开PADDLE_CANDIDATE_LOG)
        if PADDLE_CANDIDATE_LOG or sum(1 for ch in tag if ch in text) >= 2 or not tag:
            print('paddle[%s]:' % (tag or 'ocr'), text, round(score, 2), cx, cy)
    return out


def text_locate(rect, text, dy=0, quick=False):
    """
    在rect区域内用PaddleOCR找目标文字
    :param dy: 点击y向下偏移(如"点击空白"要点文字下方)
    :param quick: 状态识别场景传True, 透传给paddle_find(单档检测, 省时间)
    :return: (点击x, y+dy) 或 None
    """
    need = max(2, len(text) - 1)  # 2字要全中, 3字以上允许错1个
    for t, s, cx, cy in paddle_find(rect[0], rect[1], rect[2], rect[3], text, quick=quick):
        if sum(1 for ch in text if ch in t) >= need:
            return cx, cy + dy
    return None


def click_text(rect, text, dy=0):
    """
    定位文字并点击(通用操作: 找到才点, 点不到返回None)
    :return: (点击x, y+dy) 或 None
    """
    pos = text_locate(rect, text, dy=dy)
    if pos is not None:
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        paddle_cache.clear()  # 点击后界面将变, 清OCR缓存防下轮判到点击前的旧画面
    return pos


def fail_color_hit():
    """
    一队失败判定: 在特征色检测区域里找红色X的色值(像素级匹配)
    用自己的PrintWindow截图而不是dm.FindColor: dm绑定的是normal模式,
    后台(被遮挡)时它取到的是屏幕内容, 取不到游戏画面的颜色
    :return: True 检测到一队失败特征色
    """
    ok, full = capture_game()
    if not ok or full is None:
        return False
    fx1, fy1, fx2, fy2 = page_rect(FAIL_COLOR_RECT)
    x1, y1, x2, y2 = fx1, fy1, fx2, fy2
    region = full[y1:y2, x1:x2].astype(int)
    for color in FAIL_COLORS:
        hits = (np.abs(region - color) <= FAIL_COLOR_OFFSET).all(axis=2)
        if hits.any():
            ys, xs = np.nonzero(hits)
            # 只在命中时打日志, 未命中是每轮循环的常态
            print('一队失败特征色', x1 + int(xs[0]), y1 + int(ys[0]))
            return True
    # 宽容判定: 不同GPU渲染/非基准缩放的截图重采样会让红X颜色偏移,
    # 精确匹配漏检(实测150%虚拟化模式)。
    # 判定(多重校验, 防误判):
    # 1.颜色=纯深红(150<R, G<80, B<80): 失败X是深红(203,29,29);
    #   排除公屏聊天的粉色文字/白色描边(实测: 聊天滚过检测区被误判成一队失败)
    # 2.位置=紧凑红块中心在三个队伍徽章槽位内: 左下角3队横排, 任意一队失败
    #   都有红X(本机实测: 1队X中心(48,745), 2队X中心(90,754); 旧单槽位x35~70
    #   漏掉2/3队, 150%客户机2队失败不弃权); y上界776排除队伍交换图标持久红块
    #   (实测(82,787), 只比X低10~40px, 是唯一可用区分维度)
    # 3.持续=连续两次检测(6秒内)同一位置(±15px): 聊天文字1秒就滚走,
    #   失败标记持续在场
    global _fail_streak, _fail_pos, _fail_t
    r, g, b = region[:, :, 0], region[:, :, 1], region[:, :, 2]
    mask = ((r > 150) & (g < 80) & (b < 80)).astype(np.uint8)
    now = time.time()
    hit_pos = None
    if mask.any():
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for i in range(1, n):
            x0, y0, bw, bh, area = stats[i]
            cx, cy = x0 + bw / 2.0, y0 + bh / 2.0
            # 相对区域原点(25,715): 槽位x 30~148 -> rel 5~123, y 715~776 -> rel 0~61
            if area >= 60 and bw <= 45 and bh <= 45 and 5 <= cx <= 123 and 0 <= cy <= 61:
                hit_pos = (x0 + x1 + cx, y0 + y1 + cy)
                break
    if hit_pos is None:
        _fail_streak = 0
        return False
    if (_fail_t and now - _fail_t < 6 and _fail_pos
            and abs(hit_pos[0] - _fail_pos[0]) <= 15 and abs(hit_pos[1] - _fail_pos[1]) <= 15):
        _fail_streak += 1
    else:
        _fail_streak = 1
    _fail_pos, _fail_t = hit_pos, now
    if _fail_streak >= 2:
        print('队伍失败特征色(宽容): 红色标记块连续%d次命中@(%d,%d)'
              % (_fail_streak, hit_pos[0], hit_pos[1]))
        return True
    return False


# ================== 网络检测与断网监控 ==================
def net_ok():
    """
    网络判定: 多目标多协议探测, 任一成功即算在线。
    实测部分客户网络禁ICMP/限制DNS, 只ping百度会误报断网(游戏明明在线):
    - ping baidu(ICMP+DNS)
    - TCP连 www.qq.com:443 / dns.alidns.com:53(DNS+TCP)
    - TCP连 223.5.5.5:53(纯IP直连, 不依赖DNS: DNS挂了但长连接还在的场景)
    全部失败才判定断网(真断网: 所有协议都出不去, 不会漏判)
    :return: True 网络通 / False 网络不通
    """
    try:
        if subprocess.call('ping -n 1 -w 2000 www.baidu.com',
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) == 0:
            return True
    except Exception:
        pass  # ping通道失败属正常降级, 后面还有TCP三通道兜着
    import socket
    for host, port in (('www.qq.com', 443), ('dns.alidns.com', 53), ('223.5.5.5', 53)):
        try:
            s = socket.create_connection((host, port), timeout=3)
            s.close()
            return True
        except Exception:
            continue
    return False


def check_network():
    """
    连续3次ping不通才判定断网, 避免偶尔丢包误判
    :return: True 网络正常 / False 断网
    """
    fail = 0
    while fail < 3:
        if net_ok():
            return True
        fail += 1
        sleep(1)
    return False


def wait_network_recover():
    """
    断网后阻塞等待网络恢复, 连续3次ping通才算恢复
    """
    print(time.strftime('%H:%M:%S'), '检测到断网, 等待网络恢复...')
    ok_count = 0
    while ok_count < 3:
        if net_ok():
            ok_count += 1
        else:
            ok_count = 0
        sleep(5)
    print(time.strftime('%H:%M:%S'), '网络已恢复!')


# 挂机模式的后台断网监控信号
net_offline = threading.Event()  # 断网中: 主循环暂停一切游戏操作
net_restart = threading.Event()  # 网络恢复: 通知主循环重启游戏继续挂机


def net_monitor_bg():
    """
    挂机模式的后台断网监控线程: 只做ping检测(不碰dm/COM, 避免跨线程COM问题)
    断网→置net_offline让主循环暂停; 恢复→清offline并置net_restart让主循环重启游戏
    """
    while 1:
        if check_network():
            sleep(3)
            continue
        net_offline.set()
        wait_network_recover()
        net_offline.clear()
        net_restart.set()


# ================== 游戏启动/重启 ==================
def game_hwnds():
    """当前所有佣兵小镇游戏窗口句柄
    标题用包含匹配: 不同渠道(QQ/微信)启动的实例标题可能带后缀,
    精确匹配会漏数 -> 多账号保护失效(实测全局taskkill连锁杀了另一个账号)"""
    u32 = ctypes.windll.user32
    out = []
    h = 0
    while True:
        h = u32.FindWindowExW(0, h, 'Chrome_WidgetWin_0', None)
        if not h:
            break
        buf = ctypes.create_unicode_buffer(64)
        u32.GetWindowTextW(h, buf, 64)
        if '佣兵小镇' in buf.value:
            out.append(h)
    return out


class restart_lock:
    """跨进程重启锁: 多账号同时断网重启时串行化,
    防止两个worker按标题抢绑同一个新窗口(实测)"""
    _h = None

    def __enter__(self):
        k32 = ctypes.windll.kernel32
        if restart_lock._h is None:
            restart_lock._h = k32.CreateMutexW(None, False, 'YBXZ_GameRestartLock')
        t0 = time.time()
        k32.WaitForSingleObject(restart_lock._h, 0x7FFFFFFF)
        waited = time.time() - t0
        if waited > 3:
            print(time.strftime('%H:%M:%S'),
                  '等待其他窗口的重启完成(%.0f秒), 马上轮到本窗口...' % waited)
        return self

    def __exit__(self, *a):
        ctypes.windll.kernel32.ReleaseMutex(restart_lock._h)
        return False


def kill_window_host(hwnd):
    """精确杀掉指定窗口的宿主进程树(绝不按进程名全局杀WeChatAppEx:
    它是所有小程序窗口的共享宿主, 全局杀会连带其他账号, 实测两账号同灭)"""
    pid = wintypes.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value:
        os.system('taskkill /f /t /pid %d' % pid.value)
        sleep(3)


def close_game_window(user32, hwnd=0):
    """关闭游戏窗口: 先WM_CLOSE礼貌关闭(重试3次),
    关不掉最后手段=精确杀掉该窗口的宿主进程树(只影响自己)"""
    if hwnd == 0 or not user32.IsWindow(hwnd):
        return
    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    sleep(3)
    for _ in range(3):
        if not user32.IsWindow(hwnd):
            return
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        sleep(2)
    print(time.strftime('%H:%M:%S'), '礼貌关闭失败, 精确强杀该窗口的宿主进程...')
    kill_window_host(hwnd)
def get_game_lnk():
    """
    找桌面上的佣兵小镇快捷方式(兼容桌面被OneDrive重定向的情况)
    UI多账号模式会通过--lnk传入完整路径覆盖(每个窗口各自的重启快捷方式)
    """
    if os.path.isabs(GAME_LNK):
        return GAME_LNK  # 完整路径覆盖: 存在性由调用方检查
    home = os.path.expanduser('~')
    candidates = [
        os.path.join(home, 'Desktop', GAME_LNK),
        os.path.join(home, 'OneDrive', 'Desktop', GAME_LNK),
        os.path.join(os.environ.get('ONEDRIVE', '') or home, 'Desktop', GAME_LNK),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


def loading_now():
    """识别加载进度区域: '加载进度'4个字全中即认为正在加载"""
    for t, s, cx, cy in paddle_find(*LOADING_RECT, '加载进度'):
        if all(ch in t for ch in '加载进度'):
            return True
    return False


def dm_drag_steps(x, y, distance=200, steps=12):
    """后台拖拽手势(分步版): LeftDown后小步移动再LeftUp。
    必须分步! 瞬移200px浏览器不认成拖动, 列表纹丝不动(实测)
    distance正=手指向下拖(露出列表上方内容), 负=向上拖"""
    dm.MoveTo(x, y)
    sleep(0.2)
    dm.LeftDown()
    sleep(0.2)
    for i in range(1, steps + 1):
        dm.MoveTo(x, y + distance * i // steps)
        sleep(0.05)
    sleep(0.3)
    dm.LeftUp()


def unknown_debug_dump():
    """unknown状态诊断: 存当前画面 + 打印全屏OCR识别到的所有文字(远程排查用)
    保存位置: exe(或脚本)同目录, 方便用户直接找到发回来"""
    ok, img = capture_game()
    out_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    if ok:
        Image.fromarray(img).save(os.path.join(out_dir, 'unknown_debug.png'))
        print(time.strftime('%H:%M:%S'), '诊断: 已保存当前画面 unknown_debug.png(程序同目录)')
        for t, s, cx, cy in paddle_find(0, 0, CLIENT_SIZE[0], CLIENT_SIZE[1], '全屏'):
            print(time.strftime('%H:%M:%S'), '诊断OCR: %s(%.2f) (%d,%d)' % (t, s, cx, cy))
    else:
        print(time.strftime('%H:%M:%S'), '诊断: 内部截图失败(窗口可能已关闭)')


def _restart_marker_path(hwnd):
    """断网重启标记文件路径(UI靠它区分'重启中要保留日志页'和'窗口没了照旧清理')"""
    return os.path.join(_data_dir(), 'ybxz_restarting_%d.flag' % (hwnd or 0))


def _clear_restart_marker(hwnd):
    try:
        os.remove(_restart_marker_path(hwnd))
    except OSError:
        pass


def launch_game(user32, hwnd=0, mark=False):
    """
    重启游戏并重新登录: 关旧窗口 -> 拉起小程序 -> 等新窗口 -> 重新绑定 -> 点登录
    多账号安全: 跨进程重启锁串行化; 只绑定本次启动后【新出现】的窗口
    (按标题FindWindow会抢到别的账号窗口, 实测两worker同时重启绑到同一个)
    mark=True(断网/掉线重启): 先写重启标记, UI发现旧窗口消失时保留日志页等
    新窗口改挂(重启全程几分钟, 没标记会被UI的5秒定期同步当成窗口没了杀掉worker);
    非网络原因的重启(如keep_window单账号拉起)不标记, 维持UI原有清理行为
    """
    if mark:
        try:
            io.open(_restart_marker_path(hwnd), 'w').write(str(hwnd or 0))
        except Exception:
            pass
    handoff = os.path.join(tempfile.gettempdir(), 'ybxz_restart_handoff.txt')
    with restart_lock():
        # 同窗口双worker场景: 排队的worker接管上一个worker刚重启好的新窗口, 不二次重启
        inherited = False
        try:
            old, new = io.open(handoff).read().split(',')
            if hwnd and int(old) == hwnd:
                hwnd = int(new)
                inherited = True
                print(time.strftime('%H:%M:%S'), '本窗口刚被其他worker重启, 接管新窗口', hwnd)
        except Exception:
            pass
        hwnd2 = 0
        if inherited:
            # 接管的窗口已经在运行(别的worker刚重启完), 直接跳到绑定登录
            print(time.strftime('%H:%M:%S'), '接管窗口, 跳过拉起流程')
        else:
            pre = set(game_hwnds())
            close_game_window(user32, hwnd)
            lnk = get_game_lnk()
            if not os.path.exists(lnk):
                raise RuntimeError('桌面上未找到佣兵小镇快捷方式(%s), 无法自动启动游戏, 请手动创建或打开游戏' % GAME_LNK)
            print(time.strftime('%H:%M:%S'), '正在重新启动游戏...')
            # 微信刚拉起时登录态未就绪会吞掉打开小程序的指令, 没见到窗口就重发URL
            for attempt in range(3):
                # 模拟双击桌面快捷方式启动
                os.startfile(lnk)
                for i in range(6):  # 每次尝试最多等60秒
                    sleep(10)
                    new = [h for h in game_hwnds() if h not in pre]
                    if new:
                        hwnd2 = new[0]
                        break
                if hwnd2 != 0:
                    break
            if hwnd2 == 0:
                # 兜底: 没等到新窗口(可能lnk只是聚焦了已开实例), 退回按标题找。
                # 多账号绝不能杀"残留"窗口: 那可能是其他账号正在用的窗口(实测误杀
                # 了地下城刚重启好的新窗口)
                hwnd2 = dm.FindWindow('Chrome_WidgetWin_0', GAME_WINDOW_TITLE)
        # 写交接文件: 排队的同窗口worker拿到锁后接管新窗口(避免二次重启)
        try:
            if hwnd and not inherited and hwnd2:
                io.open(handoff, 'w').write('%d,%d' % (hwnd, hwnd2))
        except Exception:
            pass
        if hwnd2 == 0:
            if hwnd:
                # 多账号: 按标题兜底FindWindow会抢到其他账号的窗口
                # (实测地下城重启绑定了远征刚重启的窗口), 宁可报错让人工处理
                raise RuntimeError('重启游戏失败: 拉起lnk后未出现新游戏窗口(180秒)')
            hwnd2 = dm.FindWindow('Chrome_WidgetWin_0', GAME_WINDOW_TITLE)
        if hwnd2 == 0:
            raise RuntimeError('重启游戏失败, 未找到游戏窗口!')
        # 绑定+登录也在锁内: 另一账号要等本账号完全登录好才轮到重启。
        # 并发拉起时微信小程序单实例可能把对方窗口关了重建, 锁外绑定会绑错窗口(实测)
        sleep(10)  # 等小程序加载
        bind_game(hwnd2)
        sleep(3)
        # 点登录直到进入游戏主界面: 每隔1秒点登录坐标, 出现加载进度就停手,
        # 加载完识别到地下城入口即登录成功, 最多等5分钟
        lx, ly = LOGIN_CLICK
        loading_seen = False
        for i in range(150):
            if text_locate(LOGIN_OK_RECT, '地下城') is not None:
                _clear_restart_marker(hwnd)
                print(time.strftime('%H:%M:%S'), '已重新进入游戏!')
                return hwnd2
            if not loading_seen and loading_now():
                loading_seen = True
                print(time.strftime('%H:%M:%S'), '检测到加载进度, 停止点击, 等待进入主界面...')
            if not loading_seen:
                dm.MoveTo(lx, ly)
                dm.LeftClick()
            sleep(1)
        raise RuntimeError('重启游戏后5分钟仍未进入游戏主界面!')


# ================== 网络监控独立功能(功能7) ==================
def monitor_main(user32):
    """
    网络监控+掉线重启(独立功能, 用 python YBXZ.py 7 单独运行)
    监控断网/掉线弹窗/窗口消失, 触发后等网络恢复并重启游戏重新登录
    """
    print(time.strftime('%H:%M:%S'), '网络监控已启动: 断网/掉线弹窗/窗口消失都会自动重启, 心跳每60秒一次')
    last_beat = time.time()
    # 多账号: 优先绑定指定窗口(--hwnd), 单账号按标题找
    if FIXED_HWND and user32.IsWindow(FIXED_HWND):
        hwnd2 = FIXED_HWND
    else:
        hwnd2 = dm.FindWindow('Chrome_WidgetWin_0', GAME_WINDOW_TITLE)
    if hwnd2 != 0:
        bind_game(hwnd2)
    own = hwnd2  # 本监控跟踪的游戏窗口(重启后由launch_game返回值更新)
    while 1:
        # ===== 游戏窗口消失(崩溃/被手动关闭)也自动重启 =====
        # 放最前: 窗口没了就不做弹窗OCR, 避免截图失败的报错刷屏
        # 只认自己跟踪的句柄(实测: 多账号时按标题找会盯上别人的窗口, 永远不重启自己)
        hwnd_cur = own if (own and user32.IsWindow(own)) else 0
        if hwnd_cur == 0:
            print(time.strftime('%H:%M:%S'), '游戏窗口消失, 重新启动...')
            own = launch_game(user32, own, mark=True)
            if FIXED_HWND:
                globals()['FIXED_HWND'] = own  # 跟踪新窗口(远征转入监控时UI日志页按标记保留)
            last_beat = time.time()
            continue
        # ===== 掉线弹窗检测: 识别弹窗文字, 包含断线重连字样则重启 =====
        if text_locate(OFFLINE_RECT, OFFLINE_TEXT) is not None:
            print(time.strftime('%H:%M:%S'), '检测到游戏掉线弹窗!')
            own = launch_game(user32, hwnd_cur, mark=True)
            if FIXED_HWND:
                globals()['FIXED_HWND'] = own
            continue
        # ===== 断网监控: 网络不通则等待恢复并重启游戏 =====
        if not check_network():
            wait_network_recover()
            own = launch_game(user32, hwnd_cur, mark=True)
            if FIXED_HWND:
                globals()['FIXED_HWND'] = own
            continue
        # ===== 心跳: 每60秒报一次平安, 方便确认监控活着 =====
        if time.time() - last_beat > 60:
            print(time.strftime('%H:%M:%S'), '监控运行中: 网络/游戏正常')
            last_beat = time.time()
        sleep(3)


# ================== 精灵秘境挂机(功能5) ==================
def elf_fighting():
    """战斗中判定: 剩余时间区域识别到'时间'两字(两个字都要中)"""
    return text_locate(FIGHT_RECT, '时间') is not None


def elf_find_mire():
    """
    定位玩法列表左侧标签区里的'精灵秘境'(认出2个字即命中)
    :return: (中心x, 中心y) 或 None
    """
    for t, s, cx, cy in paddle_find(MIRE_RECT[0], MIRE_RECT[1], MIRE_RECT[2], MIRE_RECT[3], MIRE_TEXT):
        if sum(1 for ch in MIRE_TEXT if ch in t) >= 2:
            return cx, cy
    return None


def elf_screen(last_screen=''):
    """
    判断当前处于精灵秘境流程哪个界面(按SCREEN_CHECK顺序判定, 弹窗优先)
    last_screen: 上一轮识别结果, 优先复查它(界面没变时第一二次识别就命中, 省时间)
    :return: gain成功结算 / lose失败弹窗 / fight战斗中 / ready挑战按钮
             / select玩法列表 / main主界面 / unknown
    """
    names = list(SCREEN_CHECK)
    if last_screen:
        # 稳定排序: 上一轮的界面提到最前, 其余保持弹窗优先的顺序
        names.sort(key=lambda n: n != last_screen)
    for name in names:
        if SCREEN_CHECK[name]():
            return name
    return 'unknown'


def elf_main(user32):
    """
    精灵秘境挂机(功能5, 用 python YBXZ.py 5 运行)
    和地下城挂机同构: 每轮先识别当前界面, 再按界面做对应处理
    带断网监控(后台线程)和看门狗, 连续失败3次自动停止
    """
    hwnd_bound = bind_target(user32)
    # 后台断网监控: 断网暂停, 恢复后重启游戏继续挂
    threading.Thread(target=net_monitor_bg, daemon=True).start()
    fail_count = 0  # 连续失败次数(成功后清零)
    lose_counted = False  # 当前这个失败弹窗是否已计入失败(防止没关掉时重复累计)
    idle_since = time.time()  # 上次识别到已知界面的时间, 用于看门狗
    last_screen = ''  # 上次识别到的界面, 变化时才打日志
    unknown_debug_t = 0  # unknown诊断(截图+全屏OCR)上次执行时间
    screen = elf_screen()  # 启动时全量判定一次当前界面, 后续按预期流程只查单区域

    while 1:
        # ===== 断网中: 暂停一切游戏操作 =====
        if net_offline.is_set():
            sleep(2)
            continue
        # ===== 网络恢复: 重启游戏重新登录 =====
        if net_restart.is_set():
            net_restart.clear()
            print(time.strftime('%H:%M:%S'), '网络已恢复, 重启游戏继续挂机...')
            hwnd_bound = launch_game(user32, hwnd_bound, mark=True)
            idle_since = time.time()
            continue
        # ===== 窗口存活检查 =====
        ok, hwnd_bound, relaunched = keep_window(user32, hwnd_bound)
        if not ok:
            return
        if relaunched:
            idle_since = time.time()
            continue

        # ===== 识别当前界面并分发处理 =====
        # 预期界面优先: 正常流程下下一个界面是固定的, 每轮只查它(1次识别, 省时间);
        # 预期没出现才全量重判(弹窗/异常/加载中等情况)
        expected = ELF_NEXT.get(screen, 'main')
        if SCREEN_CHECK[expected]():
            screen = expected
        else:
            screen = elf_screen(last_screen)
        if screen != last_screen:
            print(time.strftime('%H:%M:%S'), '当前界面:', screen)
            last_screen = screen
            sleep(2)  # 界面流转后延时2秒再OCR(页面加载慢, 1秒就读会校验失败)
        if screen != 'unknown':
            idle_since = time.time()

        if screen == 'gain':
            # 挑战成功: 获得物品弹窗 -> 点坐标领奖励, 失败计数清零
            fail_count = 0
            print(time.strftime('%H:%M:%S'), '挑战成功! 领取奖励')
            dm.MoveTo(*GAIN_CLICK)
            sleep(0.5)
            dm.LeftClick()
            paddle_cache.clear()
            sleep(2)  # 界面流转后延时再识别
            continue
        if screen != 'lose':
            lose_counted = False  # 离开失败弹窗后, 下次失败才重新计数
        if screen == 'lose':
            # 挑战失败: 参考地下城点击空白逻辑关弹窗回主界面
            # 同一个失败弹窗只计一次数(没关掉会重复点, 但不重复累计)
            if not lose_counted:
                lose_counted = True
                fail_count += 1
                print(time.strftime('%H:%M:%S'), '挑战失败(%d/%d), 点空白回主界面...' % (fail_count, MIRE_FAIL_MAX))
                if fail_count >= MIRE_FAIL_MAX:
                    raise RuntimeError('精灵秘境连续失败%d次, 战力不足以挂机, 停止脚本!' % MIRE_FAIL_MAX)
            # 优先识别"点击空白"文字, 点文字下方20px(点文字本身无效)
            pos = text_locate(BLANK_RECT, '点击空白', dy=20)
            if pos is not None:
                print(time.strftime('%H:%M:%S'), '点击空白(%s, %s)...' % pos)
                dm.MoveTo(*pos)
            else:
                dm.MoveTo(*page_pt(BLANK_CLICK))  # 识别不到就点固定空白位置
            sleep(0.5)
            dm.LeftClick()
            paddle_cache.clear()
            sleep(2)
            continue
        if screen == 'fight':
            # 战斗中: 剩余时间还在就继续等
            if elf_fighting():
                sleep(3)
                continue
            # 剩余时间结束: 先专门等领取奖励/挑战失败弹窗(最多10秒),
            # 结算弹窗有延迟, 直接全量重判会误判成main界面
            print(time.strftime('%H:%M:%S'), '剩余时间结束, 等待结算弹窗...')
            result = ''
            for _ in range(10):
                sleep(1)
                if text_locate(GAIN_RECT, '获得物品') is not None:
                    result = 'gain'
                    break
                if text_locate(LOSE_RECT, '战斗失败') is not None:
                    result = 'lose'
                    break
            if result:
                # 交给gain/lose分支统一处理(领奖励/计数点空白)
                screen = result
            else:
                # 10秒都没等到弹窗: 全量重判(可能界面有变)
                screen = elf_screen()
                if screen == 'fight':
                    last_screen = 'fight'  # 弹了又消失/没弹, 回到战斗等待
            continue
        if screen == 'ready':
            # 秘境界面: 点挑战开打
            print(time.strftime('%H:%M:%S'), '点击挑战...')
            click_text(CHALLENGE_RECT, '挑战')
            paddle_cache.clear()
            sleep(2)  # 页面加载慢, 立刻识别会校验失败
            continue
        if screen == 'select':
            # 玩法列表: 定位精灵秘境并点击
            pos = elf_find_mire()
            if pos is not None:
                print(time.strftime('%H:%M:%S'), '点击精灵秘境(%s, %s)' % pos)
                dm.MoveTo(pos[0], pos[1])
                sleep(0.5)
                dm.LeftClick()
                paddle_cache.clear()
            sleep(2)  # 页面加载慢, 放缓节奏防校验失败连点(实测连点16次)
            continue
        if screen == 'main':
            # 主界面: 点玩法打开列表
            print(time.strftime('%H:%M:%S'), '点击玩法...')
            click_text(WANFA_RECT, '玩法')
            paddle_cache.clear()
            sleep(2)
            continue
        # unknown: 不做动作, 交给看门狗(60秒ESC恢复)
        if time.time() - idle_since > 60:
            print(time.strftime('%H:%M:%S'), '连续60秒未识别到已知界面, 按ESC尝试恢复')
            dm.KeyPress(27)
            sleep(1)
            idle_since = time.time()
        if time.time() - unknown_debug_t > 30:
            unknown_debug_t = time.time()
            unknown_debug_dump()
        sleep(0.5)


# ================== 地下城挂机(功能6, 默认) ==================
def which_screen():
    """
    全量重判地下城流程当前状态(预期流程不符时调用), 按浮层到界面依次判定
    :return: 'battle'战斗中(弃权/一队失败) / 'again'再次挑战 / 'blank'点击空白
             / 'pass'地下城通过确定 / 'confirm'确定 / 'depart'队伍出发
             / 'dungeon'地下城界面 / 'town'城镇 / 'unknown'
    """
    # 判定顺序: 结算页元素(再次挑战/点击空白)优先于战斗锚点——
    # 失败结算页上战斗底栏(弃权)和再次挑战会同时可见, 先判again才能正确进结算流程
    # 全部quick=True: 重判只做状态识别, 单档检测(冷缓存实测7s -> 预期~2s)
    if text_locate(PASS_RECT, '确定', quick=True) is not None:
        return 'pass'
    if text_locate(CONFIRM_RECT, '确定', quick=True) is not None:
        return 'confirm'
    if text_locate(AGAIN_RECT, '再次挑战', quick=True) is not None:
        return 'again'
    if text_locate(page_rect(BLANK_RECT), '点击空白', quick=True) is not None:
        return 'blank'
    if battle_anchor_visible():
        return 'battle'
    if fail_color_hit():
        return 'battle'
    if text_locate(DEPART_RECT, '队伍出发', quick=True) is not None:
        return 'depart'
    if text_locate(DUNGEON_RECT, '第章', quick=True) is not None:
        return 'dungeon'
    if text_locate(TOWN_RECT, '地下城', quick=True) is not None:
        return 'town'
    return 'unknown'


# ---- 英雄远征: 状态判定(纯谓词, 不依赖流程局部状态; 原 expedition_main 内嵌函数提升) ----
def exp_level_page_visible():
    """层数关卡界面判定(不校验具体层数): 区域文字含数字+层"""
    for t, s, cx, cy in paddle_find(*EXP_LEVEL_RECT, '层'):
        if '层' in t and any(ch.isdigit() for ch in t):
            return True
    return False


def exp_level_ok(target_floor):
    """层数匹配判定: 已读层时要求层数一致; 未读取(target_floor=0)时
    任意'第N层'标题都算(实测: 刚进地图页就被重判, 用'第0层'匹配永远失败,
    level_back护栏失效误点放弃)"""
    target = str(target_floor) if target_floor else ''
    for t, s, cx, cy in paddle_find(*EXP_LEVEL_RECT, '层'):
        if '层' in t and any(ch.isdigit() for ch in t):
            if not target or target in t:
                return True
    return False


def exp_read_floor():
    """从层数关卡界面读取用户所选层数数字, 失败返回0"""
    for t, s, cx, cy in paddle_find(*EXP_LEVEL_RECT, '层数'):
        digits = re.findall(r'\d+', t)
        if digits:
            return int(digits[0])
    return 0


def exp_find_sub():
    """全屏检测找"前往"按钮, 只认守卫选择页的位置区
    (x 150~300: 层数列表每行也有前往按钮@x340, 不限x会误认, 实测150%布局;
     y > EXP_SUB_MIN_Y: 排除上半屏)"""
    for t, s, cx, cy in paddle_find(*EXP_SUB_SEARCH, '前往'):
        # 前往=前+往(没有去字!); text_locate对2字标签要求全中, 用'去'永远匹配不上
        if ('前' in t and '往' in t and cy > EXP_SUB_MIN_Y
                and 150 <= cx <= 300):
            return cx, cy
    return None


def exp_check_fight():
    """弃权检测: 小框识别在战斗特效下偶发miss, miss时清缓存重试一次
    (实测: 第二次进fight时过渡期miss被负缓存放大成19秒unknown)"""
    if text_locate(EXP_FIGHT_RECT, '弃权') is not None:
        return True
    paddle_cache.clear()
    return text_locate(EXP_FIGHT_RECT, '弃权') is not None


EXP_STATE_CHECK = {
    'floor': lambda: text_locate(EXP_SELECTED_RECT, '开始远征') is not None,
    'level': exp_level_page_visible,
    'sub': lambda: exp_find_sub() is not None,
    'challenge': lambda: text_locate(EXP_CHALLENGE_RECT, '开始挑战') is not None,
    'fight': exp_check_fight,
    'bonus': lambda: text_locate(EXP_BONUS_RECT, '选择加成') is not None,
    'level_back': lambda: text_locate(EXP_ABANDON_RECT, '放弃') is not None,
    'confirm': lambda: text_locate(EXP_CONFIRM_RECT, '确定') is not None,
    'title': lambda: text_locate(EXP_TITLE_RECT, '英雄远征') is not None,
}
EXP_NEXT = {
    'floor': 'level', 'level': 'sub', 'sub': 'challenge',
    'challenge': 'fight', 'fight': 'bonus', 'bonus': 'level_back',
    'level_back': 'confirm', 'confirm': 'scroll', 'scroll': 'floor',
}


def exp_resync(target_floor):
    """全量重判远征流程状态(按浮层优先)
    界面流转: 层数列表(floor) -> 地图页(level,常驻放弃) -> 守卫选择(sub,前往)
    -> 布阵(challenge,开始战斗) -> 战斗(fight,弃权) -> 胜利弹选择加成(bonus)"""
    if text_locate(EXP_BONUS_RECT, '选择加成') is not None:
        return 'bonus'
    if text_locate(EXP_CONFIRM_RECT, '确定') is not None:
        return 'confirm'
    # 弃权只在战斗界面存在, 必须先于放弃判定
    if exp_check_fight():
        return 'fight'
    # 放弃判定加护栏: 关卡页(第65层标题+放弃按钮同屏)过渡期会被误判成level_back,
    # 点了放弃整个流程就搅了(实测15:49:01)
    if text_locate(EXP_ABANDON_RECT, '放弃') is not None and not exp_level_ok(target_floor):
        return 'level_back'
    if text_locate(EXP_CHALLENGE_RECT, '开始挑战') is not None:
        return 'challenge'
    if text_locate(EXP_SELECTED_RECT, '开始远征') is not None:
        return 'floor'
    # 层数列表但未选中(无开始远征按钮, 提示'请选择要挑战的层数'):
    # 交给scroll分支点选目标层, 选完开始远征出现自然回到floor
    for t, s, cx, cy in paddle_find(0, 100, CLIENT_SIZE[0], 700, '第'):
        if '第' in t and '层' in t and cy > 200 and any(ch.isdigit() for ch in t):
            return 'scroll'
    if exp_level_ok(target_floor):
        return 'level'
    # sub的全屏检测最贵(2~4秒), 放最后兜底; 层数列表页是重判最常见落点
    if exp_find_sub() is not None:
        return 'sub'
    return 'unknown'


class _ExpRun(object):
    """远征主循环可变状态(原expedition_main局部变量, 状态handler需跨分支读写)"""
    def __init__(self):
        self.target_floor = 0     # 用户选择的层数(进入层数关卡界面后自动读取)
        self.state = 'unknown'
        self.wait_until = 0       # 切页等待窗: 窗内不做全量重判
        self.battle_started = 0   # 进入战斗的时刻(战斗超时看门狗用)
        self.swipes = 0           # scroll分支已滚动次数
        self.unknown_debug_t = 0  # unknown/miss诊断(截图+全屏OCR)上次执行时间


def _exp_st_floor(run):
    """层数列表(已选层): 点击开始远征
    返回True=继续循环(原continue) / False=停止挂机(原return)"""
    pos = text_locate(EXP_SELECTED_RECT, '开始远征')
    if pos is not None:
        print(time.strftime('%H:%M:%S'), '点击开始远征...')
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        run.state = 'level'
        run.wait_until = time.time() + 2
        return True
    if text_locate(EXP_TITLE_RECT, '英雄远征') is not None:
        print(time.strftime('%H:%M:%S'), '层数未选择(无开始远征按钮), 请手动选择层数后重新执行脚本')
        return False
    sleep(1)
    return True


def _exp_st_level(run):
    """远征地图页(第65层标题+六边形地图, 右上角放弃): 盲点地图节点进守卫选择。
    每轮放弃后从列表重进, 地图重置回起点, 固定坐标稳定命中首个守卫"""
    # 首次读取用户所选层数(页面加载需要时间, 重试10秒), 之后校验层数一致再进入
    if run.target_floor == 0:
        n = 0
        for _ in range(10):
            n = exp_read_floor()
            if n:
                break
            sleep(1)
        if not n:
            print(time.strftime('%H:%M:%S'), '无法读取所选层数, 请确认已选择层数')
            return False
        run.target_floor = n
        print(time.strftime('%H:%M:%S'), '已读取用户选择的层数: 第%d层' % n)
    print(time.strftime('%H:%M:%S'), '点击进入第%d层关卡...' % run.target_floor)
    dm.MoveTo(*EXP_LEVEL_CLICK)
    sleep(0.5)
    dm.LeftClick()
    sleep(2)  # 页面切换动画约3秒, 立刻识别'前往'必然miss(实测)
    run.state = 'sub'
    run.wait_until = time.time() + 2
    return True


def _exp_st_sub(run):
    """小关选择: 点击前往(按钮可能有呼吸动画, 识别偶发miss, 原地重试3次)"""
    pos = exp_find_sub()
    for _ in range(3):
        if pos is not None:
            break
        paddle_cache.clear()  # 清掉切换期的miss负缓存, 重试才是真识别
        sleep(1.5)
        pos = exp_find_sub()
    if pos is not None:
        print(time.strftime('%H:%M:%S'), '点击前往...')
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        run.state = 'challenge'
        run.wait_until = time.time() + 2
    else:
        # 别的机器布局可能偏移导致前往定位不到, 存现场图排查
        # (实测: 有机器在此循环回floor, 需要看他们的实际界面)
        if time.time() - run.unknown_debug_t > 30:
            run.unknown_debug_t = time.time()
            unknown_debug_dump()
        run.state = exp_resync(run.target_floor)
    return True


def _exp_st_challenge(run):
    """布阵页: 点击开始挑战(加载动画期会miss, 原地清缓存重试,
    实测miss后重判弹回sub多绕35秒)"""
    pos = text_locate(EXP_CHALLENGE_RECT, '开始挑战')
    for _ in range(2):
        if pos is not None:
            break
        paddle_cache.clear()
        sleep(1.5)
        pos = text_locate(EXP_CHALLENGE_RECT, '开始挑战')
    if pos is not None:
        print(time.strftime('%H:%M:%S'), '点击开始挑战...')
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        run.state = 'fight'
        run.battle_started = time.time()
        run.wait_until = time.time() + 2
    else:
        run.state = exp_resync(run.target_floor)
    return True


def _exp_st_fight(run):
    """战斗中: 弃权按钮还在就5秒一查; 消失说明打完了"""
    if run.battle_started == 0:
        run.battle_started = time.time()
    if time.time() - run.battle_started > EXP_BATTLE_TIMEOUT:
        print(time.strftime('%H:%M:%S'), '战斗超时, 全量重判')
        run.battle_started = time.time()
        run.state = exp_resync(run.target_floor)
        return True
    # 点开始挑战后可能弹确定弹窗(非100%), 优先关掉它
    if text_locate(EXP_CONFIRM_RECT, '确定') is not None:
        print(time.strftime('%H:%M:%S'), '关闭确定弹窗...')
        pos = text_locate(EXP_CONFIRM_RECT, '确定')
        if pos is not None:
            dm.MoveTo(*pos)
            sleep(0.5)
            dm.LeftClick()
            paddle_cache.clear()
            sleep(2)  # 战斗页加载动画, 立刻识别弃权会miss并被负缓存放大(实测19秒unknown)
            run.wait_until = time.time() + 8
        return True
    if exp_check_fight():
        # 弃权还在=战斗没打完, 刷新等待窗让识别阶段别去重判
        # (实测: 战斗结束瞬间页面过渡, 重判会把加成页误判成level)
        run.wait_until = time.time() + 5
        sleep(3)
        return True
    # 弃权刚消失: 可能是结束过渡动画, 稳一下二次确认
    sleep(1.5)
    if text_locate(EXP_FIGHT_RECT, '弃权', quick=True) is not None:
        return True
    paddle_cache.clear()
    sleep(1)  # 等结算页加载完再判定胜负
    # 战斗可能失败: 失败弹"战斗失败/提升战力"页(没有选择加成, 实测22:48),
    # 不处理会误当加成页点坐标, 有误点'前往'离开远征的风险; 按用户要求直接停止
    if text_locate(EXP_LOSE_RECT, '失败', quick=True) is not None:
        print(time.strftime('%H:%M:%S'), '战斗失败, 脚本停止(请手动处理后重跑)')
        return False
    print(time.strftime('%H:%M:%S'), '战斗结束, 等待选择加成...')
    run.state = 'bonus'
    run.wait_until = time.time() + 5
    return True


def _exp_st_bonus(run):
    """选择加成: 查奖励技能区域是否含眩晕, 有→停止(刷眩晕成功)"""
    texts = paddle_find(*EXP_STUN_RECT, '眩晕')
    hit = any('眩' in t and '晕' in t for t, s, c1, c2 in texts)
    if hit:
        print(time.strftime('%H:%M:%S'), '刷眩晕成功! 奖励技能含眩晕, 脚本停止')
        return False
    print(time.strftime('%H:%M:%S'), '无眩晕, 点击选择加成上方坐标继续...')
    dm.MoveTo(*EXP_BONUS_CLICK)
    sleep(0.5)
    dm.LeftClick()
    sleep(1)  # 加成页关闭有动画, 立刻找放弃会落空(实测)
    run.state = 'level_back'
    run.wait_until = time.time() + 3
    return True


def _exp_st_level_back(run):
    """回到层数关卡界面: 点击放弃(关闭加成页有动画, miss后清缓存重试,
    不然下一轮战斗胜利加成页又弹回来, 死循环bonus<->level_back)"""
    pos = text_locate(EXP_ABANDON_RECT, '放弃')
    for _ in range(3):
        if pos is not None:
            break
        paddle_cache.clear()
        sleep(1)
        pos = text_locate(EXP_ABANDON_RECT, '放弃')
    if pos is not None:
        print(time.strftime('%H:%M:%S'), '点击放弃...')
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        run.state = 'confirm'
        run.wait_until = time.time() + 2
        return True
    if exp_level_ok(run.target_floor):
        sleep(1)  # 页面还在加载, 继续等放弃按钮
        return True
    run.state = exp_resync(run.target_floor)
    return True


def _exp_st_confirm(run):
    """确认弹窗: 开始战斗确认和放弃确认长得一模一样, 点完不能盲跳scroll
    (实测: 把开始战斗确认当放弃确认, 战斗在后台打却去滚列表找层)"""
    pos = text_locate(EXP_CONFIRM_RECT, '确定')
    for _ in range(2):  # 弹窗淡入期会miss, 原地重试; 立刻重判会弹回level_back多点放弃
        if pos is not None:
            break
        paddle_cache.clear()
        sleep(1)
        pos = text_locate(EXP_CONFIRM_RECT, '确定')
    if pos is not None:
        print(time.strftime('%H:%M:%S'), '点击确定...')
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        paddle_cache.clear()
        # 等页面定型, 按证据路由: 弃权出现=进战斗; 目标层行出现=放弃回列表
        target_text = '第%d层' % run.target_floor
        routed = ''
        deadline = time.time() + 12
        while time.time() < deadline:
            sleep(1.5)
            if text_locate(EXP_FIGHT_RECT, '弃权', quick=True) is not None:
                routed = 'fight'
                break
            paddle_cache.clear()
            for t, s, cx, cy in paddle_find(0, 100, CLIENT_SIZE[0], 700, target_text):
                # 目标层在视野: 直接选它; 不在但列表可见: 也进scroll去拖拽找
                if target_text in t and cy > 200:
                    routed = 'scroll'
                    break
                if '第' in t and '层' in t and cy > 200:
                    routed = 'scroll'  # 列表可见但目标不在视野
            if routed:
                break
        if routed == 'fight':
            print(time.strftime('%H:%M:%S'), '确认后进入战斗...')
            run.state = 'fight'
            run.battle_started = time.time()
        else:
            print(time.strftime('%H:%M:%S'), '确认完成, 回层数列表...')
            run.state = 'scroll'
            run.swipes = 0
            run.wait_until = 0
        return True
    run.state = exp_resync(run.target_floor)
    return True


def _exp_st_scroll(run):
    """放弃后回层数列表: 选中状态会被清空, 需重新点击目标层数再开始远征"""
    pos = text_locate(EXP_SELECTED_RECT, '开始远征')
    if pos is not None:
        print(time.strftime('%H:%M:%S'), '点击开始远征...')
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        sleep(2)  # 页面过渡2~3秒, 立刻重判会抓到带放弃按钮的过渡帧(实测)
        run.state = 'level'
        run.wait_until = time.time() + 2
        run.swipes = 0
        return True
    # 放弃后可能停在远征地图页(顶部第N层标题+右上放弃, 地图无法拖动找层):
    # 若放弃确认弹窗还开着先点确定; 否则点右上角放弃退出本层, 确认后回层数列表
    mhit = None
    for t, s, cx, cy in paddle_find(150, 100, 300, 160, '层'):
        m = re.search(r'第(\d+)层', t)
        if m:
            mhit = m.group(0)
            break
    if mhit:
        print(time.strftime('%H:%M:%S'), '停在远征地图页(%s), 点放弃退出本层...' % mhit)
        popup = text_locate(EXP_CONFIRM_RECT, '确定', quick=True)
        if popup is not None:
            dm.MoveTo(*popup)
            sleep(0.5)
            dm.LeftClick()
            paddle_cache.clear()
            sleep(2)
            return True
        apos = text_locate(EXP_ABANDON_RECT, '放弃')
        if apos is not None:
            dm.MoveTo(*apos)
            sleep(0.5)
            dm.LeftClick()
            paddle_cache.clear()
            run.state = 'confirm'
            run.wait_until = time.time() + 5
            return True
    # 没有开始远征按钮: 找目标层数文字, 点击重新选择
    # (精确匹配"第66层"全文, 避免模糊匹配把65层认成66层)
    if run.target_floor == 0:
        # 正式上线: 层数必须用户自己在游戏里点选, 脚本不替用户做主
        print(time.strftime('%H:%M:%S'),
              '未读取到挑战层数, 请在层数列表手动点选要挑战的层数后重新执行脚本')
        return False
    target_text = '第%d层' % run.target_floor
    pos = None
    vis = []
    for t, s, cx, cy in paddle_find(0, 100, CLIENT_SIZE[0], 700, target_text):
        # 页面标题也是"第52层"且永远在最上面, 点它无效; 只认列表里的层行(y>200)
        m = re.search(r'第(\d+)层', t)
        if m and cy > 200:
            vis.append(int(m.group(1)))
        if target_text in t and cy > 200:
            pos = (cx, cy)
            break
    if pos is not None:
        print(time.strftime('%H:%M:%S'), '找到第%d层, 点击重新选择...' % run.target_floor)
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        sleep(1)
        run.swipes = 0
        return True
    if run.swipes >= EXP_SWIPE_MAX:
        print(time.strftime('%H:%M:%S'), '滚动%d次仍未找到第%d层, 请手动检查' % (EXP_SWIPE_MAX, run.target_floor))
        return False
    print(time.strftime('%H:%M:%S'), '拖拽滚动寻找第%d层...(%d/%d)' % (run.target_floor, run.swipes + 1, EXP_SWIPE_MAX))
    # 拖完必须清OCR缓存, 否则下轮读到的是拖前旧行
    paddle_cache.clear()
    if vis and run.target_floor > max(vis):
        dm_drag_steps(223, 500, -EXP_SWIPE_DISTANCE)  # 目标在视野下方, 向上拖
    else:
        dm_drag_steps(223, 500, EXP_SWIPE_DISTANCE)   # 目标在视野上方, 向下拖
    run.swipes += 1
    sleep(1)
    return True


_EXP_HANDLERS = {
    'floor': _exp_st_floor, 'level': _exp_st_level,
    'sub': _exp_st_sub, 'challenge': _exp_st_challenge,
    'fight': _exp_st_fight, 'bonus': _exp_st_bonus,
    'level_back': _exp_st_level_back, 'confirm': _exp_st_confirm,
    'scroll': _exp_st_scroll,
}


def expedition_main(user32):
    """
    英雄远征刷眩晕(功能4): 反复打指定层第一关, 查奖励技能是否有眩晕
    有→停止(成功); 没有→放弃→重新进入→循环
    前提: 手动进入英雄远征界面, 并手动选好层数
    """
    if FIXED_HWND:
        u32 = ctypes.windll.user32
        if not u32.IsWindow(FIXED_HWND):
            raise RuntimeError('多账号模式: 指定的游戏窗口已关闭')
        hwnd2 = FIXED_HWND
        print(time.strftime('%H:%M:%S'), '多账号模式, 绑定指定窗口:', hwnd2)
    else:
        hwnd2 = dm.FindWindow('Chrome_WidgetWin_0', GAME_WINDOW_TITLE)
        if hwnd2 == 0:
            raise RuntimeError('未找到游戏窗口, 请先打开游戏并进入英雄远征界面')
    bind_game(hwnd2)
    threading.Thread(target=net_monitor_bg, daemon=True).start()

    target_floor = 0  # 用户选择的初始层数(进入层数关卡界面后自动读取)

    # 启动校验: 从远征流程的任何一页都能恢复(层数列表/地图/守卫/布阵/战斗/加成),
    # 只认标题会拒绝战斗页等无标题页面(实测: 上次中途停止留了场战斗, 重跑被拒)
    # 3次校验兜页面过渡就够了(全量重判一轮就要好几秒, 次数多了不在远征界面时
    # 要干等一分钟才报错, 实测66秒); 每轮打进度日志, 不让界面看着像卡死
    print(time.strftime('%H:%M:%S'), '校验是否在英雄远征流程中...')
    state = 'unknown'
    for attempt in range(3):
        t0 = time.time()
        state = exp_resync(target_floor)
        if state != 'unknown':
            break
        print(time.strftime('%H:%M:%S'), '第%d次校验未识别到远征界面(耗时%.1f秒)%s'
              % (attempt + 1, time.time() - t0,
                 ', 重试...' if attempt < 2 else ', 放弃'))
        if attempt < 2:
            sleep(1.5)
    if state in ('unknown', 'scroll'):
        # unknown=不在远征流程; scroll=在远征界面但还没选层数(层数列表无开始远征按钮)
        print(time.strftime('%H:%M:%S'), '请先进入远征界面选择层数后再运行脚本')
        return
    print(time.strftime('%H:%M:%S'), '已在英雄远征流程中, 当前状态:', state)
    run = _ExpRun()
    run.state = state
    state_since = time.time()
    unknown_since = 0
    last_log = ''

    while 1:
        if net_offline.is_set():
            sleep(2)
            continue
        if net_restart.is_set():
            net_restart.clear()
            print(time.strftime('%H:%M:%S'),
                  '网络已恢复, 重启游戏重新登录(与其他账号排队); 此后只做网络/窗口监控保活, 远征需手动重跑继续')
            try:
                # 传FIXED_HWND(旧窗口): 关旧窗口/交接/重启标记都用它, UI日志页得以保留改挂
                new_hwnd = launch_game(user32, FIXED_HWND or 0, mark=True)
            except Exception as e:
                print(time.strftime('%H:%M:%S'), '重启游戏失败: %r' % e)
                return
            if FIXED_HWND:
                globals()['FIXED_HWND'] = new_hwnd  # 监控跟踪新窗口, 不然按标题找会抢别的账号
            monitor_main(user32)  # 转入纯监控(断网/掉线弹窗/窗口消失都自动重启保活)
            return
        # 多账号: 大窗口/渲染子窗口关闭时停止; 单账号: 游戏没了提示停止
        if FIXED_HWND:
            u32 = ctypes.windll.user32
            if not u32.IsWindow(bound_top):
                print(time.strftime('%H:%M:%S'), '大窗口已关闭, 停止本账号挂机')
                return
            if bound_child and not u32.IsWindow(bound_child):
                print(time.strftime('%H:%M:%S'), '渲染子窗口已关闭, 停止本账号挂机')
                return
        else:
            if dm.FindWindow('Chrome_WidgetWin_0', GAME_WINDOW_TITLE) == 0:
                print(time.strftime('%H:%M:%S'), '游戏窗口消失, 请重新进入英雄远征界面后重跑')
                return

        # ===== 状态识别: 预期状态优先, 切页等待窗内不重判, 其余全量重判 =====
        expected = EXP_NEXT.get(run.state, run.state)
        if expected in EXP_STATE_CHECK and EXP_STATE_CHECK[expected]():
            run.state = expected
        elif run.state == 'scroll':
            # 滚动找层: 视野内没有可识别的状态文字是正常现象, 由scroll分支边滑边找
            pass
        elif time.time() < run.wait_until:
            sleep(0.5)
        else:
            new_state = exp_resync(run.target_floor)
            if new_state != run.state:
                run.state = new_state
                state_since = time.time()
        if run.state != last_log:
            print(time.strftime('%H:%M:%S'), '当前状态:', run.state)
            last_log = run.state
        if run.state != 'unknown':
            unknown_since = 0

        # ===== 按状态处理: handler返回False=停止挂机(原return) =====
        h = _EXP_HANDLERS.get(run.state)
        if h is not None:
            if not h(run):
                return
            continue

        # unknown: 不做动作, 60秒无法识别就退出
        if unknown_since == 0:
            unknown_since = time.time()
        if time.time() - unknown_since > 60:
            print(time.strftime('%H:%M:%S'), '连续60秒无法识别远征界面, 请手动检查后重跑')
            return
        if time.time() - run.unknown_debug_t > 30:
            run.unknown_debug_t = time.time()
            unknown_debug_dump()
        sleep(1)



def state_click(state_key, rect, text, page=True, max_cache_age=600):
    """状态目标点击: 优先用记忆坐标(毫秒级), 异常/无记忆再OCR定位并记忆
    记忆超过10分钟或unknown后清空, 防止页面结构变化后点错位置"""
    pos = pos_cache.get(state_key)
    if pos is not None and time.time() - pos_cache.get(state_key + '_t', 0) < max_cache_age:
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        paddle_cache.clear()  # 点击后界面将变, 清OCR缓存防下轮判到点击前的旧画面
        return pos
    r = page_rect(rect) if page else rect
    pos = text_locate(r, text)
    if pos is not None:
        dm.MoveTo(*pos)
        sleep(0.5)
        dm.LeftClick()
        paddle_cache.clear()
        pos_cache[state_key] = pos
        pos_cache[state_key + '_t'] = time.time()
    return pos


def main(action=None, fixed_hwnd=None, game_lnk=None):
    """挂机入口(参数为显式接口, 供界面进程内调用; 不传则沿用模块全局,
    CLI路径由__main__块设好全局后无参调用, 两条路径行为一致):
      action: 挂机模式 '4'远征/'5'秘境/'6'地下城/'7'网络监控
      fixed_hwnd: 多账号指定窗口句柄(None=单账号自动查找)
      game_lnk: 多账号各自的重启快捷方式文件名
    """
    g = globals()
    if action is None:
        # CLI路径: __main__块已设全局action, 参数回落读全局(局部参数会遮蔽同名全局,
        # 不读回的话派发全不中, 实测所有模式都落进地下城兜底分支)
        action = g.get('action', '6')
    else:
        g['action'] = str(action)  # in-process路径: 显式参数同步到全局(监控线程等读全局)
    if fixed_hwnd is not None:
        g['FIXED_HWND'] = int(fixed_hwnd)
    if game_lnk:
        g['GAME_LNK'] = game_lnk
    user32 = ctypes.windll.user32
    if action == '4':
        # 英雄远征刷眩晕
        expedition_main(user32)
        return
    if action == '5':
        # 精灵秘境挂机
        elf_main(user32)
        return
    if action == '7':
        # 网络监控+重启游戏
        monitor_main(user32)
        return
    # ===== 地下城自动(默认) =====
    sleep(2)
    hwnd_bound = bind_target(user32)
    # 后台断网监控: 断网暂停, 恢复后重启游戏继续挂
    # (实测: 此前地下城漏启动监控线程, 断网后毫无反应继续点弹窗)
    threading.Thread(target=net_monitor_bg, daemon=True).start()
    idle_since = time.time()  # unknown状态持续计时, 用于看门狗
    entry_clicked_at = 0  # 点击地下城入口的时间, 30秒没进去就ESC
    battle_started = 0  # 点击队伍出发的时间, 战斗超时兜底用
    wait_until = 0  # 点击流转后的切页等待窗截止时间, 窗内不做全量重判
    unknown_debug_t = 0  # unknown诊断(截图+全屏OCR)上次执行时间
    resync_next = 0  # 全量重判的冷却截止时间(页面加载期不做高频OCR)
    last_depart_time = 0  # 上次点击队伍出发的时间(出发回弹检测用)
    depart_bounce = 0  # 点出发后被弹回城镇的连续次数
    fail_click_miss = 0  # 一队失败后点弃权连续失败的次数
    battle_anchor_miss = 0  # 战斗中弃权锚点连续消失的轮数(防战斗页被手动关闭后卡到超时)
    offline_check_t = 0  # 掉线弹窗上次检测时间(每10秒查一次, 防每轮OCR拖慢循环)
    last_screen = ''
    state = which_screen()  # 启动时全量判定一次当前状态
    resync_next = time.time() + 4  # 判完给4秒冷却, 防首轮立刻又重判一次

    while 1:
        # ===== 断网中: 暂停一切游戏操作 =====
        if net_offline.is_set():
            sleep(2)
            continue
        # ===== 网络恢复: 重启游戏重新登录, 接着挂机 =====
        if net_restart.is_set():
            net_restart.clear()
            print(time.strftime('%H:%M:%S'), '网络已恢复, 重启游戏继续挂机...')
            hwnd_bound = launch_game(user32, hwnd_bound, mark=True)
            state = which_screen()
            resync_next = time.time() + 4
            continue
        # ===== 窗口存活检查 =====
        ok, hwnd_bound, relaunched = keep_window(user32, hwnd_bound)
        if not ok:
            return
        if relaunched:
            reset_page_calibration()  # 游戏重启后需重新校准页面偏移
            state = which_screen()
            resync_next = time.time() + 4
            continue

        # ===== 游戏掉线弹窗检测: 系统网络正常但游戏会话掉线时, 断网监控不触发 =====
        # (实测: 会话掉线后反复弹确定, worker与弹窗死磕不出活)
        if time.time() - offline_check_t > 10:
            offline_check_t = time.time()
            if text_locate(OFFLINE_RECT, OFFLINE_TEXT, quick=True) is not None:
                print(time.strftime('%H:%M:%S'), '检测到游戏掉线弹窗, 重启游戏...')
                hwnd_bound = launch_game(user32, hwnd_bound, mark=True)
                reset_page_calibration()
                state = which_screen()
                resync_next = time.time() + 4
                continue

        # ===== 状态识别: 预期状态优先(1次识别), 不符再全量重判 =====
        expected = FLOW_NEXT.get(state, state)
        if expected in STATE_CHECK and STATE_CHECK[expected]():
            state = expected
        elif time.time() < wait_until:
            # 刚点击完, 游戏切页需要时间, 窗内预期界面没出现不重判;
            # 但若当前状态自己的界面已出现(实测弹窗/切页只要1~3秒),
            # 立刻结束等待进入分支处理, 不傻等满窗口
            if state not in STATE_CHECK or not STATE_CHECK[state]():
                sleep(1)
                continue
        elif time.time() < resync_next:
            # 全量重判冷却: 页面加载期高频OCR会拖慢游戏加载, 只等
            sleep(1)
            continue
        else:
            state = which_screen()
            resync_next = time.time() + 4  # 重判后4秒冷却
        if state != last_screen:
            print(time.strftime('%H:%M:%S'), '当前状态:', state)
            last_screen = state
            sleep(1)  # 界面流转后延时1秒再OCR
        if state != 'unknown':
            idle_since = time.time()  # 已知状态要持续刷新看门狗计时

        # ===== 按状态处理 =====
        if state == 'battle':
            depart_bounce = 0  # 成功进入战斗, 清零出发回弹计数
            last_depart_time = 0
            # 战斗中: 一队失败=失败流程; 通过确定=通关; 都没有就继续打
            if battle_started == 0:
                battle_started = time.time()  # 中途启动也能正确计超时
            if text_locate(CONFIRM_RECT, '确定', quick=True) is not None:
                # 弃权后的确定弹窗已出现(一队失败色可能还在屏上), 优先进入confirm
                state = 'confirm'
                continue
            if fail_color_hit():
                print(time.strftime('%H:%M:%S'), '队伍失败(红X), 点击弃权...')
                if state_click('surrender', page_rect(BATTLE_RECT), '弃权') is not None:
                    state = 'confirm'
                    wait_until = time.time() + 8  # 弹窗实测1~2秒就出, 窗口只做兜底
                    fail_click_miss = 0
                else:
                    # 弃权文本定位失败: 先兼容确定弹窗, 2次失败后按默认坐标盲点弃权
                    fail_click_miss += 1
                    if state_click('confirm', CONFIRM_RECT, '确定') is not None:
                        print(time.strftime('%H:%M:%S'), '弃权不可点, 已点击确定弹窗...')
                        state = 'confirm'
                        wait_until = time.time() + 6
                        fail_click_miss = 0
                    elif fail_click_miss >= 2:
                        # 一队失败色已确认在失败界面, 直接盲点弃权按钮位置
                        print(time.strftime('%H:%M:%S'), '文本定位失败, 盲点弃权按钮位置...')
                        p = page_pt((395, 745))
                        dm.MoveTo(*p)
                        sleep(0.5)
                        dm.LeftClick()
                        paddle_cache.clear()
                        if fail_click_miss >= 3:
                            fail_click_miss = 0
                            unknown_debug_dump()
                        state = 'confirm'
                        wait_until = time.time() + 8
                    sleep(1)
                continue
            if text_locate(PASS_RECT, '确定', quick=True) is not None:
                print(time.strftime('%H:%M:%S'), '地下城通过, 点击确定...')
                if state_click('pass', PASS_RECT, '确定') is not None:
                    state = 'dungeon'
                    wait_until = time.time() + 12
                sleep(1)
                continue
            # 强锚点计次: 弃权按钮是战斗中唯一可靠标志, 计时器大区域会被别的
            # 界面倒计时误匹配(关到非城镇界面时靠它, 约25秒恢复)
            if text_locate(page_rect(BATTLE_RECT), '弃权', quick=True) is None:
                battle_anchor_miss += 1
                if battle_anchor_miss >= 5:  # 约25秒, 容忍战斗特效短暂遮挡
                    print(time.strftime('%H:%M:%S'), '战斗锚点(弃权)连续消失, 全量重判状态')
                    battle_anchor_miss = 0
                    state = which_screen()
                    # 注意: 这里不重置battle_started, 300秒兜底始终有效
                    continue
            else:
                battle_anchor_miss = 0
            if time.time() - battle_started > BATTLE_TIMEOUT:
                # 超时不ESC(可能还在打), 全量重判对齐真实状态
                print(time.strftime('%H:%M:%S'), '战斗超过%s秒, 全量重判状态' % BATTLE_TIMEOUT)
                state = which_screen()
                if state == 'battle':
                    battle_started = time.time()  # 还在打, 重新计时
                continue
            sleep(3)
            continue

        if state == 'confirm':
            # 弃权后的确定弹窗: 点掉后停在confirm, 由预期检查等"点击空白"结算页出现
            # (不改状态盲跳: 弹窗生效要10~30秒, 点早了下轮预期检查还会带我们回来重点)
            if click_text(page_rect(CONFIRM_RECT), '确定') is not None:
                print(time.strftime('%H:%M:%S'), '点击确定...')
                wait_until = time.time() + 8  # 空白页实测3秒内出现, 窗口只做兜底
            else:
                state = which_screen()
            continue

        if state == 'blank':
            # 点击空白关闭结算遮挡页; 停在blank由预期检查等"再次挑战"出现
            if text_locate(page_rect(BLANK_RECT), '点击空白', quick=True) is not None:
                print(time.strftime('%H:%M:%S'), '点击空白...')
                dm.MoveTo(*page_pt(BLANK_CLICK))
                sleep(0.5)
                dm.LeftClick()
                paddle_cache.clear()  # 盲点也要清缓存, 防预期检查读到点击前的旧画面
                wait_until = time.time() + 8
            else:
                state = which_screen()
            continue

        if state == 'again':
            # 再次挑战: 直接开下一把
            if state_click('again', AGAIN_RECT, '再次挑战') is not None:
                print(time.strftime('%H:%M:%S'), '点击再次挑战...')
                sleep(1)
                state = 'depart'
                wait_until = time.time() + 8  # 实测2秒内到出发页, 窗口只做兜底
            else:
                state = which_screen()
            continue

        if state == 'pass':
            # 地下城通过确定: 点击回地下城界面
            if click_text(page_rect(PASS_RECT), '确定') is not None:
                print(time.strftime('%H:%M:%S'), '点击通过确定, 回地下城界面...')
                sleep(1)
                state = 'dungeon'
                wait_until = time.time() + 12
            else:
                state = which_screen()
            continue

        if state == 'depart':
            # 队伍出发界面: 点击出发; 出发后可能弹确定(非100%), 5秒内点掉再进战斗监控
            if state_click('depart', DEPART_RECT, '队伍出发') is not None:
                print(time.strftime('%H:%M:%S'), '点击队伍出发...')
                last_depart_time = time.time()
                sleep(1)
                for _ in range(5):
                    cpos = text_locate(CONFIRM_RECT, '确定', quick=True)
                    if cpos is None:
                        break
                    print(time.strftime('%H:%M:%S'), '关闭确定弹窗...')
                    dm.MoveTo(*cpos)
                    sleep(0.5)
                    dm.LeftClick()
                    paddle_cache.clear()
                    sleep(1)
                state = 'battle'
                battle_started = time.time()
                wait_until = time.time() + 12  # 实测出发到战斗识别要7~14秒
            else:
                state = which_screen()
            continue

        if state == 'dungeon':
            # 地下城界面: 点进入战斗准备
            entry_clicked_at = 0
            print(time.strftime('%H:%M:%S'), '点击进入战斗准备...')
            sleep(2)  # 地图刚出现时按钮未就绪, 稳定2秒再点(点太早会弹回城镇)
            dm.MoveTo(*page_pt(DUNGEON_ENTRY_CLICK))
            sleep(0.5)
            dm.LeftClick()
            paddle_cache.clear()
            sleep(1)
            state = 'depart'
            wait_until = time.time() + 20  # 出发页加载实测15~20秒, 窗内不重判不重复点击
            continue

        if state == 'town':
            # 城镇: 点地下城入口; 30秒还没进去就ESC
            pos = pos_cache.get('town_entry') or text_locate(TOWN_RECT, '地下城')
            if pos is not None:
                # 出发后20秒内被弹回城镇: 连续2次判定无法出发(如今日次数用完), 停止
                if last_depart_time and time.time() - last_depart_time < 20:
                    depart_bounce += 1
                    if depart_bounce >= 2:
                        print(time.strftime('%H:%M:%S'),
                              '点击出发后连续返回城镇(可能今日挑战次数已用完), 停止脚本')
                        return
                else:
                    depart_bounce = 0
                # 出发后12秒内被弹回城镇: 连续2次判定出发异常, 停止并存诊断图
                if last_depart_time and time.time() - last_depart_time < 12:
                    depart_bounce += 1
                    if depart_bounce >= 2:
                        print(time.strftime('%H:%M:%S'), '点击出发后连续返回城镇, 存在异常, 停止脚本')
                        unknown_debug_dump()
                        return
                else:
                    depart_bounce = 0
                # 中部出现"确定"弹窗(公告/二次确认)会挡住入口点击, 先点掉
                cpos = text_locate(CONFIRM_RECT, '确定', quick=True)
                if cpos is not None:
                    print(time.strftime('%H:%M:%S'), '关闭确定弹窗...')
                    dm.MoveTo(*cpos)
                    sleep(0.5)
                    dm.LeftClick()
                    paddle_cache.clear()
                    sleep(1)
                    continue
                if entry_clicked_at and time.time() - entry_clicked_at > 30:
                    print(time.strftime('%H:%M:%S'), '点击入口后未进入地下城界面, 按ESC恢复')
                    dm.KeyPress(27)
                    sleep(1)
                    entry_clicked_at = 0
                    unknown_debug_dump()  # 存画面+OCR, 排查是什么挡住了入口
                    continue
                if entry_clicked_at == 0:
                    entry_clicked_at = time.time()
                    print(time.strftime('%H:%M:%S'), '点击地下城入口...')
                dm.MoveTo(*pos)
                sleep(0.5)
                dm.LeftClick()
                paddle_cache.clear()
                sleep(1)
                wait_until = time.time() + 12  # 地图加载8~15秒, 窗内不重判不重复点击
            continue

        # unknown: 不做动作, 交给看门狗(60秒ESC恢复); 位置缓存清空防点错
        pos_cache.clear()
        if time.time() - idle_since > 60:
            print(time.strftime('%H:%M:%S'), '连续60秒未识别到已知状态, 按ESC尝试恢复')
            dm.KeyPress(27)
            sleep(1)
            idle_since = time.time()
        if time.time() - unknown_debug_t > 30:
            unknown_debug_t = time.time()
            unknown_debug_dump()
        sleep(0.5)


if __name__ == "__main__":
    # 界面子进程模式下stdout是管道, 默认块缓冲会让日志延迟, 改为行缓冲
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass
    # 多线程(挂机主线程+断网监控线程)同时print时片段会交叉堆在一行,
    # 重写print: 先拼成完整一行再一次输出
    import builtins as _builtins
    _orig_print = print

    def _single_line_print(*args, **kwargs):
        _orig_print(' '.join(str(a) for a in args), **kwargs)

    _builtins.print = _single_line_print
    init_dm()
    action = sys.argv[1] if len(sys.argv) > 1 else "6"
    # 界面多账号模式传入指定窗口句柄: python YBXZ.py 6 --hwnd 1645924
    if '--hwnd' in sys.argv:
        FIXED_HWND = int(sys.argv[sys.argv.index('--hwnd') + 1])
        print('多账号模式, 指定窗口:', FIXED_HWND)
    if '--lnk' in sys.argv:
        GAME_LNK = sys.argv[sys.argv.index('--lnk') + 1]
        print('多账号模式, 重启快捷方式:', GAME_LNK)
    main()
