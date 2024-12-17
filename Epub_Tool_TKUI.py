import tkinter as tk
from tkinter.filedialog import askopenfiles
from tkinter import filedialog, ttk, NSEW, BOTH, messagebox
import os
from utils.encrypt_epub import run as encrypt_run
from utils.decrypt_epub import run as decrypt_run
from utils.reformat_epub import run as reformat_run
import sys
import threading
import subprocess
import webbrowser

root = tk.Tk()
root.title("Epub Tool")
root.minsize(512, 768)
root.resizable(True, True)
tmp_files_dic = {}
defalut_output_dir = None

# 创建一个 Frame 用于介绍
intro_frame = tk.Frame(root)
intro_frame.pack(padx=10, pady=10)
# 创建顶部介绍标签
intro_label = tk.Label(
    intro_frame,
    text="欢迎使用 Epub Tool\n此工具可帮助您处理电子书文件",
    font=("Arial", 14),
    fg="#333",
    justify="center",
)
intro_label.pack(side=tk.TOP, padx=10, pady=0)


def open_link(event):
    webbrowser.open_new("https://github.com/cnwxi/epub_tool")


link_label = tk.Label(
    intro_frame,
    text="访问本项目GITHUB仓库",
    font=("Arial", 10, "underline"),
    fg="CornflowerBlue",
)
link_label.pack()
link_label.bind("<Button-1>", open_link)

# 添加分界线
separator = ttk.Separator(root, orient="horizontal")  # 创建水平分界线
separator.pack(fill="x", pady=10, padx=10)

frame1 = tk.Frame(root)
frame1.pack(padx=10, pady=5)


# 刷新文件列表显示
def display_added_file(files):
    file_list.delete(0, tk.END)
    for file in files:
        file_list.insert(tk.END, file)


# 利用字典存储文件，避免重复添加
def store_file(files):
    for file in files:
        if file not in tmp_files_dic:
            tmp_files_dic[file] = 1


# 添加文件（可多选）
def add_file():
    files = filedialog.askopenfilenames(
        title="选择文件", filetypes=[("EPUB files", "*.epub *.EPUB")]
    )
    tmp_files = []
    for file in files:
        if file.lower().endswith(".epub"):
            tmp_files.append(os.path.normpath(file))
    store_file(tmp_files)
    display_added_file(tmp_files_dic.keys())


# 添加文件夹（单选文件夹）
def add_dir():
    dir = filedialog.askdirectory(title="选择文件夹")
    # 遍历文件夹所有文件
    tmp_files = []
    for root, dirs, files in os.walk(dir):
        for file in files:
            if file.lower().endswith(".epub"):
                tmp_files.append(os.path.normpath(os.path.join(root, file)))
    store_file(tmp_files)
    display_added_file(tmp_files_dic.keys())


# 删除选中的文件
def delete_selected():
    # 获取所有选中的项（返回的是一个元组）
    selected_indices = file_list.curselection()

    if not selected_indices:
        messagebox.showwarning("Warning", "未选中任何文件")
        return
    print(selected_indices)
    # 由于删除操作会改变列表框中的索引，所以需要从后往前删除
    for index in reversed(selected_indices):
        del tmp_files_dic[file_list.get(index)]  # 删除元素
        file_list.delete(index)


# 清空输入文件列表
def delete_all():
    file_list.delete(0, tk.END)
    tmp_files_dic.clear()


add_files_btn = tk.Button(frame1, text="添加文件", command=add_file, fg="green")
add_files_btn.pack(side=tk.LEFT, padx=5)

select_dir_btn = tk.Button(frame1, text="添加文件夹", command=add_dir, fg="royalblue")
select_dir_btn.pack(side=tk.LEFT, padx=5)


delete_button = tk.Button(frame1, text="删除所选", command=delete_selected, fg="orange")
delete_button.pack(side=tk.LEFT, padx=5)


delete_all_button = tk.Button(frame1, text="删除全部", command=delete_all, fg="red")
delete_all_button.pack(side=tk.LEFT, padx=5)

# 创建一个 Frame 用于放置 Listbox 和 Scrollbar
listbox_frame = tk.Frame(root)
listbox_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
listbox_label = tk.Label(
    listbox_frame, text="输入文件列表", font=("Arial", 14), fg="DimGray"
)
# listbox_label.pack(side=tk.LEFT, padx=5)
listbox_label.grid(row=0, column=0, sticky=tk.W, padx=5)
# 创建 Listbox
file_list = tk.Listbox(listbox_frame, selectmode=tk.EXTENDED)
file_list.grid(row=1, column=0, sticky=tk.NSEW)

# 创建垂直 Scrollbar
v_scrollbar = tk.Scrollbar(listbox_frame, orient=tk.VERTICAL, command=file_list.yview)
v_scrollbar.grid(row=1, column=1, sticky=tk.NS)

# 创建水平 Scrollbar
h_scrollbar = tk.Scrollbar(listbox_frame, orient=tk.HORIZONTAL, command=file_list.xview)
h_scrollbar.grid(row=2, column=0, sticky=tk.EW)

# 将 Scrollbar 绑定到 Listbox
file_list.config(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)

# 配置 grid 行列权重
listbox_frame.grid_rowconfigure(1, weight=1)
listbox_frame.grid_columnconfigure(0, weight=1)

# 添加分界线
separator = ttk.Separator(root, orient="horizontal")  # 创建水平分界线
separator.pack(fill="x", pady=10, padx=10)


def select_output_dir():
    global defalut_output_dir
    output_dir = os.path.normpath(filedialog.askdirectory(title="选择输出文件夹"))
    if output_dir and os.path.exists(output_dir):
        output_dir_label.config(text=f"输出路径: {output_dir}")
        defalut_output_dir = output_dir
        print(defalut_output_dir)


def open_output_dir():
    path = defalut_output_dir
    if path and os.path.exists(path):
        try:
            if sys.platform.startswith("darwin"):  # macOS
                subprocess.run(["open", path])
            elif os.name == "nt":  # Windows
                os.startfile(path)
            elif os.name == "posix":  # Linux
                subprocess.run(["xdg-open", path])
            else:
                messagebox.showerror("Warning", "不支持的操作系统")
        except Exception as e:
            messagebox.showerror("Warning", f"无法打开路径: {e}")
    else:
        messagebox.showwarning("Warning", "未指定输出路径")


def reset_output_dir():
    global defalut_output_dir
    defalut_output_dir = None
    output_dir_label.config(text=f"输出路径: 默认文件所在路径")


frame3 = tk.Frame(root)
frame3.pack(pady=5)
# 创建一个标签用于显示输出路径
show_btn = tk.Button(
    frame3, text="选择输出路径", command=select_output_dir, fg="royalblue"
)
show_btn.pack(side=tk.LEFT, padx=5)

open_btn = tk.Button(frame3, text="打开输出路径", command=open_output_dir, fg="orange")
open_btn.pack(side=tk.LEFT, padx=5)

reset_btn = tk.Button(frame3, text="重置输出路径", command=reset_output_dir, fg="red")
reset_btn.pack(side=tk.LEFT, padx=5)
frame4 = tk.Frame(root)
frame4.pack(pady=5)
output_dir_label = tk.Label(
    frame4, text="输出路径: 默认文件所在路径", font=("Arial", 12), fg="DimGray"
)
output_dir_label.pack(side=tk.LEFT, padx=5)
# 添加分界线
separator = ttk.Separator(root, orient="horizontal")  # 创建水平分界线
separator.pack(fill="x", pady=10, padx=10)


def start_progress(func, func_name, output_dir, *args):
    # 创建一个新的线程来运行传入的函数
    thread = threading.Thread(
        target=lambda: run_in_thread(func, func_name, output_dir, *args)
    )
    thread.start()


def run_in_thread(func, func_name, output_dir, *args):
    file_count = len(file_list.get(0, tk.END))
    if file_count == 0:
        messagebox.showwarning("Warning", "未添加任何文件")
        return
    progress["value"] = 0
    progress["maximum"] = file_count
    root.update_idletasks()
    for i in range(file_count):
        # 获取文件路径
        file_path = file_list.get(0)
        file_list.delete(0)
        tmp_files_dic.pop(file_path)
        # 执行操作
        with open("./log_file", "w", encoding="utf-8") as f:
            try:
                sys.stdout = f
                print(*args)
                ret = func(file_path, output_dir, *args)
                sys.stdout = sys.__stdout__

                if ret == 0:
                    result = f"^_^ {file_path} {func_name}成功"
                elif ret == "skip":
                    result = f"O_O {file_path} 跳过：{func_name}已处理"
                else:
                    result = f"T_T {file_path} 失败：{ret}"
            except Exception as e:
                sys.stdout = sys.__stdout__
                result = f"X_X {file_path} {func_name}处理时发生错误：{e}"

        # 显示处理结果
        result_list.insert(tk.END, f"{result}")
        progress["value"] += 1
        root.update_idletasks()


frame2 = tk.Frame(root)
frame2.pack(pady=5)
reformat_btn = tk.Button(
    frame2,
    text="格式化",
    command=lambda: start_progress(reformat_run, "格式化", defalut_output_dir),
    fg="green",
)
reformat_btn.pack(side=tk.LEFT, padx=5)

encrypt_btn = tk.Button(
    frame2,
    text="加密",
    command=lambda: start_progress(encrypt_run, "加密", defalut_output_dir),
    fg="red",
)
encrypt_btn.pack(side=tk.LEFT, padx=5)

decrypt_btn = tk.Button(
    frame2,
    text="解密",
    command=lambda: start_progress(decrypt_run, "解密", defalut_output_dir),
    fg="royalblue",
)
decrypt_btn.pack(side=tk.LEFT, padx=5)

# 创建一个 Frame 用于放置进度条
progress_frame = tk.Frame(root)
progress_frame.pack(fill=tk.X, padx=10, pady=5)

# 创建进度条
progress = ttk.Progressbar(
    progress_frame, orient=tk.HORIZONTAL, length=400, mode="determinate"
)
progress.pack(fill=tk.X, padx=5, pady=5)


# 创建一个 Frame 用于放置 Listbox 和 Scrollbar
result_box_frame = tk.Frame(root)
result_box_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
result_box_lable = tk.Label(
    result_box_frame, text="执行结果", font=("Arial", 14), fg="DimGray"
)
result_box_lable.grid(row=0, column=0, sticky=tk.W, padx=5)
# 创建 Listbox
result_list = tk.Listbox(result_box_frame, selectmode=tk.EXTENDED)
result_list.grid(row=1, column=0, sticky=tk.NSEW)

# 创建垂直 Scrollbar
v_scrollbar = tk.Scrollbar(
    result_box_frame, orient=tk.VERTICAL, command=file_list.yview
)
v_scrollbar.grid(row=1, column=1, sticky=tk.NS)

# 创建水平 Scrollbar
h_scrollbar = tk.Scrollbar(
    result_box_frame, orient=tk.HORIZONTAL, command=file_list.xview
)
h_scrollbar.grid(row=2, column=0, sticky=tk.EW)

# 将 Scrollbar 绑定到 Listbox
result_list.config(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)

# 配置 grid 行列权重
result_box_frame.grid_rowconfigure(1, weight=1)
result_box_frame.grid_columnconfigure(0, weight=1)

root.mainloop()
