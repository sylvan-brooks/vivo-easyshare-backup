# ldb.py — 只读解析 LevelDB 目录 (.ldb 表 + .log 日志), 纯 Python, 零依赖零联网.
# 用法: from ldb import read_db; kv = read_db(r'...\db')  → {key: value} (按序号取最新, 删除的去掉)
import glob, os, struct

MAGIC = 0xdb4775248b80fb57


def varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1
        r |= (c & 0x7f) << s; s += 7
        if c < 0x80:
            return r, i


def snappy(b):
    n, i = varint(b, 0)
    out = bytearray()
    while i < len(b):
        t = b[i]; i += 1
        k = t & 3
        if k == 0:                                  # 字面量
            ln = t >> 2
            if ln >= 60:
                nb = ln - 59
                ln = int.from_bytes(b[i:i + nb], 'little'); i += nb
            ln += 1
            out += b[i:i + ln]; i += ln
            continue
        if k == 1:
            ln = ((t >> 2) & 7) + 4; off = ((t >> 5) << 8) | b[i]; i += 1
        elif k == 2:
            ln = (t >> 2) + 1; off = int.from_bytes(b[i:i + 2], 'little'); i += 2
        else:
            ln = (t >> 2) + 1; off = int.from_bytes(b[i:i + 4], 'little'); i += 4
        st = len(out) - off
        for j in range(ln):                         # 可能自重叠, 逐字节复制
            out.append(out[st + j])
    assert len(out) == n, 'snappy 长度不符'
    return bytes(out)


def block(f, off, size):
    f.seek(off)
    d = f.read(size + 5)
    body, kind = d[:size], d[size]
    return snappy(body) if kind == 1 else body


def entries(b):
    nr = struct.unpack_from('<I', b, len(b) - 4)[0]
    end = len(b) - 4 - 4 * nr
    i, key = 0, b''
    while i < end:
        sh, i = varint(b, i); ns, i = varint(b, i); vl, i = varint(b, i)
        key = key[:sh] + b[i:i + ns]; i += ns
        yield key, b[i:i + vl]; i += vl


def read_table(path):
    with open(path, 'rb') as f:
        f.seek(-48, 2)
        foot = f.read(48)
        assert struct.unpack_from('<Q', foot, 40)[0] == MAGIC, path
        _, j = varint(foot, 0); _, j = varint(foot, j)        # metaindex
        io, j = varint(foot, j); isz, j = varint(foot, j)     # index
        for _, h in entries(block(f, io, isz)):
            bo, k = varint(h, 0); bs, _ = varint(h, k)
            for ik, v in entries(block(f, bo, bs)):
                tag = int.from_bytes(ik[-8:], 'little')
                yield ik[:-8], tag >> 8, tag & 0xff, v


def read_log(path):
    # 日志按 32KB 块分记录; 记录里是 WriteBatch: seq(8) count(4) + [type key (value)]
    d = open(path, 'rb').read()
    buf, recs, i = b'', [], 0
    while i + 7 <= len(d):
        blk_left = 32768 - (i % 32768)
        if blk_left < 7:
            i += blk_left; continue
        ln, typ = struct.unpack_from('<H', d, i + 4)[0], d[i + 6]
        if typ == 0 and ln == 0:
            i += blk_left; continue
        frag = d[i + 7:i + 7 + ln]; i += 7 + ln
        if typ in (1, 2):
            buf = frag if typ == 1 else buf + frag
            if typ == 1:
                recs.append(buf); buf = b''
        elif typ == 3:
            buf += frag
        elif typ == 4:
            recs.append(buf + frag); buf = b''
    for r in recs:
        seq, cnt = struct.unpack_from('<QI', r, 0)
        j = 12
        for n in range(cnt):
            t = r[j]; j += 1
            kl, j = varint(r, j); k = r[j:j + kl]; j += kl
            if t == 1:
                vl, j = varint(r, j); v = r[j:j + vl]; j += vl
            else:
                v = None
            yield k, seq + n, t, v


def read_db(d):
    best = {}
    srcs = [read_table(p) for p in glob.glob(os.path.join(d, '*.ldb')) + glob.glob(os.path.join(d, '*.sst'))]
    srcs += [read_log(p) for p in glob.glob(os.path.join(d, '*.log')) if os.path.getsize(p)]
    for s in srcs:
        for k, seq, t, v in s:
            if k not in best or seq > best[k][0]:
                best[k] = (seq, t, v)
    return {k: v for k, (seq, t, v) in best.items() if t == 1}
