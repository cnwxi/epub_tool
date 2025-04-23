# for mac
pyinstaller --noconfirm -w -i ./img/icon.icns -F Epub_Tool_TKUI.py -n Epub_Tool_TKUI;
cd ./dist
zip -r Epub_Tool_TKUI.zip ./Epub_Tool_TKUI.app;
rm -rf ./Epub_Tool_TKUI.app;
rm -rf ./Epub_Tool_TKUI;
echo "------finished------";
cd ..
rm -rf ./build;
rm ./Epub_Tool_TKUI.spec;