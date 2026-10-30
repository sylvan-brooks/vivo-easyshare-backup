// write_ldb.mjs — repack.py 的第二步: 把 _ldb_entries.json 写成 db\ (LevelDB), 再写 .backup 和 config.backup.zip 的同款兜底包.
// 跑法: node write_ldb.mjs <新包目录>
// 依赖: 在本目录跑一次 npm install (classic-level). 退出码: 0 成功 / 1 参数 / 2 写库或核对失败
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { ClassicLevel } = require('classic-level');

const out = process.argv[2];
if (!out || !fs.existsSync(path.join(out, '_ldb_entries.json'))) {
  console.error('用法: node write_ldb.mjs <新包目录> (要先跑 repack.py)'); process.exit(1);
}
if (fs.existsSync(path.join(out, '.backup'))) { console.error('已有 .backup, 不覆盖'); process.exit(1); }
const entries = JSON.parse(fs.readFileSync(path.join(out, '_ldb_entries.json'), 'utf8'));
const dbDir = path.join(out, 'db');
if (fs.existsSync(dbDir)) { console.error('db 目录已存在, 不覆盖'); process.exit(1); }

const db = new ClassicLevel(dbDir, { keyEncoding: 'buffer', valueEncoding: 'buffer' });
const keys = Object.keys(entries);
await db.batch(keys.map(k => ({ type: 'put', key: Buffer.from(k, 'utf8'), value: Buffer.from(entries[k], 'base64') })));
await db.close();

// 重开核对: 键数和逐个值一致
const chk = new ClassicLevel(dbDir, { keyEncoding: 'buffer', valueEncoding: 'buffer' });
let n = 0, diff = 0;
for await (const [k, v] of chk.iterator()) {
  n++;
  if (entries[k.toString('utf8')] !== v.toString('base64')) diff++;
}
await chk.close();
fs.rmSync(path.join(dbDir, 'LOCK'), { force: true });
if (n !== keys.length || diff) { console.error(`核对失败: 库里 ${n} 个键 / 应有 ${keys.length}, 值不同 ${diff}`); process.exit(2); }

const flag = fs.readFileSync(path.join(out, '_backup_flag.json'));
fs.writeFileSync(path.join(out, '.backup'), flag);

// 互传自己的兜底: sha1("config.backup.zip") 命名的 zip, 装 db\ + .backup (原包有, 不带只是少一层保险)
const zipName = crypto.createHash('sha1').update('config.backup.zip').digest('hex');
const files = [...fs.readdirSync(dbDir).map(f => ['db/' + f, path.join(dbDir, f)]), ['.backup', path.join(out, '.backup')]];
fs.writeFileSync(path.join(out, zipName), storedZip(files));

fs.rmSync(path.join(out, '_ldb_entries.json'));
fs.rmSync(path.join(out, '_backup_flag.json'));
console.log(`db 写好 ${n} 个键 · .backup 已写 · 兜底 zip ${zipName}`);

function storedZip(list) {
  const crcT = new Int32Array(256).map((_, i) => { let c = i; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; return c; });
  const crc = b => { let c = -1; for (const x of b) c = crcT[(c ^ x) & 255] ^ (c >>> 8); return (c ^ -1) >>> 0; };
  const loc = [], cen = []; let off = 0;
  for (const [name, p] of list) {
    const data = fs.readFileSync(p), nm = Buffer.from(name), c = crc(data);
    const h = Buffer.alloc(30); h.writeUInt32LE(0x04034b50, 0); h.writeUInt16LE(20, 4); h.writeUInt32LE(c, 14);
    h.writeUInt32LE(data.length, 18); h.writeUInt32LE(data.length, 22); h.writeUInt16LE(nm.length, 26);
    loc.push(h, nm, data);
    const d = Buffer.alloc(46); d.writeUInt32LE(0x02014b50, 0); d.writeUInt16LE(20, 4); d.writeUInt16LE(20, 6); d.writeUInt32LE(c, 16);
    d.writeUInt32LE(data.length, 20); d.writeUInt32LE(data.length, 24); d.writeUInt16LE(nm.length, 28); d.writeUInt32LE(off, 42);
    cen.push(d, nm); off += 30 + nm.length + data.length;
  }
  const cd = Buffer.concat(cen), e = Buffer.alloc(22);
  e.writeUInt32LE(0x06054b50, 0); e.writeUInt16LE(list.length, 8); e.writeUInt16LE(list.length, 10);
  e.writeUInt32LE(cd.length, 12); e.writeUInt32LE(off, 16);
  return Buffer.concat([...loc, cd, e]);
}
