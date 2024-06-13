# epub_tool<br>
## 仓库介绍<br>
一些可用的epub工具<br>
1. 重构epub为规范格式_v2.8.3.py<br>
原始的百度贴吧帖子链接：[遥遥心航的帖子](https://jump2.bdimg.com/p/8090221625)<br>
遥遥心航提供的原始文件：[蓝奏云网盘链接](https://wwb.lanzoub.com/b01k016hg) 密码：`i89p`<br>
3. 重构epub并反文件名混淆.py<br>
4. 重构epub并加入文件名混淆.py<br>

## 怎么使用？<br>
### python执行<br>
1. 下载python3.8<br>
2. 使用`git clone https://github.com/cnwxi/epub_tool.git`克隆本仓库；或直接在网页下载源码压缩包，解压后得到py文件<br>
3. 使用命令行执行 `python 解压目标文件夹/epub_tool/**.py` 或修改py为pyz双击运行<br>
### windows<br>
1. 从[releases](https://github.com/cnwxi/epub_tool/releases)下载对应的exe文件<br>
2. 双击运行<br>
### mac<br>
1. 从[releases](https://github.com/cnwxi/epub_tool/releases)下载对应的无后缀可执行文件<br>
2. 双击运行<br>
### 自行编译
1. 执行`pip install pyinstaller`，下载安装依赖库
2. 参考`pyinstaller.*`文件自行打包成可执行文件

## 更新日志<br>
### 2024.04.23<br>
初始化仓库。<br>
### 2024.05.09<br>
针对多看~slim文件进行修改，处理html中使用`../`、`./`、`/`开头的链接。<br>

### 2024.05.16<br>
更改文件输出路径，由[lgernier](https://github.com/lgernierO)提交。<br>
### 2024.05.28<br>
修正`重构epub为规范格式_v2.8.3.py`中生成的content.opf文件内容格式，由[lgernier](https://github.com/lgernierO)提交。<br>
### 2024.06.07<br>
修改主函数逻辑，防止epub文件不存在导致的程序崩溃，由[lgernier](https://github.com/lgernierO)提交。<br>
加入CI自动构建，由[lgernier](https://github.com/lgernierO)提交<br>
加入CI自动发布，由[No Response](https://github.com/cnwxi)提交<br>
### 2024.06.08<br>
CI配置文件更新，由[lgernier](https://github.com/lgernierO)提交<br>
### 2024.06.12<br>
针对cover页面未混淆的情况做更改<br>
修改自动发布逻辑，修改py文件不触发CI，仅修改yml后触发。修改yml，无需手动执行才执行发布。<br>
### 2024.06.13<br>
更新yml文件，由[lgernier](https://github.com/lgernierO)提交。<br>

---

### THX<br>
thx to [遥遥心航](https://tieba.baidu.com/home/main?id=tb.1.7f262ae1.5_dXQ2Jp0F0MH9YJtgM2Ew)、[lgernier](https://github.com/lgernierO)<br>
