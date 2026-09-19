# 说明

VajraGo是用于爬取是[碧蓝幻想中文维基](https://gbf.huijiwiki.com)所需的数据、图片资源，以及协助进行Wiki更新的小工具

# 安装
最高测试过的环境版本为[Python 3.9.10(64-bit)](https://www.python.org/downloads/release/python-3910/)

```bash
pip install -r requirements.txt
```

# 配置文件
请参考[config.ini.example](config.ini.example)文件，去掉example后缀后放到脚本运行目录下

具体请参考范例运行目录的内容

# 功能使用

请参考范例运行目录的说明和bat指令

## 图片下载

下载指定类型图片：

```bash
python vajra_go.py image <类型>
```

例如下载技能图标：

```bash
python vajra_go.py image skill
```

下载战斗状态/Buff图标：

```bash
python vajra_go.py image status
```

`status` 使用**内置历史快照 + GBF 官方 CDN 增量扫描**。程序打包时已把 2026-09-19 的 GBFAL `buffs` 数据写入 `module_gbf/image/status_seed.py`，运行时不会访问 GBFAL。

- 内置快照包含当时已知的全部历史状态 ID 与后缀，因此首次运行不会再从 ID 1 开始逐个探测。
- 运行时只对各状态 ID 区段的最新前沿做增量扫描，默认使用 16 线程；可在 `[IMAGE]` 中设置 `status_threads=2~32` 调整。
- 新发现的数据会写入本地 `data/status_index.json`，以后继续在这个基础上增量更新。
- 新 ID 的各种特殊后缀会按 GBFAL updater 的命名规律并行探测，但不依赖 GBFAL 在线数据。
- 已下载的图片保存在 `IMAGE/status/`；当 `[IMAGE] new=yes` 时也会写入 `IMAGE/new/`。
- 状态图标探测产生的 404 不会写入全局 `skip_log.txt`，避免未来新增资源被永久跳过。

# 打包
脚本使用pyinstaller打包，打包指令附于pack.bat中

```bash
venv\Scripts\pyinstaller -c --onefile --version-file "VERSION_INFO" --workpath "build" --distpath "dist" --icon="res\vajra.ico" -y "vajra_go.py"
```
