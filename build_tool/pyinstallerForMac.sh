# for mac
pyinstaller --noconfirm -w -i ./img/icon.icns Epub_Tool_TKUI.py -n Epub_Tool_TKUI;
cd ./dist

# hdiutil create -volname Epub_Tool_TKUI -srcfolder ./Epub_Tool_TKUI.app -ov -format UDZO ./Epub_Tool_TKUI.dmg;

create-dmg \
    --volname "Epub_Tool_TKUI" \
    --window-pos 400 200 \
    --window-size 660 400 \
    --icon-size 100 \
    --icon "Epub_Tool_TKUI.app" 160 185 \
    --hide-extension "Epub_Tool_TKUI.app"\
    --app-drop-link 500 185 \
    --no-internet-enable \
    "./Epub_Tool_TKUI.dmg" \
    "./Epub_Tool_TKUI.app";

# zip -r Epub_Tool_TKUI.zip ./Epub_Tool_TKUI.app;
rm -rf ./Epub_Tool_TKUI.app;
rm -rf ./Epub_Tool_TKUI;
echo "------finished------";
cd ..
rm -rf ./build;
rm ./Epub_Tool_TKUI.spec;