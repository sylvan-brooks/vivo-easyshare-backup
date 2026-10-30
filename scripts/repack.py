#!/usr/bin/env python
# repack.py — 把 unpack.py 还原出的普通文件, 按原包的索引快照重新打成互传「备份至电脑」包, 让互传电脑版能直接恢复回手机.
#   用途: 原包删了, 只剩解开的文件夹, 互传不认普通文件夹
# 跑法: python -I -X utf8 repack.py <还原目录> <新包目录> [--cats album,video,notes,encrypt_data] [--dry]
#   之后: node write_ldb.mjs <新包目录>   (写 db\ 和 .backup)
# 依据 (读互传电脑版 3.6.13 resources\app.asar 的 main.js / renderer.js):
#   备份记录 = 备份目录下含 .backup 的子目录; 恢复时 fileValidation 要求 db\ 有 CURRENT + MANIFEST + (.ldb 或非空 .log), 且 file_validate_filed 列的文件全在
#   xor_obfuscated=true 或 version>=3.6.12 时, 上传前整文件按 8 字节密钥循环异或解回原样 → 包里的文件要先异或
#   图片 / 视频: 遍历 album_ / video_ 开头的键, 读包里 <hash> 文件, 按键里的 filename / mtime 打 tar 发给手机
#   原子笔记: 读 sha1("notes") 文件; 隐私笔记: 读 sha1("encrypt_data_note"); 内容是手机吐出来的「4 字节大端长度 + 内容」分段流 (每段 40960, 末尾 4 个 0)
# 退出码: 0 成功 / 1 参数或空间 / 2 有文件缺失或大小不符 / 3 产出为空
import argparse, base64, csv, hashlib, json, os, shutil, struct, sys, tempfile, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldb import read_db
from unpack import xor, L, CH

CAT_ID = {'album': 3, 'video': 5, 'notes': 9, 'encrypt_data': 11}
SEG = 40960


def sha1(s):
    return hashlib.sha1(s.encode()).hexdigest()


def load_snapshot(root):
    z = zipfile.ZipFile(os.path.join(root, '_备份包自带', '索引快照.zip'))
    tmp = tempfile.mkdtemp(prefix='iqoo_idx_')
    try:
        for n in z.namelist():
            if n.startswith('db/') and not n.endswith('/'):
                with open(os.path.join(tmp, n[3:]), 'wb') as g:
                    g.write(z.read(n))
        kv = {k.decode('utf-8'): v for k, v in read_db(tmp).items()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return kv, json.loads(z.read('.backup'))


def write_x(dst, chunks, mt=None):
    """chunks 依次异或 (偏移从 0 连续) 写到 dst. 返回字节数."""
    tmp = dst + '.part'
    pos = 0
    with open(L(tmp), 'wb') as g:
        for b in chunks:
            g.write(xor(b, pos)); pos += len(b)
    os.replace(L(tmp), L(dst))
    if mt:
        os.utime(L(dst), (mt, mt))
    return pos


def file_chunks(p):
    with open(L(p), 'rb') as f:
        while True:
            b = f.read(CH)
            if not b:
                return
            yield b


def seg_chunks(p):
    """普通文件 → 「4 字节大端长度 + 内容」分段流, 末尾 4 个 0."""
    with open(L(p), 'rb') as f:
        while True:
            b = f.read(SEG)
            if not b:
                break
            yield struct.pack('>I', len(b)) + b
    yield b'\0\0\0\0'


def seg_size(n):
    return n + 4 * (-(-n // SEG)) + 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root'); ap.add_argument('out')
    ap.add_argument('--cats', default='album,video,notes,encrypt_data')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    root, out = os.path.abspath(a.root), os.path.abspath(a.out)
    cats = [c for c in a.cats.split(',') if c]
    if not cats or any(c not in CAT_ID for c in cats):
        print('--cats 只认', ','.join(CAT_ID)); sys.exit(1)
    kv, meta = load_snapshot(root)

    local = {}                                   # 手机原路径 → 还原出的本地文件
    with open(os.path.join(root, '_清单.csv'), encoding='utf-8-sig') as f:
        for r in csv.reader(f):
            if len(r) >= 5 and r[0] == 'media' and r[4] == 'ok':
                local[r[2]] = r[1]

    jobs, bad = [], []                           # (包内文件名, 来源, 生成器种类, 期望大小, mtime)
    keep = {}
    for k, v in kv.items():
        pre = k.split('_', 1)[0]
        if pre in ('album', 'video') and k.startswith(pre + '_') and pre in cats:
            j = json.loads(v)
            src = local.get(j['filename'])
            if not src or not os.path.exists(L(src)):
                bad.append(('本地缺失', j['filename'])); continue
            if os.path.getsize(L(src)) != j['size']:
                bad.append(('大小不符', j['filename'])); continue
            jobs.append((j['hash'], src, 'plain', j['size'], (j.get('mtime') or 0) / 1000))
            keep[k] = v
    if 'notes' in cats:
        src = os.path.join(root, '笔记', '笔记.zip')
        jobs.append((sha1('notes'), src, 'seg', seg_size(os.path.getsize(src)), None))
    if 'encrypt_data' in cats:
        src = os.path.join(root, '隐私笔记', '隐私笔记.zip')
        want = json.loads(kv['encrypt_data_note'])['size']
        if seg_size(os.path.getsize(src)) != want:
            bad.append(('隐私笔记分段后大小对不上索引', f'{seg_size(os.path.getsize(src))} != {want}'))
        jobs.append((sha1('encrypt_data_note'), src, 'seg', want, None))

    # 索引: 原样带上元数据和选中类目的键, 类目表 / 校验表按这次的内容重写
    for k in ('version', 'phone', 'password', 'success', 'create_time', 'end_time', 'note_use_base64',
              'setting_info', 'hidden_apps_info', 'xor_obfuscated'):
        keep[k] = kv[k]
    for c in cats:
        keep[c] = kv[c]
    if 'encrypt_data' in cats:
        keep['encrypt_data_note'] = kv['encrypt_data_note']
        keep['encrypt_data_info'] = kv['encrypt_data_info']
    ids = {CAT_ID[c] for c in cats}
    for k in ('categories', 'result'):
        keep[k] = json.dumps([c for c in json.loads(kv[k]) if c['id'] in ids], ensure_ascii=False, separators=(',', ':')).encode()
    keep['app_info'] = b'[]'
    keep['file_validate_filed'] = json.dumps([h for h, *_ in jobs]).encode()

    need = sum(s for *_, s, _ in jobs)
    n_media = sum(1 for j in jobs if j[2] == 'plain')
    print(f'类目 {cats} · 媒体 {n_media} 个 · 笔记包 {len(jobs) - n_media} 个 · 共 {need / 2**30:.2f} GB · 索引键 {len(keep)}')
    for b in bad[:20]:
        print('  问题:', *b)
    if bad:
        print(f'有 {len(bad)} 个问题, 停'); sys.exit(2)
    if not jobs:
        print('没东西可打包'); sys.exit(3)
    if a.dry:
        return
    os.makedirs(L(out), exist_ok=True)
    if os.path.exists(os.path.join(out, '.backup')):
        print('目标目录已经是一个备份包 (有 .backup), 不覆盖, 停'); sys.exit(1)
    if shutil.disk_usage(out).free < need * 1.02:
        print('空间不够, 停'); sys.exit(1)

    done = 0
    for i, (h, src, kind, size, mt) in enumerate(jobs, 1):
        dst = os.path.join(out, h)
        if not (os.path.exists(L(dst)) and os.path.getsize(L(dst)) == size):   # 断了重跑: 大小对的跳过
            w = write_x(dst, file_chunks(src) if kind == 'plain' else seg_chunks(src), mt)
            if w != size:
                print('写出大小不符', h, w, size); sys.exit(2)
        done += size
        if i % 500 == 0 or i == len(jobs) or size > 5e8:
            print(f'[{i}/{len(jobs)}] {done / 2**30:.2f}/{need / 2**30:.2f} GB', flush=True)

    with open(os.path.join(out, '_ldb_entries.json'), 'w', encoding='utf-8') as g:
        json.dump({k: base64.b64encode(v).decode() for k, v in keep.items()}, g)
    with open(os.path.join(out, '_backup_flag.json'), 'w', encoding='utf-8') as g:
        json.dump({'model': meta['model'], 'createTime': meta['createTime'], 'encrypted': False,
                   'backupPackageVer': meta['backupPackageVer'], 'success': True}, g, ensure_ascii=False, separators=(',', ':'))
    print('文件写完. 下一步: node write_ldb.mjs', out)


if __name__ == '__main__':
    main()
