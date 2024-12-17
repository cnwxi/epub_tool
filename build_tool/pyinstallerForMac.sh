# for mac
pyinstaller -F epub_tool_TKUI.py -n epub_tool_TKUI;
echo "------finished------";
rm -rf ./build;
rm ./epub_tool_TKUI.spec;