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
    parser.add_argument("-m", help="mode: e: encrypt, d: decrypt, r: reformat")
    return parser.parse_args()


def check_args(args):
    if not args.i:
        print("未指定epub文件路径或文件夹路径")
        args.i = input("请输入epub文件路径或文件夹路径：")
    # 判断输入文件是否为文件夹
    if os.path.isdir(args.i):
        file_list = []
        for root, dirs, files in os.walk(args.i):
            for file in files:
                if file.endswith(".epub"):
                    file_list.append(os.path.join(root, file))
        args.i = file_list
    else:
        args.i = [args.i]
    for i in args.i:
        if not os.path.exists(i):
            print("输入路径不存在")
            sys.exit(1)
    return args


def check_mode(args):
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
        print("未指定处理模式。e：加密，d：解密，r：重排")
        args.m = input("请输入操作：")
        func = check_mode(args)
    print(f"处理模式：{process}")
    return process, func


def main():
    prepare_args()
    log_file = "log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        args = prepare_args()
        args = check_args(args)
        process, func = check_mode(args)
        tmp_run_result = []
        with tqdm(total=len(args.i),ncols=100,desc=f'{process}文件') as pbar:
            for file in args.i:
                sys.stdout = f
                ret = func(file)
                sys.stdout = sys.__stdout__
                if ret == 0:
                    result = f"^_^ {file} 成功"
                elif ret == "skip":
                    result = f"O_O {file} 跳过"
                else:
                    result = f"T_T {file} 失败: {ret}"
                tmp_run_result.append(result)
                pbar.update(1)

    print(f"{process}结果:")
    for result in tmp_run_result:
        print(result)
    with open("result.txt", "w", encoding="utf-8") as f:
        for result in tmp_run_result:
            f.write(result + "\n")
    input("按下回车退出...")


if __name__ == "__main__":
    main()
