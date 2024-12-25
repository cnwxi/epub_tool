from utils.encrypt_epub import run as encrypt_run
from utils.decrypt_epub import run as decrypt_run
from utils.reformat_epub import run as reformat_run
import sys
import os
import argparse
from tqdm import tqdm


def prepare_args():
    parser = argparse.ArgumentParser(description="Epub Tool")
    parser.add_argument("-i", help="input file/folder")
    parser.add_argument("-e", action="store_true", help="encrypt epub file")
    parser.add_argument("-d", action="store_true", help="decrypt epub file")
    parser.add_argument("-r", action="store_true", help="reformat epub file")
    parser.add_argument(
        "-m",
        help="mode: e: encrypt, d: decrypt, r: reformat, c: choose files, a: all files",
    )
    return parser.parse_args()


def clean_input_path(input_path):
    return input_path.strip("'").strip('"').strip()


def check_args(args):
    while True:
        if not args.i:
            args.i = input("请输入epub文件路径或文件夹路径：")
        args.i = clean_input_path(args.i)

        # 判断输入文件是否为文件夹
        if os.path.isdir(args.i):
            file_list = [
                os.path.join(root, file)
                for root, _, files in os.walk(args.i)
                for file in files
                if file.endswith(".epub")
            ]

            if file_list:
                while True:
                    if not args.m:
                        args.m = (
                            input("请输入操作（c：手动选择，a：全部文件）：")
                            .strip()
                            .lower()
                        )

                    if args.m == "c":
                        mode = "手动选择"
                    elif args.m == "a":
                        mode = "全部文件"
                    else:
                        print("输入错误，请输入 'c' 或 'a'")
                        args.m = None
                        continue

                    print(f"处理模式：{mode}")

                    if mode == "手动选择":
                        print("以下是文件夹中的epub文件：")
                        for idx, file in enumerate(file_list):
                            print(f"{idx + 1}: {file}")

                        while True:
                            selected_files = input(
                                "请输入你想要处理的文件序号（多个序号请用空格分开）："
                            )
                            selected_indices = selected_files.split()

                            try:
                                args.i = [
                                    file_list[int(index) - 1]
                                    for index in selected_indices
                                ]
                                break
                            except (ValueError, IndexError):
                                print("输入错误，请确保输入的是有效的文件序号")

                    elif mode == "全部文件":
                        args.i = file_list
                    break
            else:
                print("文件夹中没有找到任何epub文件，请重新输入")
                args.i = None
                continue
        else:
            if os.path.exists(args.i):
                args.i = [args.i]
            else:
                print(f"输入路径不存在：{args.i}，请重新输入")
                args.i = None
                continue

        return args


def check_mode(args):
    while True:
        if args.e or args.m == "e":
            process = "加密"
            func = encrypt_run
        elif args.d or args.m == "d":
            process = "解密"
            func = decrypt_run
        elif args.r or args.m == "r":
            process = "重排"
            func = reformat_run
        else:
            while True:
                args.m = (
                    input("请输入操作（e：加密，d：解密，r：重排）：").strip().lower()
                )
                if args.m in ["e", "d", "r"]:
                    break
                else:
                    print("输入错误，请输入 'e'、'd' 或 'r'")

            continue

        return process, func


def main():
    print("-欢迎使用Epub Tool-")
    print("-此程序由cnwxi提供-")
    args = prepare_args()
    args = check_args(args)
    process, func = check_mode(args)
    print(f"处理模式：{process}")
    tmp_run_result = []

    with tqdm(total=len(args.i), ncols=100, desc=f"{process}文件") as pbar:
        for file in args.i:
            try:
                ret = func(file)
                if ret == 0:
                    result = f"^_^ {file} 成功"
                elif ret == "skip":
                    result = f"O_O {file} 跳过：已{process}"
                else:
                    result = f"T_T {file} 失败：{ret}"
            except Exception as e:
                result = f"X_X {file} 处理时发生错误：{e}"
            tmp_run_result.append(result)
            pbar.update(1)

    print(f"{process}结果：")
    for result in tmp_run_result:
        print(result)

    with open("result.txt", "w", encoding="utf-8") as f:
        for result in tmp_run_result:
            f.write(result + "\n")

    input("按下回车退出...")


if __name__ == "__main__":
    main()
