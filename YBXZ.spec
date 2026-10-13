# -*- mode: python ; coding: utf-8 -*-


block_cipher = None

a = Analysis(
    ['YBXZ.py'],
    pathex=[],
    binaries=[],
    datas=[
        # PaddleOCR(RapidOCR) 模型与配置, 运行时按包目录加载
        ('rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx', 'rapidocr_onnxruntime/models'),
        ('rapidocr_onnxruntime/models/ch_PP-OCRv4_rec_infer.onnx', 'rapidocr_onnxruntime/models'),
        ('rapidocr_onnxruntime/models/ch_ppocr_mobile_v2.0_cls_infer.onnx', 'rapidocr_onnxruntime/models'),
        ('rapidocr_onnxruntime/config.yaml', 'rapidocr_onnxruntime'),
    ],
    hiddenimports=['rapidocr_onnxruntime'],
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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='YBXZ',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='YBXZ',
)
