# -*- coding: utf-8 -*-
"""重构期基线冒烟检查(每阶段提交前必跑, 全绿才算过).

用法: python tools\smoke_check.py [--dead 符号1 符号2 ...]
检查项:
  1. py_compile YBXZ.py / YBXZ_UI.py        (语法底线)
  2. 子进程 import YBXZ / import YBXZ_UI     (导入级, 已证无副作用)
  3. APP_VER 一行式正则                      (publish_release.py 改版本号依赖)
  4. AST 死引用检查: --dead 列出的符号全库零引用
  5. BOUND_HWND print 标记存在               (UI 靠窗口枚举认领, 机器可读)
  6. UI 无游戏实例化冒烟 (tk.Tk+withdraw, frozen=False 不触发更新线程)
  7. --methods App 方法清单断言 (mixin 拆分后方法全部仍在)
"""
import ast
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(name, ok, detail=''):
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         (' - ' + detail) if detail and not ok else ''))
    if not ok:
        FAILS.append(name)


def ast_symbol_refs(src, names):
    """返回 {符号: 引用次数}(含定义本身, 死代码判定看是否 <= 定义处1次)."""
    tree = ast.parse(src)
    refs = dict.fromkeys(names, 0)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in refs:
            refs[node.id] += 1
        elif isinstance(node, ast.Attribute) and node.attr in refs:
            refs[node.attr] += 1
    return refs


def main():
    ybxz = open(os.path.join(ROOT, 'YBXZ.py'), encoding='utf-8').read()
    yui = open(os.path.join(ROOT, 'YBXZ_UI.py'), encoding='utf-8').read()

    # 1. py_compile
    r = subprocess.call([sys.executable, '-m', 'py_compile',
                         os.path.join(ROOT, 'YBXZ.py'),
                         os.path.join(ROOT, 'YBXZ_UI.py')],
                        cwd=ROOT)
    check('py_compile', r == 0)

    # 2. 导入级冒烟(子进程, 隔离副作用)
    for mod in ('YBXZ', 'YBXZ_UI'):
        r = subprocess.call([sys.executable, '-c', 'import ' + mod], cwd=ROOT)
        check('import ' + mod, r == 0)

    # 3. APP_VER 一行式(发版脚本正则依赖)
    check('APP_VER一行式', re.search(r"APP_VER = '\d+'", yui) is not None)

    # 4. 死引用检查
    dead = []
    if '--dead' in sys.argv:
        dead = sys.argv[sys.argv.index('--dead') + 1:]
    for sym in dead:
        n = ast_symbol_refs(ybxz, [sym])[sym] \
            + ast_symbol_refs(yui, [sym])[sym]
        check('死引用:%s' % sym, n == 0, '仍有%d处引用' % n)

    # 5. BOUND_HWND 机器可读标记(UI sync_cards 依赖, 一字符不能动)
    check('BOUND_HWND标记', "print('BOUND_HWND:%d'" in ybxz)

    # 5b. main() 模式派发回归: CLI路径(action参数回落全局)与显式参数路径都要命中正确状态机
    # (无游戏可跑: 替换掉三个状态机函数, 验证派发命中; 地下城是兜底分支无法hook, 不测)
    dispatch_test = (
        "import YBXZ\n"
        "hits = []\n"
        "YBXZ.expedition_main = lambda u: hits.append('4')\n"
        "YBXZ.elf_main = lambda u: hits.append('5')\n"
        "YBXZ.monitor_main = lambda u: hits.append('7')\n"
        "for m, fn in (('4', 'expedition_main'), ('5', 'elf_main'), ('7', 'monitor_main')):\n"
        "    del hits[:]\n"
        "    YBXZ.action = m\n"
        "    YBXZ.main()               # CLI路径: 无参调用, action回落全局\n"
        "    assert hits == [m], ('CLI回落', m, hits)\n"
        "    del hits[:]\n"
        "    YBXZ.main(action=m)       # in-process路径: 显式参数\n"
        "    assert hits == [m], ('显式参数', m, hits)\n"
        "print('dispatch-ok')\n")
    r = subprocess.run([sys.executable, '-c', dispatch_test], cwd=ROOT,
                       capture_output=True)
    ok = r.returncode == 0 and b'dispatch-ok' in r.stdout
    check('main模式派发', ok,
          r.stderr.decode('gbk', 'replace')[-300:] if not ok else '')

    # 6. UI 无游戏实例化冒烟
    smoke = (
        "import tkinter as tk, YBXZ_UI as m\n"
        "root = tk.Tk(); root.withdraw()\n"
        "app = m.App(root)\n"
        "root.update()\n"
        "need = ['sync_cards','make_card','update_card','auto_refresh',\n"
        "        'make_tab','tab_log','poll_log','script_cmd','is_running',\n"
        "        'card_toggle','stop_one','stop_all','on_close',\n"
        "        'check_update_clicked','silent_update_check']\n"
        "missing = [x for x in need if not callable(getattr(app, x, None))]\n"
        "assert not missing, missing\n"
        "root.destroy()\n"
        "print('ui-smoke-ok')\n")
    r = subprocess.run([sys.executable, '-c', smoke], cwd=ROOT,
                       capture_output=True)
    ok = r.returncode == 0 and b'ui-smoke-ok' in r.stdout
    check('UI实例化冒烟', ok,
          r.stderr.decode('gbk', 'replace')[-300:] if not ok else '')

    print('====')
    if FAILS:
        print('FAIL: %d 项未过: %s' % (len(FAILS), ', '.join(FAILS)))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
