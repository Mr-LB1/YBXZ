# YBXZ
佣兵小镇一键挂机脚本

## 源码结构

- `YBXZ_UI.py` — 主界面（tkinter 多账号管理、日志、启动 worker）
- `YBXZ.py` — 挂机 worker（远征/地下城/秘境等任务状态机）
- `ybxz_common.py` — UI 与 worker 共享的零副作用工具（data_dir/重启标记/PMv2 物理尺寸探测/BMIH/桌面目录）
- `ybxz_updater.py` — 在线更新子系统（版本检查/分片下载/exe 替换/备份清理）
- `damo/` — 大漠插件 (dm.dll/RegDll.dll) + 字典 + 图片素材
- `publish_release.py` / `sync_gitee.py` — 发版与线上原地刷新脚本
- `tools/smoke_check.py` — 冒烟检查
- `*.spec` / `my_app.ico` — PyInstaller 打包配置与图标

## 运行依赖

Python 3 + opencv-python, numpy, pillow, pywin32, pyinstaller（打包用）。
首次运行需注册大漠插件（脚本内含自动注册逻辑）。
