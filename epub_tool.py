from utils.encrypt_epub import run as encrypt_run
from utils.decrypt_epub import run as decrypt_run
from utils.reformat_epub import run as reformat_run
import sys
import os
import argparse


def prepare_args():
    parser = argparse.ArgumentParser(description='Epub Tool')
    parser.add_argument('-i', help='input file/folder')
    parser.add_argument('-e', action='store_true', help='encrypt epub file')
    parser.add_argument('-d', action='store_true', help='decrypt epub file')
    parser.add_argument('-r', action='store_true', help='reformat epub file')
    return parser.parse_args()

def check_args(args):
    if not args.i:
        print('Please specify the input file/folder')
        sys.exit(1)
    if not os.path.exists(args.i):
        print('Input file/folder does not exist')
        sys.exit(1)
    # 判断输入文件是否为文件夹
    if os.path.isdir(args.i):
        file_list = sorted(os.listdir(args.i))
        args.i = [os.path.join(args.i, file) for file in file_list if file.endswith('.epub')]
    else:
        args.i = [args.i]
    for i in args.i:
        if not os.path.exists(i):
            print('Input file does not exist')
            sys.exit(1)
    return args


def main():
    prepare_args()
    args = prepare_args()
    args = check_args(args)
    if args.e:
        func=encrypt_run
    elif args.d:
        func=decrypt_run
    elif args.r:
        func=reformat_run
    else:
        print('Please specify the operation')
        sys.exit(1)
    tmp_run_result = []
    for file in args.i:
        if func(file) == 0:
            tmp_run_result.append(f'{file} Success')
        else:
            tmp_run_result.append(f'{file} Fail')
    for result in tmp_run_result:
        print(result)
        
if __name__ == '__main__':
    main()