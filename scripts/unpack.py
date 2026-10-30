#!/usr/bin/env python
# unpack.py — vivo/iQOO「互传 · 备份至电脑」的包 → 普通文件, 按类分目录. 只读原包 (不改不删不挪), 零联网.
# 跑法: python -I -X utf8 unpack.py <备份目录> <输出目录> [--dry] [--only personal,media,apk,settings,appdata]
#   --dry 只列计划和大小, 不写盘. 重跑时已写好且大小对的文件跳过 (断了接着来).
# 包格式 (拿 iQOO 7 / 互传电脑版 3.6.13 的包实测反推, 不是官方文档):
#   .backup 写着 "encrypted":false —— 没加密, 只是混淆: 每个文件整体按 8 字节密钥 K 循环异或; 文件名是 40 位 hex (不是内容哈希)
#   db\ 是 LevelDB 索引: album_N_<hash> 这类键 → {"filename": 手机上的原路径, "hash": 包里的文件名, "size", "mtime": 毫秒}
#   不在索引里的 7 个: 联系人 vcf / 短信 xml / 通话记录 xml / 日程 json / 笔记 zip / 一份设置 xml / 一个没混淆的 LevelDB zip
#   「4 字节大端长度 + 内容」分段存的: 日程、笔记、系统设置 (末尾 4 个 0)
#   应用数据 .data = Android Backup v5, 不压缩不加密 → 去掉 24 字节头就是 tar (7-Zip 能开)
#   weixin_disk / *.sddata = 只有本地文件头、没有中央目录的 zip (stored), 只能顺着头读
# 退出码: 0 全成 / 1 参数或空间不够 / 2 有文件没还原或大小对不上
import argparse, collections, csv, io, json, os, re, shutil, struct, sys, tempfile, time, zipfile, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldb import read_db

K = bytes.fromhex('7fa3c8e15b9d2ef4')
CH = 8 << 20                                    # 每次读 8MB (8 的倍数, 偏移对齐不乱)
KB = K * (CH // 8 + 2)
AB_HEAD = b'ANDROID BACKUP\n5\n0\nnone\n'
MEDIA = {'album': '照片', 'video': '视频', 'music': '音乐', 'record': '录音', 'document': '文档', 'zip_file': '压缩包'}
APP_FALLBACK = {'com.tencent.mm': '微信'}
WX = {'weixin_apk': 'apk', 'weixin_data': 'data', 'weixin_disk': 'sddata', 'weixin_disk_clone': 'sddata_clone'}


def xor(b, off):
    n, r = len(b), off % 8
    k = KB[r:r + n] if r + n <= len(KB) else (K * (n // 8 + 2))[r:r + n]
    return (int.from_bytes(b, 'little') ^ int.from_bytes(k, 'little')).to_bytes(n, 'little')


def L(p):                                       # 长路径 (>260) 也能写
    p = os.path.abspath(p)
    return p if p.startswith('\\\\?\\') else '\\\\?\\' + p


BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {f'COM{i}' for i in range(1, 10)} | {f'LPT{i}' for i in range(1, 10)}


def clean_rel(rel):
    parts = []
    for s in re.split(r'[\\/]+', rel):
        if s in ('', '.', '..'):
            continue
        s = BAD.sub('_', s).rstrip(' .') or '_'
        if s.split('.')[0].upper() in RESERVED:
            s = '_' + s
        parts.append(s[:180])
    return os.path.join(*parts) if parts else '_'


used = set()


def uniq(p):                                    # 同一次跑里撞名的加 (2) (3)
    base, ext = os.path.splitext(p)
    q, i = p, 2
    while q.lower() in used:
        q, i = f'{base} ({i}){ext}', i + 1
    used.add(q.lower())
    return q


def done_already(dst, size):
    return size is not None and os.path.exists(L(dst)) and os.path.getsize(L(dst)) == size


def set_mtime(dst, mt):
    if mt and mt > 0:
        os.utime(L(dst), (mt, mt))


def copy_x(src, off, n, dst, mt=None):
    """src 从 off 起 n 字节异或还原写到 dst (先写 .part 再改名). 返回实际写的字节数."""
    os.makedirs(L(os.path.dirname(dst)), exist_ok=True)
    tmp = dst + '.part'
    w = 0
    with open(src, 'rb') as f, open(L(tmp), 'wb') as g:
        f.seek(off)
        pos, left = off, n
        while left > 0:
            b = f.read(min(CH, left))
            if not b:
                break
            g.write(xor(b, pos))
            pos += len(b); left -= len(b); w += len(b)
    os.replace(L(tmp), L(dst))
    set_mtime(dst, mt)
    return w


def write_bytes(dst, data, mt=None):
    os.makedirs(L(os.path.dirname(dst)), exist_ok=True)
    with open(L(dst + '.part'), 'wb') as g:
        g.write(data)
    os.replace(L(dst + '.part'), L(dst))
    set_mtime(dst, mt)
    return len(data)


def unseg(d):
    """「4 字节大端长度 + 内容」分段 → 拼起来; 不是这个格式返回 None."""
    o, parts = 0, []
    while o + 4 <= len(d):
        n = struct.unpack('>I', d[o:o + 4])[0]
        if n == 0 or o + 4 + n > len(d):
            break
        parts.append(d[o + 4:o + 4 + n]); o += 4 + n
    return b''.join(parts) if parts and d[o:] in (b'', b'\0\0\0\0') else None


def kind_of(b):
    if b.startswith(b'BEGIN:VCARD'):
        return 'vcf'
    if b[:5] == b'<?xml':
        m = re.search(rb'<([A-Za-z_][\w.-]*)', b[5:4000].split(b'?>', 1)[-1])
        return 'xml:' + (m.group(1).decode() if m else '')
    if b[:4] == b'PK\x03\x04':
        return 'zip'
    if b[:16] == b'SQLite format 3\0':
        return 'db'
    if b[:1] in (b'{', b'['):
        try:
            j = json.loads(b)
            return 'json:events' if isinstance(j, dict) and 'eventList' in j else 'json'
        except Exception:
            pass
    return 'bin'


EXT = {'vcf': '.vcf', 'zip': '.zip', 'db': '.db', 'json': '.json', 'json:events': '.json', 'bin': '.bin'}


def unzip_bytes(data, dst_dir):
    # 笔记包的 extra 字段不标准, Python 会拒; 内容 CRC 是对的 (testzip 通过), 跳过 extra 解析
    zipfile.ZipInfo._decodeExtra = lambda self, *a, **k: None
    z = zipfile.ZipFile(io.BytesIO(data))
    n = 0
    for i in z.infolist():
        if i.is_dir():
            continue
        out = uniq(os.path.join(dst_dir, clean_rel(i.filename)))
        mt = time.mktime(i.date_time + (0, 0, -1)) if i.date_time[0] >= 1980 else None
        write_bytes(out, z.read(i), mt)
        n += 1
    return n


def dos_time(t, d):
    try:
        return time.mktime(((d >> 9) + 1980, (d >> 5) & 15, d & 31, t >> 11, (t >> 5) & 63, (t & 31) * 2, 0, 0, -1))
    except Exception:
        return None


def stream_unzip(p, dst_dir):
    """没有中央目录的 zip: 顺着本地文件头一个个读. 返回 (文件数, 读到的位置, 问题)."""
    size = os.path.getsize(p)
    n, pos, note = 0, 0, ''
    with open(p, 'rb') as f:
        def rd(o, k):
            f.seek(o); return xor(f.read(k), o)
        while pos + 30 <= size:
            h = rd(pos, 30)
            if h[:4] != b'PK\x03\x04':
                break
            flags, method, mtm, mdt = struct.unpack('<HHHH', h[6:14])
            csz, usz, nl, xl = struct.unpack('<IIHH', h[18:30])
            name = rd(pos + 30, nl).decode('utf-8' if flags & 0x800 else 'cp437', 'replace')
            extra = rd(pos + 30 + nl, xl)
            if 0xFFFFFFFF in (csz, usz):        # zip64: 真实大小在 extra 0x0001 里
                e = 0
                while e + 4 <= len(extra):
                    eid, eln = struct.unpack('<HH', extra[e:e + 4])
                    if eid == 1:
                        v, q = extra[e + 4:e + 4 + eln], 0
                        if usz == 0xFFFFFFFF:
                            usz = struct.unpack('<Q', v[q:q + 8])[0]; q += 8
                        if csz == 0xFFFFFFFF:
                            csz = struct.unpack('<Q', v[q:q + 8])[0]; q += 8
                    e += 4 + eln
            if flags & 8:
                note = '有数据描述符, 后面读不了'; break
            start = pos + 30 + nl + xl
            if start + csz > size:
                note = '最后一项被截断'; break
            if not name.endswith('/'):
                out = uniq(os.path.join(dst_dir, clean_rel(name)))
                mt = dos_time(mtm, mdt)
                if method == 0:
                    if not done_already(out, csz):
                        copy_x(p, start, csz, out, mt)
                elif method == 8:
                    os.makedirs(L(os.path.dirname(out)), exist_ok=True)
                    dz = zlib.decompressobj(-15)
                    with open(L(out + '.part'), 'wb') as g:
                        o, left = start, csz
                        while left > 0:
                            b = rd(o, min(CH, left)); o += len(b); left -= len(b)
                            g.write(dz.decompress(b))
                        g.write(dz.flush())
                    os.replace(L(out + '.part'), L(out)); set_mtime(out, mt)
                else:
                    copy_x(p, start, csz, out + f'.method{method}', mt)
                n += 1
            pos = start + csz
    if pos < size:
        copy_x(p, pos, size - pos, os.path.join(dst_dir, '_包尾剩余.bin'))
    return n, pos, note


def load_index(src):
    tmp = tempfile.mkdtemp(prefix='iqoo_db_')      # 互传开着库 (LOCK), 拷一份读
    try:
        for n in os.listdir(os.path.join(src, 'db')):
            if n != 'LOCK':
                shutil.copy2(os.path.join(src, 'db', n), tmp)
        kv = read_db(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    J = {}
    for k, v in kv.items():
        try:
            J[k.decode('utf-8', 'replace')] = json.loads(v)
        except Exception:
            pass
    return J


def plan(src, J):
    files = {f for f in os.listdir(src) if re.fullmatch(r'[0-9a-f]{40}', f)}
    apps = {a.get('packageName'): a for a in J.get('app_info', []) if isinstance(a, dict)}
    jobs, seen = [], set()

    def app_name(pkg):
        return clean_rel(f"{apps.get(pkg, {}).get('app_name') or APP_FALLBACK.get(pkg, pkg)}_{pkg}")

    for k, j in sorted(J.items()):
        if not (isinstance(j, dict) and isinstance(j.get('hash'), str)) or j['hash'] not in files:
            continue                            # 类目汇总条目 (album / music …) 指向的文件本来就不存在
        h, mt = j['hash'], (j.get('mtime') or 0) / 1000
        seen.add(h)
        m = re.match(r'(album|video|music|record|document|zip_file)_\d+_', k)
        if m:
            fn = j['filename']
            mm = re.match(r'/?storage/emulated/(\d+)/(.*)$', fn)
            rel = (mm.group(2) if mm.group(1) == '0' else f'分身空间{mm.group(1)}/{mm.group(2)}') if mm else fn
            jobs.append(dict(cat='media', how='plain', h=h, out=os.path.join(MEDIA[m.group(1)], clean_rel(rel)), size=j['size'], mt=mt, src=fn))
            continue
        base = re.sub(r'_\d+$', '', k)
        mm = re.match(r'app_(.+)\.(apk|data|sddata)$', base)
        if mm or base in WX:
            pkg, kind = (mm.group(1), mm.group(2)) if mm else ('com.tencent.mm', WX[base])
            name = app_name(pkg)
            if kind == 'apk':
                jobs.append(dict(cat='apk', how='plain', h=h, out=os.path.join('应用安装包', name + '.apk'), size=j['size'], mt=mt, src=base))
            elif kind == 'data':
                jobs.append(dict(cat='appdata', how='ab', h=h, out=os.path.join('应用数据', name + '.tar'), size=j['size'] - len(AB_HEAD), mt=mt, src=base))
            else:
                suf = '_分身存储' if kind.endswith('clone') else '_存储'
                jobs.append(dict(cat='appdata', how='szip', h=h, out=os.path.join('应用数据', name + suf), size=None, mt=mt, src=base))
            continue
        if base.startswith('settings_'):
            jobs.append(dict(cat='settings', how='sniff', h=h, out=os.path.join('系统设置', clean_rel(base)), size=None, mt=mt, src=base))
        elif base == 'encrypt_data_note':
            jobs.append(dict(cat='personal', how='sniff', h=h, out=os.path.join('隐私笔记', '隐私笔记'), size=None, mt=mt, src=base))
        else:
            jobs.append(dict(cat='settings', how='sniff', h=h, out=os.path.join('其他', clean_rel(base)), size=None, mt=mt, src=base))
    for h in sorted(files - seen):              # 不在索引里的: 按内容认
        jobs.append(dict(cat='personal', how='unindexed', h=h, out=None, size=None, mt=None, src='(未索引)'))
    return jobs, files


UNINDEXED = {'vcf': ('联系人', '联系人.vcf'), 'xml:messages': ('短信', '短信.xml'), 'xml:calls': ('通话记录', '通话记录.xml'),
             'json:events': ('日程', '日程.json'), 'zip': ('笔记', '笔记.zip'), 'xml:settings': ('系统设置', '_未索引设置.xml')}


def run_sniff(p, out_base, unindexed):
    """小文件: 整个解出来 → 分段的拼起来 → 认类型 → 写. zip 另外解成文件夹."""
    raw_head = open(p, 'rb').read(4)
    if unindexed and raw_head == b'PK\x03\x04':  # 唯一一个没混淆的: 一份 LevelDB 的 zip
        dst = os.path.join(OUT, '_备份包自带', '索引快照.zip')
        os.makedirs(L(os.path.dirname(dst)), exist_ok=True)
        shutil.copyfile(p, L(dst))
        return [dst], 'raw zip'
    d = xor(open(p, 'rb').read(), 0)
    payload = unseg(d)
    if payload is None:
        payload = d
    k = kind_of(payload)
    if unindexed:
        sub, fname = UNINDEXED.get(k, ('其他', os.path.basename(p)[:12] + (EXT.get(k) or '.xml')))
        dst = uniq(os.path.join(OUT, sub, fname))
    else:
        dst = uniq(os.path.join(OUT, out_base + (EXT.get(k) or '.xml')))
    write_bytes(dst, payload)
    outs = [dst]
    if k == 'zip':
        try:
            n = unzip_bytes(payload, os.path.splitext(dst)[0])
        except zipfile.BadZipFile:              # 没有中央目录 (互传自己打的流式 zip): 顺着本地文件头读
            n, k = stream_unzip_bytes(payload, os.path.splitext(dst)[0]), 'zip(流式)'
        outs.append(f'{os.path.splitext(dst)[0]}\\ ({n} 个文件)')
    return outs, k


def stream_unzip_bytes(d, dst_dir):
    pos, n = 0, 0
    while pos + 30 <= len(d) and d[pos:pos + 4] == b'PK\x03\x04':
        flags, method, mtm, mdt = struct.unpack('<HHHH', d[pos + 6:pos + 14])
        csz, usz, nl, xl = struct.unpack('<IIHH', d[pos + 18:pos + 30])
        name = d[pos + 30:pos + 30 + nl].decode('utf-8' if flags & 0x800 else 'cp437', 'replace')
        start = pos + 30 + nl + xl
        if flags & 8 or start + csz > len(d):
            write_bytes(os.path.join(dst_dir, '_包尾剩余.bin'), d[pos:]); break
        if not name.endswith('/'):
            body = d[start:start + csz]
            if method == 8:
                body = zlib.decompress(body, -15)
            write_bytes(uniq(os.path.join(dst_dir, clean_rel(name))), body, dos_time(mtm, mdt))
            n += 1
        pos = start + csz
    return n


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('out')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--only', default='personal,media,apk,settings,appdata')
    a = ap.parse_args()
    src, OUT = os.path.abspath(a.src), os.path.abspath(a.out)
    want = set(a.only.split(','))
    meta = json.load(open(os.path.join(src, '.backup'), encoding='utf-8'))
    if meta.get('encrypted'):
        print('这个包标着 encrypted=true, 本脚本只认没加密的包'); sys.exit(1)
    J = load_index(src)
    jobs, files = plan(src, J)
    jobs = [j for j in jobs if j['cat'] in want]
    order = ['personal', 'media', 'apk', 'settings', 'appdata']
    jobs.sort(key=lambda j: (order.index(j['cat']), os.path.getsize(os.path.join(src, j['h']))))
    need = sum(os.path.getsize(os.path.join(src, j['h'])) for j in jobs)
    by = collections.Counter(j['cat'] for j in jobs)
    sz = collections.Counter()
    for j in jobs:
        sz[j['cat']] += os.path.getsize(os.path.join(src, j['h']))
    print(f'包内文件 {len(files)} · 要还原 {len(jobs)} 个 · 约 {need / 1e9:.1f} GB')
    for c in order:
        if by[c]:
            print(f'  {c:9} {by[c]:5} 个  {sz[c] / 1e9:7.2f} GB')
    free = shutil.disk_usage(os.path.splitdrive(OUT)[0] + '\\').free
    print(f'输出盘剩 {free / 1e9:.1f} GB')
    if need * 1.03 > free:
        print('空间不够, 停'); sys.exit(1)
    if a.dry:
        return
    os.makedirs(L(OUT), exist_ok=True)
    rows, bad = [], 0
    stat = collections.Counter()
    t0, done_bytes = time.time(), 0
    for i, j in enumerate(jobs, 1):
        p = os.path.join(src, j['h'])
        n_in = os.path.getsize(p)
        try:
            if j['how'] == 'plain':
                dst = uniq(os.path.join(OUT, j['out']))
                if not done_already(dst, j['size']):
                    w = copy_x(p, 0, n_in, dst, j['mt'])
                    if w != j['size']:
                        raise RuntimeError(f'大小不符 {w} != {j["size"]}')
                rows.append((j['cat'], dst, j['src'], j['size'], 'ok'))
            elif j['how'] == 'ab':
                dst = uniq(os.path.join(OUT, j['out']))
                with open(p, 'rb') as f:
                    head = xor(f.read(len(AB_HEAD)), 0)
                if head != AB_HEAD:                 # 压缩或加密的备份: 原样还原成 .ab, 不拆
                    dst = os.path.splitext(dst)[0] + '.ab'
                    copy_x(p, 0, n_in, dst, j['mt'])
                    rows.append((j['cat'], dst, j['src'], n_in, 'ab 头不同, 原样'))
                else:
                    if not done_already(dst, j['size']):
                        w = copy_x(p, len(AB_HEAD), n_in - len(AB_HEAD), dst, j['mt'])
                        if w != j['size']:
                            raise RuntimeError('大小不符')
                    with open(L(dst), 'rb') as g:
                        blk = g.read(512)
                    chk = int(blk[148:156].split(b'\0')[0].strip() or b'0', 8)
                    ok = chk == sum(blk[:148]) + 256 + sum(blk[156:])
                    rows.append((j['cat'], dst, j['src'], j['size'], 'ok' if ok else 'tar 头校验不过'))
                    bad += not ok
            elif j['how'] == 'szip':
                dst_dir = os.path.join(OUT, j['out'])
                n, pos, note = stream_unzip(p, dst_dir)
                rows.append((j['cat'], dst_dir, j['src'], n, f'{n} 个文件, 读到 {pos}/{n_in}' + (f', {note}' if note else '')))
                bad += bool(note)
            else:
                outs, k = run_sniff(p, j['out'], j['how'] == 'unindexed')
                for o in outs:
                    rows.append((j['cat'], o, j['src'], '', k))
            stat[j['cat']] += 1
        except Exception as e:
            bad += 1
            rows.append((j['cat'], j['out'] or '', j['src'], '', f'失败 {type(e).__name__}: {e}'))
            print(f'[{i}/{len(jobs)}] {j["cat"]} 失败 {type(e).__name__}', flush=True)
        done_bytes += n_in
        if n_in > 1e9 or i % 500 == 0 or i == len(jobs):
            el = time.time() - t0
            print(f'[{i}/{len(jobs)}] {j["cat"]} · 已处理 {done_bytes / 1e9:.1f}/{need / 1e9:.1f} GB · {el / 60:.1f} 分钟', flush=True)
    man = L(os.path.join(OUT, '_清单.csv'))
    old = []
    if os.path.exists(man):                     # 分几次跑 (--only): 只替换这次跑的类, 别的类留着
        old = [r for r in list(csv.reader(open(man, encoding='utf-8-sig')))[1:] if r and r[0] not in want]
    with open(man, 'w', encoding='utf-8-sig', newline='') as g:
        w = csv.writer(g)
        w.writerow(['类', '还原到', '手机原路径 / 索引键', '大小或文件数', '结果'])
        w.writerows(old + rows)
    print('完成:', dict(stat), '· 问题', bad, '· 清单', os.path.join(OUT, '_清单.csv'))
    sys.exit(2 if bad else 0)


if __name__ == '__main__':
    main()
