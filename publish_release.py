# -*- coding: utf-8 -*-
"""一键发版: 改版本号 -> 打包 -> 发布到GitHub更新仓库(Mr-LB1/YBXZ)
用法: python publish_release.py "更新说明"
发完后客户程序启动时会自动检测到新版本弹窗更新
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

DIST_EXE = os.path.join('dist', '佣兵小镇一键版.exe')
RELEASE_DIR = 'ybxz_release'  # 本地克隆的 Mr-LB1/YBXZ
UI_FILE = 'YBXZ_UI.py'


def md5_file(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(262144), b''):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit('命令失败: %s\n%s\n%s' % (cmd, r.stdout, r.stderr))


def gitee_api(path, token, data=None, file_path=None):
    """Gitee v5 API: JSON POST / multipart附件上传; 出错带响应体便于定位"""
    import urllib.request
    url = 'https://gitee.com/api/v5/repos/li-bin970228/ybxz' + path
    if file_path is not None:
        boundary = '----ybxz%d' % os.getpid()
        body = ('--%s\r\nContent-Disposition: form-data; name="access_token"\r\n\r\n%s\r\n'
                % (boundary, token)).encode()
        fname = os.path.basename(file_path)
        body += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n' % (boundary, fname)).encode()
        body += open(file_path, 'rb').read()
        body += ('\r\n--%s--\r\n' % boundary).encode()
        req = urllib.request.Request(url, data=body, method='POST', headers={
            'Content-Type': 'multipart/form-data; boundary=%s' % boundary})
    else:
        body = json.dumps(dict(data or {}, access_token=token)).encode()
        req = urllib.request.Request(url, data=body, method='POST', headers={
            'Content-Type': 'application/json'})
    try:
        r = urllib.request.urlopen(req, timeout=300)
        out = r.read().decode('utf-8')
        return json.loads(out) if out else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError('gitee API %s: HTTP %s %s' % (
            path, e.code, e.read().decode('utf-8')[:200]))


def main():
    notes = sys.argv[1] if len(sys.argv) > 1 else 'bug修复与优化'
    release_repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), RELEASE_DIR)
    if not os.path.isdir(os.path.join(release_repo, '.git')):
        raise SystemExit('找不到发布仓库 %s, 先: git clone git@github.com:Mr-LB1/YBXZ.git' % release_repo)

    # 0. 先同步线上, 版本号按线上最新叠加:
    #    v + yyyymmdd + 当天序列号4位, 当天第n个版本(线上是当天→序列号+1, 跨天→0001起)
    #    序列号必须4位: 客户端按int数值比较新旧, 位数变了会被当成降级不弹更新
    run(['git', 'pull', 'gitee', 'master'], cwd=release_repo)
    latest = 0
    vfile = os.path.join(release_repo, 'version.json')
    if os.path.exists(vfile):
        try:
            latest = int(json.load(open(vfile, encoding='utf-8')).get('ver') or 0)
        except (ValueError, OSError):
            latest = 0
    today = int(time.strftime('%Y%m%d'))
    ver = latest + 1 if latest // 10000 == today else today * 10000 + 1
    print('线上最新版本:', latest, '-> 本次发版:', ver)

    # 1. 改客户端版本号(必须先改再打包, 否则下载的新版还会被提示更新)
    src = open(UI_FILE, encoding='utf-8').read()
    src, n = re.subn(r"APP_VER = '\d+'", "APP_VER = '%d'" % ver, src, count=1)
    if n != 1:
        raise SystemExit('YBXZ_UI.py里找不到APP_VER常量')
    open(UI_FILE, 'w', encoding='utf-8').write(src)
    print('版本号 ->', ver)

    # 2. 杀掉打包版实例(exe被占用会打包失败; 没有实例在跑则忽略报错), 重新打包
    subprocess.run('taskkill /f /im 佣兵小镇一键版.exe', capture_output=True)
    run([sys.executable, '-m', 'PyInstaller', 'YBXZ_ONE.spec', '--noconfirm'])
    if not os.path.exists(DIST_EXE):
        raise SystemExit('打包失败: 找不到' + DIST_EXE)

    # 3. 拷进发布仓库, 压缩分片(客户端国内CDN/源分片下载), 生成version.json
    import zlib
    tag = 'v%d' % ver
    dst = os.path.join(release_repo, 'YBXZ.exe')
    with open(DIST_EXE, 'rb') as f:
        exe_bytes = f.read()
    md5 = hashlib.md5(exe_bytes).hexdigest()
    size = len(exe_bytes)
    comp = zlib.compress(exe_bytes, 6)
    chunk_dir = os.path.join(release_repo, 'chunks')
    os.makedirs(chunk_dir, exist_ok=True)
    for p in os.listdir(chunk_dir):  # 清掉旧版本分片
        os.remove(os.path.join(chunk_dir, p))
    CHUNK = 16 * 1024 * 1024
    chunks = []
    for i in range(0, len(comp), CHUNK):
        part = comp[i:i + CHUNK]
        cmd5 = hashlib.md5(part).hexdigest()
        name = 'ybxz_%s_%d.bin' % (md5[:8], i // CHUNK)
        open(os.path.join(chunk_dir, name), 'wb').write(part)
        csha = subprocess.run(['git', 'hash-object', os.path.join('chunks', name)],
                              cwd=release_repo, capture_output=True).stdout.decode().strip()
        chunks.append({'name': name, 'md5': cmd5, 'size': len(part), 'sha': csha})
    sha = subprocess.run(['git', 'hash-object', 'YBXZ.exe'], cwd=release_repo,
                         capture_output=True).stdout.decode().strip()
    history = []
    vfile = os.path.join(release_repo, 'version.json')
    if os.path.exists(vfile):
        try:
            history = json.load(open(vfile, encoding='utf-8')).get('history') or []
        except (ValueError, OSError):
            history = []
    history.insert(0, {'ver': ver, 'notes': notes})
    history = history[:20]  # 只留最近20条, 客户端只展示比本机版本新的
    info = {'ver': ver, 'notes': notes, 'md5': md5, 'size': size, 'sha': sha,
            'tag': tag, 'exe_name': '佣兵小镇一键版.exe', 'chunks': chunks,
            'history': history}
    with open(vfile, 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print('exe %dMB -> 压缩%dMB, %d个分片' % (size // 1048576, len(comp) // 1048576, len(chunks)))
    print('version.json:', {k: info[k] for k in ('ver', 'notes', 'md5')})

    # 4. 提交推送: 只推gitee(纯国内源, github已放弃)
    run(['git', 'add', '-A'], cwd=release_repo)
    run(['git', 'commit', '-m', 'release %d: %s' % (ver, notes)], cwd=release_repo)
    run(['git', 'push', 'gitee', 'main:master'], cwd=release_repo)

    # 5. Gitee Release附件: 分片上传(gitee raw对未登录大文件403, 附件匿名可下)
    tag = 'v%d' % ver
    token_f = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gitee_token.txt')
    if os.path.exists(token_f):
        token = open(token_f).read().strip()
        try:
            rel = gitee_api('/releases', token, {
                'tag_name': tag, 'name': tag, 'body': notes,
                'target_commitish': 'master'})
            for c in chunks:
                gitee_api('/releases/%s/attach_files' % rel['id'], token,
                          file_path=os.path.join(chunk_dir, c['name']))
                print('附件已传:', c['name'])
            # gitee侧也维护一份version.json(在仓库文件里, raw小文件匿名可下)
            print('Gitee Release %s 完成' % tag)
        except Exception as e:
            raise SystemExit('Gitee Release失败: %s\n(github侧已发布, gitee需重跑本脚本)' % e)
    else:
        print('提示: 未找到gitee_token.txt, 跳过Gitee Release(仅GitHub已发布)')
    print('发布完成: ver=%d  (客户下次启动自动检测更新)' % ver)


if __name__ == '__main__':
    main()
