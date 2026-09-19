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

`status` 不再依赖 GBFAL 或其他第三方在线索引，而是直接检查 GBF 官方 CDN，并在本地维护 `data/status_index.json`。

- 第一次运行时会按状态图标的 ID 分段建立本地索引，因此耗时会明显长于后续运行。
- 后续运行会基于本地索引做增量探测，并复查各分段较新的状态 ID，以发现新的状态图标和特殊后缀。
- 已下载的图片保存在 `IMAGE/status/`；程序也会从该目录已有文件补充本地索引。
- 当 `config.ini` 的 `[IMAGE] new=yes` 时，新下载文件也会写入 `IMAGE/new/`。
- 如果需要重新建立完整索引，可以删除 `data/status_index.json` 后再次执行 `image status`。已有的 `IMAGE/status/` 文件仍会被自动识别并加入新索引。
- 状态图标的 404 不会写入全局 `skip_log.txt`，避免未来新增资源被永久跳过。

# 打包
脚本使用pyinstaller打包，打包指令附于pack.bat中

```bash
venv\Scripts\pyinstaller -c --onefile --version-file "VERSION_INFO" --workpath "build" --distpath "dist" --icon="res\vajra.ico" -y "vajra_go.py"
```
