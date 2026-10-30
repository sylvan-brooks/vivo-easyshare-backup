#!/usr/bin/env python
# verify_repack.py — 核对 repack.py 打出的包: 按互传恢复时的读法 (读 db 索引 → 异或解回 → 分段的拼回) 逐个比 sha1
# 跑法: python -I -X utf8 verify_repack.py <新包目录> <还原目录>
# 退出码: 0 全对 / 1 参数 / 2 有不一致或缺文件
import csv, hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldb import read_db
from unpack import xor, unseg, L, CH

pkg, root = sys.argv[1], sys.argv[2]
kv = {k.decode('utf-8'): v for k, v in read_db(os.path.join(pkg, 'db')).items()}
meta = json.load(open(os.path.join(pkg, '.backup'), encoding='utf-8'))
local = {}
with open(os.path.join(root, '_清单.csv'), encoding='utf-8-sig') as f:
    for r in csv.reader(f):
        if len(r) >= 5 and r[0] == 'media' and r[4] == 'ok':
            local[r[2]] = r[1]


def sha_file(p, dex=False):
    h, pos = hashlib.sha1(), 0
    with open(L(p), 'rb') as f:
        while True:
            b = f.read(CH)
            if not b:
                return h.hexdigest()
            h.update(xor(b, pos) if dex else b); pos += len(b)


bad = []
fv = json.loads(kv['file_validate_filed'])
miss = [h for h in fv if not os.path.exists(os.path.join(pkg, h))]
n = 0
for k, v in kv.items():
    if k.startswith(('album_', 'video_')):
        j = json.loads(v)
        p = os.path.join(pkg, j['hash'])
        if j['hash'] not in fv:
            bad.append(('不在校验表', k))
        if os.path.getsize(p) != j['size'] or sha_file(p, True) != sha_file(local[j['filename']]):
            bad.append(('内容不一致', j['filename']))
        n += 1
for name, src in (('notes', os.path.join(root, '笔记', '笔记.zip')), ('encrypt_data_note', os.path.join(root, '隐私笔记', '隐私笔记.zip'))):
    h = hashlib.sha1(name.encode()).hexdigest()
    d = xor(open(os.path.join(pkg, h), 'rb').read(), 0)
    if unseg(d) != open(src, 'rb').read():
        bad.append(('笔记包解不回原样', name))
    n += 1
cats = [(c['id'], c['name'], c['count']) for c in json.loads(kv['categories'])]
print('.backup:', meta)
print('类目:', cats, '· xor_obfuscated =', kv.get('xor_obfuscated'))
print(f'核了 {n} 个 · 校验表 {len(fv)} 个, 缺 {len(miss)} · 问题 {len(bad)}')
for b in bad[:20]:
    print('  ', *b)
sys.exit(2 if bad or miss else 0)
