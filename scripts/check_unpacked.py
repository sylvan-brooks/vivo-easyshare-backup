# verify.py — 抽查还原结果: 扩展名跟文件头对不对得上, 图片能不能完整解码, 视频音频 box 链是否完整. 只打统计, 不打文件名.
# 跑法: python -I -X utf8 check_unpacked.py <还原目录> (要 Pillow, 系统 Python 有)
import collections, os, struct, sys
from PIL import Image

root = sys.argv[1]
SIG = {
    '.jpg': [b'\xff\xd8\xff'], '.jpeg': [b'\xff\xd8\xff'], '.png': [b'\x89PNG'], '.gif': [b'GIF8'], '.webp': [b'RIFF'],
    '.mp3': [b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'], '.flac': [b'fLaC'], '.wav': [b'RIFF'], '.pdf': [b'%PDF'],
    '.zip': [b'PK\x03\x04'], '.apk': [b'PK\x03\x04'], '.docx': [b'PK\x03\x04'], '.xlsx': [b'PK\x03\x04'], '.pptx': [b'PK\x03\x04'],
    '.doc': [b'\xd0\xcf\x11\xe0'], '.xls': [b'\xd0\xcf\x11\xe0'], '.db': [b'SQLite format 3'],
}
ISO = {'.mp4', '.m4a', '.mov', '.3gp', '.heic', '.heif', '.aac'}
res = collections.Counter()
img_fail = collections.Counter()


def iso_ok(p, n):
    off = 0
    with open(p, 'rb') as f:
        while off < n:
            f.seek(off); h = f.read(16)
            if len(h) < 8:
                return False
            sz = struct.unpack('>I', h[:4])[0]
            if sz == 1:
                sz = struct.unpack('>Q', h[8:16])[0]
            elif sz == 0:
                return True
            if sz < 8:
                return False
            off += sz
    return off == n


for top in ('照片', '视频', '音乐', '录音', '文档', '压缩包', '应用安装包'):
    for a, _, fs in os.walk(os.path.join(root, top)):
        for fn in fs:
            p = os.path.join(a, fn)
            ext = os.path.splitext(fn)[1].lower()
            n = os.path.getsize(p)
            with open(p, 'rb') as f:
                h = f.read(16)
            if ext in SIG:
                ok = any(h.startswith(s) for s in SIG[ext])
                if not ok and ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp') and h[4:8] == b'ftyp':
                    res[(top, ext, '其实是 HEIC/MP4')] += 1; continue
                res[(top, ext, '头对' if ok else '头不对')] += 1
                if ok and ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp') and n < 60e6:
                    try:
                        with Image.open(p) as im:
                            im.load()
                        res[(top, '图片', '能解码')] += 1
                    except Exception as e:
                        res[(top, '图片', '解码失败')] += 1; img_fail[type(e).__name__] += 1
            elif ext in ISO:
                res[(top, ext, 'box 完整' if h[4:8] == b'ftyp' and iso_ok(p, n) else 'box 不完整')] += 1
            elif n == 0:
                res[(top, '空文件')] += 1
            else:
                res[(top, '其他扩展名')] += 1
for k, v in sorted(res.items()):
    print(f'{v:6}  {k}')
print('解码失败原因', img_fail)
