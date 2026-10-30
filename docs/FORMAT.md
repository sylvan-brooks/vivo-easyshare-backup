# 互传「备份至电脑」包格式

拿 iQOO 7（OriginOS 3）和互传电脑版 3.6.13 实测，再对照电脑版 `resources/app.asar` 里的 `main.js` / `renderer.js`。不是官方文档，别的版本可能不一样。

## 目录

互传设置里的备份目录下，每次备份一个子文件夹，名字是「机型 日期_时间」：

```
iQOO 7 20240101_120000/
├── .backup                                    记录文件 (JSON)
├── db/                                        索引 (LevelDB)
├── 7446fd5e…  = sha1("config.backup.zip")     db/ + .backup 的兜底 zip
└── <40 位十六进制>  × N                        数据文件
```

`.backup` 示例：

```json
{"model":"iQOO 7","createTime":1704081600000,"encrypted":false,"backupPackageVer":2,"success":true}
```

## 混淆

`encrypted` 是 false 时，每个数据文件整体按 8 字节密钥循环异或，偏移从文件头算起：

```
7f a3 c8 e1 5b 9d 2e f4
```

db 里 `xor_obfuscated` 为 true（或备份时的版本 ≥ 3.6.12）时，互传恢复前会先解回原样再发给手机。设了备份密码的包改用 aes-256-ctr，本工具不支持。

## 索引 db 里的键

| 键 | 值 |
|---|---|
| `album_<序号>_<hash>`、`video_…`、`music_…`、`record_…`、`document_…`、`zip_file_…` | `{filename: 手机上的原路径, hash: 包里的文件名, size, mtime: 毫秒}` |
| `album`、`video`、`notes`、`contact` … | 各类目的汇总信息 |
| `categories`、`result` | 类目表 (id、名称、条数) |
| `file_validate_filed` | 恢复前要检查存在的包内文件名列表 |
| `xor_obfuscated` | 是否做了异或 |
| `phone`、`version`、`password`、`create_time`、`end_time`、`setting_info` | 元数据 |
| `app_<包名>.apk` / `.data` / `.sddata` | 应用安装包和应用数据 |

## 单文件类目

联系人、短信、通话记录、日程、原子笔记、隐私笔记、系统设置，每类一个文件，文件名是键名的 sha1，例如 `sha1("notes")`、`sha1("encrypt_data_note")`。

内容是手机直接吐出来的分段流：每段「4 字节大端长度 + 内容」，每段 40960 字节，最后跟 4 个 0。拼起来以后：

- 联系人：vcf
- 短信 / 通话记录：xml
- 日程：json
- 原子笔记 / 隐私笔记：zip，里面是 `easy_transfer_db_*.json`（笔记表、文件夹、图片、待办）和笔记图片

## 应用数据

- `.data`：Android Backup v5，不压缩不加密，去掉 24 字节头就是 tar
- 微信 `weixin_disk` / `*.sddata`：只有本地文件头、没有中央目录的流式 zip，只能顺着头读

## 互传认这个包的条件

1. 备份目录下的子文件夹里有 `.backup`
2. `db/` 里有 `CURRENT`、`MANIFEST-*`，再加 `.ldb` 或非空的 `.log`；`db/` 坏了会试着解兜底 zip
3. `file_validate_filed` 列的文件全都在

## 恢复时怎么读

- 图片 / 视频：遍历 `album_` / `video_` 开头的键，读包里对应文件，解回原样，按 `filename` 和 `mtime` 打成 tar 发给手机
- 笔记：把分段流原样发给手机，手机自己拼
- 每个类目单独发，手机回 `{"error":"sucess","status":0}` 算成功（原文就拼成 sucess）
