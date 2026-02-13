#pyinstaller -F -w Epub_Tool_TKUI.py -n Epub_Tool_TKUI;
pyinstaller --hidden-import=PIL._tkinter_finder -F -w Epub_Tool_TKUI.py -n Epub_Tool_TKUI;
rm -rf ./build
rm -f ./Epub_Tool_TKUI.spec