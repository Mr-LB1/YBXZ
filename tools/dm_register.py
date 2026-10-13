# -*- coding: utf-8 -*-
"""大漠dm.dll注册诊断修复工具(右键以管理员身份运行):
提取随包dm.dll到固定位置 -> 可见模式regsvr32重注册(失败弹原生错误框能看到原因)
-> 验证Dispatch与关键方法 -> 结果同时写dm注册报告.txt(可截图回传)
用法: dm注册修复工具.exe [--quiet]  (--quiet=静默注册, 不弹成功/失败框)
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time

LINES = []


def log(msg):
    print(msg)
    LINES.append(str(msg))


def finish():
    # 报告保存位置降级链: exe旁 -> 桌面 -> TEMP
    # (exe在微信接收目录/QQ目录等受保护位置运行时, exe旁经常不可写, 实测PermissionError 13)
    candidates = []
    try:
        candidates.append(('exe旁', os.path.dirname(os.path.abspath(sys.argv[0]))))
    except Exception:
        pass
    candidates.append(('桌面', os.path.join(os.path.expanduser('~'), 'Desktop')))
    candidates.append(('TEMP', os.environ.get('TEMP', os.path.expanduser('~'))))
    for tag, base in candidates:
        try:
            report = os.path.join(base, 'dm注册报告.txt')
            with open(report, 'w', encoding='utf-8') as f:
                f.write('\n'.join(LINES))
            log('报告已保存(%s): %s' % (tag, report))
            break
        except Exception as e:
            log('报告保存失败(%s): %r' % (tag, e))
    log('10秒后退出...')
    time.sleep(10)
    sys.exit(0)


def main():
    log('=== dm.dll 注册诊断修复工具 ===')
    # 1. 管理员权限检查(写HKCR注册表必须提权)
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        admin = False
    log('管理员权限: %s' % ('是' if admin else '否!! 请关闭后右键"以管理员身份运行"'))
    if not admin:
        finish()
    # 2. 定位随包dm.dll(onefile解压在_MEIPASS)
    if getattr(sys, '_MEIPASS', False):
        src = os.path.join(sys._MEIPASS, 'damo', 'dm.dll')
    else:
        src = os.path.join(os.path.abspath('.'), 'damo', 'dm.dll')
    log('随包dm.dll: %s (%s)' % (src, '存在' if os.path.exists(src) else '缺失!!'))
    if not os.path.exists(src):
        finish()
    # 3. 复制到固定位置注册(COM注册表指向dll完整路径, 只要该路径的文件一直在,
    #    注册一次永久有效)。不用exe旁(客户可能清理桌面/删工具)也不用临时目录(重启被清),
    #    用 %LOCALAPPDATA%\ybxz\damo\dm.dll 程序数据目录
    if getattr(sys, 'frozen', False):
        log('exe位置: %s' % os.path.abspath(sys.executable))
    base = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ybxz')
    dst_dir = os.path.join(base, 'damo')
    dst = os.path.join(dst_dir, 'dm.dll')
    log('目标路径: %s' % dst)
    src_size = os.path.getsize(src)
    log('源文件大小: %d 字节' % src_size)
    try:
        os.makedirs(dst_dir, exist_ok=True)
        copied = False
        # 方式1: shutil.copyfile (py3.8+底层走CopyFile2, 云手机/容器类文件系统上可能报EINVAL)
        try:
            shutil.copyfile(src, dst)
            copied = True
            log('已复制到固定位置(copyfile)')
        except Exception as e:
            log('copyfile失败: %r, 换CopyFileW' % e)
        # 方式2: kernel32.CopyFileW (传统API, 不走CopyFile2, 兼容老/虚拟文件系统)
        if not copied:
            try:
                if ctypes.windll.kernel32.CopyFileW(u'%s' % src, u'%s' % dst, 0):
                    copied = True
                    log('已复制到固定位置(CopyFileW)')
                else:
                    gle = ctypes.windll.kernel32.GetLastError()
                    log('CopyFileW失败: GetLastError=%d%s'
                        % (gle, ' (225=杀软判定文件含病毒/PUA并拦截!)' if gle == 225 else ''))
            except Exception as e:
                log('CopyFileW异常: %r, 换字节流复制' % e)
        # 方式3: 字节流复制(最原始, 任何可读写文件系统都支持)
        if not copied:
            with open(src, 'rb') as f:
                data = f.read()
            with open(dst, 'wb') as f:
                f.write(data)
            copied = True
            log('已复制到固定位置(字节流): %d字节' % len(data))
        if os.path.getsize(dst) != src_size:
            copied = False
            log('复制后大小不一致! 目标%d字节 != 源%d字节, 视为失败' % (os.path.getsize(dst), src_size))
        if copied:
            log('复制校验通过: %s' % dst)
    except Exception as e:
        dst = src  # 兜底: 直接注册随包临时dll, 保证本次能用
        log('复制失败(所有方式都失败): %r' % e)
        log('降级: 直接注册随包dll %s' % dst)
        log('!!! 三种复制全失败+错误225/EINVAL => 基本确定是杀软(Windows Defender等)')
        log('!!! 把dm.dll当病毒拦截了。永久修复方法:')
        log('!!! 1.打开 Windows安全中心->病毒和威胁防护->保护历史记录,')
        log('!!!    找到dm.dll的拦截记录选"允许/还原"')
        log('!!!    (或"排除项"->添加排除文件夹: %s 和挂机程序所在文件夹)' % base)
        log('!!! 2.再重新运行本工具, 复制成功后注册才不会因重启失效')
        log('注意: 临时目录重启会被清空, dm注册会随之失效, 到时需再跑一次本工具')
    # 4. 重新注册: 可见模式(不加/s), 失败会弹regsvr32原生错误框, 原因看得见
    sysroot = os.environ.get('SystemRoot', r'C:\Windows')
    reg = os.path.join(sysroot, 'SysWOW64', 'regsvr32.exe')  # 32位dll必须32位regsvr32
    if not os.path.exists(reg):
        reg = os.path.join(sysroot, 'System32', 'regsvr32.exe')
    log('regsvr32: %s' % reg)
    quiet = '--quiet' in sys.argv
    r = subprocess.run('"%s" %s "%s"' % (reg, '/s' if quiet else '', dst), shell=True)
    log('regsvr32 退出码: %d (0=成功; 非0时注意屏幕上的错误弹框内容)' % r.returncode)
    # 5. 验证: Dispatch + 关键方法逐项检查
    try:
        from win32com.client.dynamic import Dispatch
        dm = Dispatch('dm.dmsoft')
        log('Dispatch(dm.dmsoft): 成功')
        try:
            log('dm版本: %s' % str(dm.Ver()))
        except Exception as e:
            log('dm.Ver()失败: %r' % e)
        for m in ('SetPath', 'BindWindow', 'FindStr', 'MoveTo', 'LeftClick'):
            try:
                getattr(dm, m)
                log('方法 %-10s: OK' % m)
            except Exception:
                log('方法 %-10s: 缺失(电脑上有别的旧版dm.dmsoft占着注册)' % m)
    except Exception as e:
        log('Dispatch(dm.dmsoft)失败: %r' % e)
        log('-> 多半是杀软拦截了regsvr32或dm.dll, 请查杀软拦截记录并加信任后重试')
    log('=== 结束 ===')
    finish()


if __name__ == '__main__':
    main()
