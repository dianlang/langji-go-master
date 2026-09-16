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

`status` 会读取 [GBFAL](https://github.com/MizaGBF/GBFAL) 的 `buffs` 索引，只请求 GBFAL 已确认存在的状态图标及其特殊后缀，避免对 CDN 进行大范围暴力枚举。图片保存到 `IMAGE/status/`；当 `config.ini` 的 `[IMAGE] new=yes` 时，新下载文件也会写入 `IMAGE/new/`。

# 打包
脚本使用pyinstaller打包，打包指令附于pack.bat中

```bash
venv\Scripts\pyinstaller -c --onefile --version-file "VERSION_INFO" --workpath "build" --distpath "dist" --icon="res\vajra.ico" -y "vajra_go.py"
```
