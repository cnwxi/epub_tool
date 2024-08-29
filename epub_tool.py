from utils.encrypt_epub import run as encrypt_run
from utils.decrypt_epub import run as decrypt_run
from utils.reformat_epub import run as reformat_run
import sys
import os
import argparse


def prepare_args():
    parser = argparse.ArgumentParser(description="Epub Tool")
    parser.add_argument("-i", help="input file/folder")
    parser.add_argument("-e", action="store_true", help="encrypt epub file")
    parser.add_argument("-d", action="store_true", help="decrypt epub file")
    parser.add_argument("-r", action="store_true", help="reformat epub file")
    return parser.parse_args()


def check_args(args):
    if not args.i:
        print("Please specify the input file/folder")
        sys.exit(1)
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
            print("Input file does not exist")
            sys.exit(1)
    return args


def main():
    prepare_args()
    log_file = "log.txt"
    with open(log_file, "w",encoding='utf-8') as f:
        sys.stdout = f
        args = prepare_args()
        args = check_args(args)
        if args.e:
            func = encrypt_run
        elif args.d:
            func = decrypt_run
        elif args.r:
            func = reformat_run
        else:
            print("Please specify the operation")
            sys.exit(1)
        tmp_run_result = []
        for file in args.i:
            ret=func(file)
            if ret==0:
                tmp_run_result.append(f"process {file} Success")
            else:
                tmp_run_result.append(f"process {file} Fail: {ret}")
    sys.stdout = sys.__stdout__
    print("Result:")
    for result in tmp_run_result:
        print(result)
    with open("result.txt", "w",encoding='utf-8') as f:
        for result in tmp_run_result:
            f.write(result + "\n")


if __name__ == "__main__":
    main()
