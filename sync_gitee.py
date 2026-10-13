# -*- coding: utf-8 -*-
"""把 dist 最新构建同步到 Gitee 更新仓库(不新增版本号, 原地刷新当前线上版本的exe与分片)
用法: python sync_gitee.py [版本号]
  版本号缺省=读取线上version.json的当前ver
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zlib

import publish_release as pr

REPO = 'ybxz_release'
TOKEN_FILE = 'gitee_token.txt'


def retry(fn, desc, n=4):
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            print('%s 第%d次失败: %s, 等%d秒' % (desc, i + 1, str(e)[:90], 5 * (i + 1)))
            time.sleep(5 * (i + 1))
    raise SystemExit(desc + ' 连续失败, 请稍后重试或手工处理')


def main():
    token = open(TOKEN_FILE).read().strip()
    repo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), REPO)
    vf = os.path.join(repo_dir, 'version.json')
    info = json.load(open(vf, encoding='utf-8'))
    ver = int(sys.argv[1]) if len(sys.argv) > 1 else int(info['ver'])
    tag = 'v%d' % ver

    # 1. exe与分片重生成
    shutil.copy(os.path.join('dist', '佣兵小镇一键版.exe'),
                os.path.join(repo_dir, 'YBXZ.exe'))
    exe = open(os.path.join(repo_dir, 'YBXZ.exe'), 'rb').read()
    md5, size = hashlib.md5(exe).hexdigest(), len(exe)
    comp = zlib.compress(exe, 6)
    cdir = os.path.join(repo_dir, 'chunks')
    for p in os.listdir(cdir):
        os.remove(os.path.join(cdir, p))
    CHUNK = 16 * 1024 * 1024
    chunks = []
    for i in range(0, len(comp), CHUNK):
        part = comp[i:i + CHUNK]
        name = 'ybxz_%s_%d.bin' % (md5[:8], i // CHUNK)
        open(os.path.join(cdir, name), 'wb').write(part)
        chunks.append({'name': name, 'md5': hashlib.md5(part).hexdigest(),
                       'size': len(part)})

    # 2. 删旧release -> 重建 -> 传附件
    token_box = {'t': token}

    def get_releases():
        return json.loads(urllib.request.urlopen(
            'https://gitee.com/api/v5/repos/li-bin970228/ybxz/releases?access_token=%s'
            % token_box['t'], timeout=30).read().decode())

    def delete_release(rid):
        req = urllib.request.Request(
            'https://gitee.com/api/v5/repos/li-bin970228/ybxz/releases/%s?access_token=%s'
            % (rid, token_box['t']), method='DELETE')
        return urllib.request.urlopen(req, timeout=30).status

    lst = retry(get_releases, '列release')
    for r0 in lst:
        if r0['tag_name'] == tag:
            rid = r0['id']
            retry(lambda rid=rid: delete_release(rid), '删旧release ' + str(rid))
    rel = retry(lambda: pr.gitee_api('/releases', token, {
        'tag_name': tag, 'name': tag, 'body': info.get('notes', ''),
        'target_commitish': 'master'}), '建release')
    for c in chunks:
        retry(lambda c=c: pr.gitee_api('/releases/%s/attach_files' % rel['id'], token,
                                       file_path=os.path.join(cdir, c['name'])),
              '传附件 ' + c['name'])
        print('附件已传:', c['name'])

    # 3. version.json刷新(exe_name字段保留) + 推送
    info['ver'] = ver
    info['tag'] = tag
    info['md5'] = md5
    info['size'] = size
    info['chunks'] = chunks
    json.dump(info, open(vf, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    retry(lambda: subprocess.run(['git', 'add', '-A'], cwd=repo_dir, check=True), 'git add')
    retry(lambda: subprocess.run(['git', 'commit', '-q', '-m',
                                  'sync %d: exe与分片刷新(版本号不变)' % ver],
                                 cwd=repo_dir, check=True), 'git commit')
    retry(lambda: subprocess.run(['git', 'push', '-q', 'gitee', 'main:master'],
                                 cwd=repo_dir, check=True), 'git push')
    print('同步完成: ver=%d md5=%s chunks=%d' % (ver, md5[:12], len(chunks)))


if __name__ == '__main__':
    main()
