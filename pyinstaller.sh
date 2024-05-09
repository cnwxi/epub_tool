# for mac
pyinstaller -F 重构epub并反文件名混淆.py -n decrypt_epub;
echo "------decrypt_epub finished------";
pyinstaller -F 重构epub并加入文件名混淆.py -n encrypt_epub;
echo "------encrypt_epub finished------";
pyinstaller -F 重构epub为规范格式_v2.8.3.py -n rebuild_epub;
echo "------rebuild_epub finished------";
rm -rf ./build
rm ./decrypt_epub.spec
rm ./encrypt_epub.spec
rm ./rebuild_epub.spec

# for windows
# pyinstaller -F 重构epub并反文件名混淆.py -n decrypt_epub;
# echo "------decrypt_epub finished------";
# pyinstaller -F 重构epub并加入文件名混淆.py -n encrypt_epub;
# echo "------encrypt_epub finished------";
# pyinstaller -F 重构epub为规范格式_v2.8.3.py -n rebuild_epub;
# echo "------rebuild_epub finished------";
# rm ./build
# rm ./decrypt_epub.spec
# rm ./encrypt_epub.spec
# rm ./rebuild_epub.spec

