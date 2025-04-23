

## Ⅰ epub_tool介绍<br>


<div>
<img src="./img/icon.ico" alt="图片名称" style="width:10em">
</div>


[![GitHub Releases](https://img.shields.io/github/v/release/cnwxi/epub_tool)](https://github.com/cnwxi/epub_tool/releases/latest)  
 [![GitHub stars](https://img.shields.io/github/stars/cnwxi/epub_tool)](https://github.com/cnwxi/epub_tool/stargazers)
  [![GitHub forks](https://img.shields.io/github/forks/cnwxi/epub_tool)](https://github.com/cnwxi/epub_tool/network/members)

Epub Tool->ET->E-Book Thor->📖🔨

<details>
  <summary>包含一些可用的epub工具，用于epub文件的重构、解密、加密。</summary>
  <p>


1. `重构epub为规范格式_v2.8.3.py`->`utils\reformat_epub.py`<br>
作用：见原文件名。<br>
原始的百度贴吧帖子链接：[遥遥心航的帖子](https://jump2.bdimg.com/p/8090221625)。<br>
遥遥心航提供的原始文件：[蓝奏云网盘链接](https://wwb.lanzoub.com/b01k016hg) 密码：`i89p`。<br>
2. `重构epub并反文件名混淆.py`->`utils\decrypt_epub.py`<br>
作用：见原文件名。<br>
3. `重构epub并加入文件名混淆.py`->`utils\encrypt_epub.py`<br>
作用：见原文件名。<br>
4. `epub_tool.py`<br>
作用：对上述工具的整合的命令行程序。<br>
5. `epub_tool_TKUI.py`<br>
作用：对上述工具的整合的带操作界面的程序。<br>

注：重构会严格保证文件夹分类和文件名后缀。[https://github.com/cnwxi/epub_tool/issues/13]
  </p>
</details>

## Ⅱ 怎么使用？（仅针对最新版本）<br>

<details>
  <summary>python源码执行</summary>
  <p>

1. 下载python3.8；<br> 
2. 使用`git clone https://github.com/cnwxi/epub_tool.git`克隆本仓库；或直接在网页下载源码压缩包，解压后得到py文件；<br>
3. 准备依赖库，在终端输入`python -m pip install -r requirements.txt`;<br>
4. 执行py文件。<br> 
    - 单个工具执行：<br> 
    1. 使用命令行执行 `python 解压目标文件夹/epub_tool/utils/**.py` 或修改py为pyz双击运行。<br>
    - 整合工具执行：<br> 
    1. 使用命令行执行 `python 解压目标文件夹/epub_tool/epub_tool.py -i 需要处理的epub文件或者所在文件夹 -e/d/r` 其中e、d、r为不同的处理模式，分别是混淆`-e`、反混淆`-d`、重新格式化`-r`。<br> 
    2. 也可使用命令行执行 `python 解压目标文件夹/epub_tool/epub_tool.py -i 需要处理的epub文件或者所在文件夹 -m 处理模式`，处理模式为e、d、r。<br> 

  </p>
</details>

<!-- 

<details> 
  <summary>命令行程序</summary>
  <p>

1. 从[releases](https://github.com/cnwxi/epub_tool/releases)下载对应的可执行文件；<br>
2. Windows可以直接双击可执行文件；<br>
![image](https://github.com/user-attachments/assets/53ed7c69-3f59-44fd-9c59-b754ada6c5a8)
3. 或使用命令行工具`CMD、Power Shell、Terminal`执行；<br>
4. 如提示无权限运行，可在终端输入 `chmod +x /可执行文件所在路径` （macOS：还需进入“设置-安全性与隐私-通用-允许从以下位置下载的APP”点击“仍要打开”）<br>
参考如图：<br>
![image](https://github.com/user-attachments/assets/18dd97fb-cc39-47d4-b5eb-fb48b01a28cd)
![image](https://github.com/user-attachments/assets/e0f7e997-6912-4792-a72d-f415e0525e34)
5. 参数列表参考如下：<br>
\-i  后面接需要处理的epub文件或所在文件夹；<br>
\-e  无需后接任何参数，指定程序对epub进行混淆处理；<br>
\-d  无需后接任何参数，指定程序对epub进行反混淆处理；<br>
\-r  无需后接任何参数，指定程序对epub进行格式化处理。<br>
\-m  后接指定的处理模式，e、d、r。（可选，效果同上-e、-d、-r）
6. 现在输入为文件夹路径时会提醒选择文件执行edr操作或所有文件执行edr操作。<br> 
 ![image](https://github.com/user-attachments/assets/4c5d6a6e-2e6e-427d-9251-8d9e4c2a3a68) 

- 举例：<br>
在可执行文件所在文件夹打开命令行工具（或打开命令行工具后切换到可执行文件所在文件夹）。<br>
可使用的命令行工具如cmd/powershell/terminal等。<br>
输入`Windows_epub_tool.exe -i epub文件路径或所在文件夹路径 -d`或`Windows_epub_tool.exe -i epub文件路径或所在文件夹路径 -m d`
并回车（注意不同平台可执行文件名不一致）。<br>
此命令行指定程序读取指定目录下所有epub文件，并对这些文件进行反混淆。<br>

  </p>
</details>

<details>
  <summary>Windows系统CMD命令行操作演示</summary>
  <p>
    
1. 可执行文件已下载至C:\Users\Administrator\Downloads\Programs位置，打开文件管理器，进入对应目录。如图：<br>
<img src="https://github.com/user-attachments/assets/0cd71e92-714b-4f44-8060-ad5d353ebb7a" width="600"><br>
2. 在最上方地址输入框输入cmd并回车，则可以直接在此目录下打开cmd。如图：<br>
<img src="https://github.com/user-attachments/assets/2f23826d-480a-4526-9dbe-f3fb06f5fa35" width="600"><br>
<img src="https://github.com/user-attachments/assets/8def1166-f7f6-4738-bed8-0b3057e1d81b" width="600"><br>
3. 输入 Windows_epub_tool.exe -i epub文件路径或所在文件夹路径 -d （注：此为演示命令行，具体的输入文件/文件夹和执行模式需要你自行指定）<br>
或 Windows_epub_tool.exe -i epub文件路径或所在文件夹路径 -m d 。如图：<br>
<img src="https://github.com/user-attachments/assets/0e1c703f-1c78-4242-9dce-480219805005" width="600"><br>
  
  </p>
</details> 

-->

<details>
  <summary>可视化界面程序</summary>
  <p>

    
>（注：同样会在可执行程序所在路径生成log日志文件，mac文件写入位置为`/Applications/Epub_Tool_TKUI.app/Contents/MacOS/log.txt`）<br>
> （Mac若提示无法验证安全性，请参考[Apple官网Mac使用手册](https://support.apple.com/zh-cn/guide/mac-help/mchleab3a043/mac)；Windows若报告病毒文件请忽略警告，允许文件保留本地。）

- UI预览，具体界面可能随后续更新改动<br>

  - mac<br> 
<img width="300" alt="mac" src="https://github.com/user-attachments/assets/dd3ba06c-5fb7-4439-88d6-4ff67ed1f0db" /><br> 

  - windows<br> 
<img width="260" alt="windows" src="https://github.com/user-attachments/assets/99acedf7-2f41-44bb-9059-6de9d36dd1d0" /><br> 

  </p>
</details>

## Ⅲ 执行遇到错误？

<details>
  <summary>epub无法正常规范/混淆/反混淆</summary><br>
  <p>
    1、优先解压文件，查看其中content.opf文件，检查是否存在问题。若无法解决，在Issues区提交issue并附带原文件。[https://github.com/cnwxi/epub_tool/issues/8 https://github.com/cnwxi/epub_tool/issues/10]<br>
  </p>
  <p>
    2、若下载文件名带“精品”二字，且解压后文件夹内包含“/META-INF/encryption.xml”，检查此文件内是否有“ZhangYue.Inc”字样。若满足则此文件为掌阅加密书籍，为规避版权问题，此处不提供解密程序，请使用「掌阅」打开阅读。[https://github.com/cnwxi/epub_tool/issues/19]<br> 
  </p>

</details>

## Ⅳ 更新日志<br>
<details>
  <summary>点击以展开</summary>
  <p>

### 2025.04.23<br>
移除命令行程序编译；移除mac编译-F参数；添加icon.icns图标适配macOS，优化显示效果；移除Ubuntu（Linux系统）编译。<br>
### 2025.03.20<br>
修复失效的自定义输出路径。<br>
### 2025.03.01<br>
图标打包进可执行文件。<br>
### 2025.02.20<br>
更新图标。<br>
### 2024.12.25<br>
修复在mac上的日志文件写入位置，更改日志写入方式，清理原始脚本中重复的无效循环。<br>
### 2024.12.24<br>
Update build.yml。https://github.com/cnwxi/epub_tool/pull/17<br>
### 2024.12.23<br>
调整UI、取消push自动构建。<br>
### 2024.12.17<br>
修复UI显示问题,分支整合。<br>
### 2024.12.16<br>
创建新分支TKUI，实现基本UI DEMO，功能已整合。<br>
### 2024.11.17<br>
添加文件夹手动选择需要处理文件，输入文件序号进行选择，不再是默认处理文件夹内全部epub文件，添加了输入检测提示，错误后会返回重新输入。https://github.com/cnwxi/epub_tool/pull/15<br>
### 2024.10.24<br>
修复未处理输入时拖入文件带引号导致的文件路径检查错误。<br>
### 2024.09.09<br>
因额外依赖库未打包到可执行文件，重新打包可执行文件。<br>
更新相关使用教程。<br>
### 2024.09.08<br>
为避免有人不会使用命令行工具，更新Windows系统下相关操作的基础流程。<br>
程序允许直接双击执行，后续再输入参数。<br>
对应操作忽略固定后缀跳过文件处理。_encrypt、_decrypt、_reformat<br>
### 2024.08.29<br>
修复混淆ID导致的反混淆不完全。<br>
修复存在异常opf时程序闪退问题。<br>
更新日志记录。<br>
### 2024.08.28<br>
整合代码，使用命令行批量处理epub文件。<br>
支持输入单个epub文件或epub文件所在文件夹，支持子目录遍历。<br>
修改输出路径，现为原epub文件同级路径，通过添加不同后缀`encrypt\decrypt\reformat`区分原文件和处理后文件。<br>
### 2024.06.19<br>
代码更新，使用相似度计算覆盖opf文件中未混淆的其他文件名情况。<br>
### 2024.06.13<br>
更新yml文件。https://github.com/cnwxi/epub_tool/pull/9<br>
### 2024.06.12<br>
针对cover页面未混淆的情况做更改。<br>
修改自动发布逻辑，修改py文件不触发CI，仅修改yml后触发。修改yml，无需手动执行才执行发布。<br>
### 2024.06.08<br>
CI配置文件更新。https://github.com/cnwxi/epub_tool/pull/6 https://github.com/cnwxi/epub_tool/pull/7<br>
### 2024.06.07<br>
修改主函数逻辑，防止epub文件不存在导致的程序崩溃。https://github.com/cnwxi/epub_tool/pull/4<br>
加入CI自动构建。https://github.com/cnwxi/epub_tool/pull/5<br>
加入CI自动发布。<br>
### 2024.05.28<br>
修正`重构epub为规范格式_v2.8.3.py`中生成的content.opf文件内容格式。https://github.com/cnwxi/epub_tool/pull/3<br>
### 2024.05.16<br>
更改文件输出路径。https://github.com/cnwxi/epub_tool/pull/2<br>
### 2024.05.09<br>
针对多看~slim文件进行修改，处理html中使用`../`、`./`、`/`开头的链接。<br>
### 2024.04.23<br>
初始化仓库。<br>

  </p>
</details>

## Ⅴ 鸣谢<br>
感谢以下用户对此项目的贡献：
- [遥遥心航](https://tieba.baidu.com/home/main?id=tb.1.7f262ae1.5_dXQ2Jp0F0MH9YJtgM2Ew)
- [lgernier](https://github.com/lgernierO)<br>
