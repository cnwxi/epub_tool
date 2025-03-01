# for mac
pyinstaller --noconfirm -w -i ./img/icon.ico -F Epub_Tool_TKUI.py -n Epub_Tool_TKUI;
echo "------finished------";
rm -rf ./build;
rm ./Epub_Tool_TKUI.spec;