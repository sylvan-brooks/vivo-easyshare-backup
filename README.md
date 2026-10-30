# vivo 互传备份包：解包 / 重打包

把 vivo / iQOO「互传 · 备份至电脑」生成的备份包解成普通文件；也能把解开的文件重新打成互传认得的包，再用互传恢复回手机。

Unpack vivo EasyShare (互传) PC backups into plain files, and repack them so EasyShare can restore them to the phone again.

## 为什么有这个

手机寄修前我用互传把资料备份到电脑，想看看里面到底存了什么，就把包解开了，然后把原包删了。手机修好回来想把照片和笔记放回去，才发现互传只认它自己的包，解开的文件夹它根本不认。

这个仓库就是那次补救用的工具：先弄清楚包的格式，再按互传恢复时的读法重新打包。实测 iQOO 7（OriginOS 3）+ 互传电脑版 3.6.13：几千张照片和视频、两百来条笔记都恢复回了手机，照片的拍摄时间也保留了。

## 能做什么

| 脚本 | 作用 |
|---|---|
| `unpack.py` | 备份包 → 按类分好的文件夹：照片、视频、音乐、录音、文档、联系人 vcf、短信 xml、通话记录、日程、笔记、应用数据 tar 等，附一份清单 csv。只读原包。 |
| `repack.py` + `write_ldb.mjs` | 解开的文件夹 → 互传能识别、能恢复的新包。可以只挑类目：照片、视频、原子笔记、隐私笔记。 |
| `verify_repack.py` | 按互传恢复时的读法把新包解一遍，和原文件逐个比 sha1。 |
| `check_unpacked.py` | 抽查解出来的图片能不能完整解码，视频文件结构完不完整。 |

## 用法

需要 Python 3.8+ 和 Node.js 18+。`check_unpacked.py` 另外要 Pillow。

```bash
cd scripts
npm install

# 解包 (先 --dry 看要写多少)
python -I -X utf8 unpack.py "D:/互传备份/iQOO 7 20240101_120000" "E:/解开的"

# 重打包: 只放照片、视频、原子笔记、隐私笔记
python -I -X utf8 repack.py "E:/解开的" "D:/互传备份/iQOO 7 20240101_120000" --cats album,video,notes,encrypt_data
node write_ldb.mjs "D:/互传备份/iQOO 7 20240101_120000"

# 恢复前先核一遍
python -I -X utf8 verify_repack.py "D:/互传备份/iQOO 7 20240101_120000" "E:/解开的"
```

然后在互传电脑版里：资料备份 → 恢复数据，选这条备份，勾要恢复的类目。互传扫描的是「备份目录」下的子文件夹，新包要放在互传设置的备份目录里。

## 注意

- 只测过一台手机、一个互传版本。别的机型或版本格式可能不同，先用 `--dry` 看。
- 设了备份密码的包（`.backup` 里 `encrypted: true`）不支持。
- 重打包要用到原包的索引快照。`unpack.py` 会把它存到 `_备份包自带/索引快照.zip`，别删。
- 原子笔记恢复时是和手机上现有的合并还是覆盖，我没测清楚。手机上有新记的笔记，先用互传单独备份一次。
- 和 vivo 没有关系。格式是从互传电脑版程序和实际备份包里看出来的，不是官方文档。

## 怎么弄明白的

包里的文件名都是 40 位十六进制，内容看着像乱码，但 `.backup` 里写着没加密。我拿 PNG 固定的文件头去跟包里的文件对，反推出是一把 8 字节的密钥在循环异或，包里所有 PNG 都对得上。索引是 LevelDB，自己写了个纯 Python 的读取器。

重打包那一步光靠猜不够，我把互传电脑版 `resources/app.asar` 里的 `main.js` 和 `renderer.js` 解出来，看它认备份的条件和恢复时怎么读文件，照着那个条件去打包。格式细节写在 [docs/FORMAT.md](docs/FORMAT.md)。

## 给 AI 助手用

仓库根目录有 `SKILL.md`，放进 Claude Code 的 `~/.claude/skills/vivo-easyshare-backup/` 或 Codex 的 skills 目录，说「解开互传备份」「把照片导回手机」就会照着做。

## 许可

MIT
