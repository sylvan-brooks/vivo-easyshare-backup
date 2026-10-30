---
name: vivo-easyshare-backup
description: 解开 vivo / iQOO「互传 · 备份至电脑」的备份包，或把解开的文件重新打成互传能恢复的包。用户说解开互传备份、看看备份里有什么、把照片 / 笔记导回手机、互传不认文件夹时使用。
---

# vivo 互传备份包解包 / 重打包

脚本在本 skill 的 `scripts/` 下。格式细节见 `docs/FORMAT.md`。

## 先问清楚

- 备份目录在哪（互传设置里的备份路径，下面每次备份一个「机型 日期_时间」子文件夹）
- 要解包、要重打包，还是两样都要
- 重打包要放回哪些类目：照片 `album`、视频 `video`、原子笔记 `notes`、隐私笔记 `encrypt_data`

## 解包

1. 看 `.backup`：`encrypted` 是 true 就停，告诉用户设了备份密码的包不支持。
2. `python -I -X utf8 scripts/unpack.py <备份子文件夹> <输出目录> --dry`，把要写多少 GB 和输出盘剩余空间报给用户。
3. 去掉 `--dry` 正式跑。退出码 0 全成功，2 是有文件没还原，看 `_清单.csv` 的「结果」列。
4. 要抽查就跑 `scripts/check_unpacked.py <输出目录>`。

解出来的内容是用户的私人资料，只报数量和大小，不打开、不读正文。

## 重打包

1. 第一次用先在 `scripts/` 下 `npm install`。
2. `python -I -X utf8 scripts/repack.py <解包目录> <新包目录> --cats album,video,notes,encrypt_data --dry`。新包目录要放在互传的备份目录下，名字照「机型 日期_时间」的样子起。
3. 有「本地缺失」或「大小不符」就停下报给用户，别跳过。
4. 去掉 `--dry` 跑，再 `node scripts/write_ldb.mjs <新包目录>`。
5. `python -I -X utf8 scripts/verify_repack.py <新包目录> <解包目录>`，必须退出码 0 才交给用户。
6. 让用户在互传里点：资料备份 → 恢复数据 → 选这条 → 勾类目。提醒一句：原子笔记可能覆盖手机上现有的，手机上有新笔记就先单独备份一次。

## 恢复后核对

手机连着 adb 时，用互传自带的 adb（互传安装目录 `resources/adb.exe`，别和别的版本混用，会互相把 adb 服务杀掉）列出 `/sdcard` 下的文件，和包里的 `filename` 对一遍数量。恢复刚结束时手机还在往外写，少一部分先等一两分钟再数。

## 坑

- 互传扫描备份目录时只认含 `.backup` 的子文件夹，放错层级列表里不出现。
- 原包的索引快照（解包时存在 `_备份包自带/索引快照.zip`）是重打包的依据，没有它没法打。
- 原子笔记、隐私笔记要按 40960 字节分段写，大小必须和索引对上，`repack.py` 会自己核。
