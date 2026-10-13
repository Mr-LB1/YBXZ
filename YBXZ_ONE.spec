# -*- mode: python ; coding: utf-8 -*-
# 佣兵小镇挂机一键版: 界面+脚本+资源 单exe
import os

block_cipher = None

a = Analysis(
    ['YBXZ_UI.py'],
    pathex=[],
    binaries=[],
    datas=[
        # PaddleOCR(RapidOCR) 模型与配置, 运行时按包目录加载
        (os.path.join('rapidocr_onnxruntime', 'models', 'ch_PP-OCRv4_det_infer.onnx'), 'rapidocr_onnxruntime/models'),
        (os.path.join('rapidocr_onnxruntime', 'models', 'ch_PP-OCRv4_rec_infer.onnx'), 'rapidocr_onnxruntime/models'),
        (os.path.join('rapidocr_onnxruntime', 'models', 'ch_ppocr_mobile_v2.0_cls_infer.onnx'), 'rapidocr_onnxruntime/models'),
        (os.path.join('rapidocr_onnxruntime', 'config.yaml'), 'rapidocr_onnxruntime'),
    ],
    hiddenimports=['YBXZ', 'rapidocr_onnxruntime'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
# damo目录(大漠插件/图片模板)打进 damo/, dic字库已弃用不打
a.datas += Tree('damo', prefix='damo', excludes=['dic'])

# ===== 精简打包: 剔除确认不用的组件 =====
# Pythonwin/mfc140u/win32ui: pywin32的GUI组件, 脚本只用win32com/ctypes
# Shapely/geos: rapidocr的声明依赖但源码根本没import
# 注意: opencv_videoio_ffmpeg460.dll 不能剔! cv2.pyd加载期硬依赖它,
# 删了整个cv2导入失败DLL load failed(实测), 哪怕脚本只用resize/imencode
_DROP = ['Pythonwin', 'mfc140u', 'win32ui']  # Shapely/geos疑似导致cv2的DLL加载失败, 暂保留
_drop = lambda e: any(d in e[0] for d in _DROP)
a.binaries = [e for e in a.binaries if not _drop(e)]
a.datas = [e for e in a.datas if not _drop(e)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='佣兵小镇一键版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
