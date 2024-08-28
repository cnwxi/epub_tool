# for mac
pyinstaller -F epub_tool.py -n epub_tool;
echo "------finished------";
rm -rf ./build;
rm ./epub_tool.spec;