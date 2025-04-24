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
min_width = 250
min_height = 768
root.geometry(f"{min_width}x{min_height}")
root.minsize(min_width, min_height)
root.resizable(True, True)
tmp_files_dic = {}
defalut_output_dir = None

# if sys.platform.startswith("darwin"):  # macOS
#     default_font = "PingFang SC"
# elif os.name == "nt":  # Windows
#     default_font = "SimSun"
# elif os.name == "posix":  # Linux
#     default_font = "WenQuanYi Zen Hei"
# else:
#     default_font = "Arial"  # 其他系统使用 Arial

# 创建一个 Frame 用于介绍
intro_frame = ttk.Frame(root)
intro_frame.pack(padx=10, pady=10)
# 创建顶部介绍标签
intro_label = ttk.Label(
    intro_frame,
    text="欢迎使用 Epub Tool\n此工具可帮助您处理电子书文件",
    # font=(default_font, 14, "bold"),
    # fg="#333",
    justify="center",
)
intro_label.pack(side=tk.TOP)


def open_link(event):
    webbrowser.open_new("https://github.com/cnwxi/epub_tool")

style = ttk.Style()
style.configure("Link.TLabel",
                foreground="royalblue",
                # font=(default_font, 10, "underline"),
                font=("TkDefaultFont",10, "underline"),
                cursor="hand2")
link_label = ttk.Label(
    intro_frame,
    text="访问本项目GITHUB仓库",
    style="Link.TLabel",
    cursor="hand2",
)
link_label.pack(side=tk.TOP)
link_label.bind("<Button-1>", open_link)

# 添加分界线
separator = ttk.Separator(root, orient="horizontal")  # 创建水平分界线
separator.pack(fill="x", padx=5, pady=5)

add_frame = ttk.Frame(root)
add_frame.pack(padx=10, pady=5)


# 刷新文件列表显示
def display_added_file(files):
    # 删除 Treeview 中的所有现有项目
    for item in file_list.get_children():
        file_list.delete(item)
    # 插入新的文件列表
    for file in files:
        file_list.insert('', 'end', values=(file,))


# 利用字典存储文件，避免重复添加
def store_file(files):
    for file in files:
        if file not in tmp_files_dic:
            tmp_files_dic[file] = 1


# 添加文件（可多选）
def add_file():
    files = filedialog.askopenfilenames(title="选择文件",
                                        filetypes=[("EPUB files",
                                                    "*.epub *.EPUB")])
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
    # 获取所有选中的项（返回的是一个元组，包含项目的 ID）
    selected_items = file_list.selection()
    if not selected_items:
        messagebox.showwarning("Warning", "未选中任何文件")
        return
    # 从后往前删除选中的项目
    for item in reversed(selected_items):
        # 获取项目的值
        file = file_list.item(item, 'values')[0]
        # 删除字典中的元素
        if file in tmp_files_dic:
            del tmp_files_dic[file]
        # 删除 Treeview 中的项目
        file_list.delete(item)
    # 显示删除后的文件列表
    display_added_file(tmp_files_dic.keys())

# 清空输入文件列表
def delete_all():
    # 删除 Treeview 中的所有项目
    for item in file_list.get_children():
        file_list.delete(item)
    # 清空字典
    tmp_files_dic.clear()


add_files_btn = ttk.Button(add_frame,
                          text="添加文件",
                          command=add_file,
                          )
                          
add_files_btn.pack(side=tk.LEFT, padx=5)

select_dir_btn = ttk.Button(
    add_frame,
    text="添加文件夹",
    command=add_dir,
)
select_dir_btn.pack(side=tk.LEFT, padx=5)

delete_button = ttk.Button(
    add_frame,
    text="删除所选",
    command=delete_selected,
)
delete_button.pack(side=tk.LEFT, padx=5)

delete_all_button = ttk.Button(add_frame,
                              text="删除全部",
                              command=delete_all,
                              )
delete_all_button.pack(side=tk.LEFT, padx=5)

# 创建一个 Frame 用于放置 Listbox 和 Scrollbar
listbox_frame = ttk.Frame(root)
listbox_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

file_list = ttk.Treeview(listbox_frame,
                         selectmode="extended",
                         columns=('index','file_name','path',),
                         show='headings')
file_list.heading('index',text='序号',anchor='center')
file_list.heading('file_name', text='文件名')
file_list.heading('path', text='文件路径')
# file_list.column('Value', width=min_width, stretch=False)  # 设置列宽为 500 像素，禁用自动调整
file_list.grid(row=1, column=0, sticky=tk.NSEW)
# 添加测试数据到 file_list
for i in range(10):  # 添加 10 行测试数据
    file_list.insert('', 'end', values=(f"{i}",f"文件{i}", f"/Users/xavierwhite/Documents/Projects/epub_tool/test_folder/long_path_example_{i}.epub"))
# 创建垂直 Scrollbar
v_scrollbar = ttk.Scrollbar(listbox_frame,
                           orient=tk.VERTICAL,
                           command=file_list.yview,
                        #    width=15
                           )
v_scrollbar.grid(row=1, column=1, sticky=tk.NS)

def adjust_column_width(event):
    # 获取窗口当前宽度
    new_width = event.width
    # 设置列宽为窗口宽度的一部分（例如 80%）
    file_list.column('Value', width=int(new_width*2))

# 绑定窗口大小变化事件
root.bind('<Configure>', adjust_column_width)

# 创建水平 Scrollbar
# h_scrollbar = ttk.Scrollbar(listbox_frame,
#                            orient=tk.HORIZONTAL,
#                            command=file_list.xview,
#                         #    width=15
#                            )
# h_scrollbar.grid(row=2, column=0, sticky=tk.EW)

# 将 Scrollbar 绑定到 Listbox
file_list.configure(yscrollcommand=v_scrollbar.set,
                #  xscrollcommand=h_scrollbar.set
                 )

# 配置 grid 行列权重
listbox_frame.grid_rowconfigure(1, weight=1)
listbox_frame.grid_columnconfigure(0, weight=1)

# 添加分界线
separator = ttk.Separator(root, orient="horizontal")  # 创建水平分界线
separator.pack(fill="x", padx=5, pady=5)


def select_output_dir():
    global defalut_output_dir
    output_dir = os.path.normpath(filedialog.askdirectory(title="选择输出文件夹"))
    if output_dir != '.' and os.path.exists(output_dir):
        defalut_output_dir = output_dir
        if len(output_dir) > 30:
            length = len(output_dir) - 15
            output_dir = output_dir[:15] + "..." + output_dir[length:]
        output_dir_label.config(text=f"输出路径: {output_dir}")
        output_dir_label.config(fg="royalblue")
        output_dir_label.update()
        result_list.insert(tk.END, f"设置输出路径成功: {output_dir}")
        root.update_idletasks()


def open_output_dir(event):
    path = defalut_output_dir
    if path is not None and os.path.exists(path):
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
        # messagebox.showwarning("Warning", "未指定输出路径")
        pass


def reset_output_dir():
    global defalut_output_dir
    defalut_output_dir = None
    output_dir_label.config(text=f"输出路径: 默认文件所在路径")
    output_dir_label.config(fg="DimGray")
    output_dir_label.update()
    result_list.insert(tk.END, "重置输出路径成功")
    root.update_idletasks()


outdir_frame = tk.Frame(root)
outdir_frame.pack(padx=10, pady=5)
# 创建一个标签用于显示输出路径
show_btn = ttk.Button(
    outdir_frame,
    text="选择输出路径",
    command=select_output_dir,
)
show_btn.pack(side=tk.LEFT, padx=5)

reset_btn = ttk.Button(
    outdir_frame,
    text="重置输出路径",
    command=reset_output_dir,
)
reset_btn.pack(side=tk.LEFT, padx=5)
frame4 = tk.Frame(root)
frame4.pack(pady=5)

style.configure("FileLink.TLabel",font=("TkDefaultFont",10,"underline"),foreground="DimGray",cursor="hand2")
output_dir_label = ttk.Label(
    frame4,
    text="输出路径: 默认文件所在路径",
    style="FileLink.TLabel",
    # font=(default_font, 10, "underline"),
    # fg="DimGray",
    # cursor="hand2",
)
output_dir_label.pack(side=tk.LEFT, padx=5)
output_dir_label.bind("<Button-1>", open_output_dir)

# 添加分界线
separator = ttk.Separator(root, orient="horizontal")  # 创建水平分界线
separator.pack(fill="x", pady=5, padx=5)


def start_progress(func, func_name, output_dir, *args):
    # 创建一个新的线程来运行传入的函数
    thread = threading.Thread(
        target=lambda: run_in_thread(func, func_name, output_dir, *args))
    thread.start()


def run_in_thread(func, func_name, output_dir, *args):
    file_count = len(file_list.get_children())
    if file_count == 0:
        messagebox.showwarning("Warning", "未添加任何文件")
        return
    progress["value"] = 0
    progress["maximum"] = file_count
    root.update_idletasks()

    children = file_list.get_children()
    for item in children:
        # 获取文件路径
        file_path = file_list.item(item, 'values')[0]
        file_list.delete(item)
        tmp_files_dic.pop(file_path)
        # 执行操作
        try:
            ret = func(file_path, output_dir, *args)
            if ret == 0:
                result = f"😄｜ {file_path} {func_name}成功"
            elif ret == "skip":
                result = f"😳｜ {file_path} 跳过：{func_name}已处理"
            else:
                result = f"😭｜ {file_path} 失败：{ret}"
        except Exception as e:
            result = f"😵‍💫 {file_path} {func_name}处理时发生错误：{e}"

        # 显示处理结果
        result_list.insert("", "end", values=(result,))
        progress["value"] += 1
        root.update_idletasks()


op_frame = ttk.Frame(root)
op_frame.pack(padx=10, pady=5)
reformat_btn = ttk.Button(
    op_frame,
    text="格式化",
    command=lambda: start_progress(reformat_run, "格式化", defalut_output_dir),
)
reformat_btn.pack(side=tk.LEFT, padx=5)

decrypt_btn = ttk.Button(
    op_frame,
    text="解密",
    command=lambda: start_progress(decrypt_run, "解密", defalut_output_dir),
)
decrypt_btn.pack(side=tk.LEFT, padx=5)

encrypt_btn = ttk.Button(
    op_frame,
    text="加密",
    command=lambda: start_progress(encrypt_run, "加密", defalut_output_dir),
)
encrypt_btn.pack(side=tk.LEFT, padx=5)

# 创建一个 Frame 用于放置进度条
progress_frame = ttk.Frame(root)
progress_frame.pack(fill=tk.X, padx=10, pady=0)

# 创建进度条
progress = ttk.Progressbar(progress_frame,
                           orient=tk.HORIZONTAL,
                           length=400,
                           mode="determinate",
                           )
progress.pack(fill=tk.X, padx=5, pady=0)

# 创建一个 Frame 用于放置 Listbox 和 Scrollbar
result_box_frame = ttk.Frame(root)
result_box_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

result_list = ttk.Treeview(result_box_frame,
                          columns=("result",),
                          show="headings",
                        #   height=10,
                          )
result_list.heading("result", text="执行结果")
result_list.grid(row=1, column=0, sticky=tk.NSEW)
# 创建垂直 Scrollbar
v_scrollbar_result = ttk.Scrollbar(result_box_frame,
                                  orient=tk.VERTICAL,
                                  command=result_list.yview,
                                #   width=10
                                  )
v_scrollbar_result.grid(row=1, column=1, sticky=tk.NS)

# 创建水平 Scrollbar
h_scrollbar_result = ttk.Scrollbar(result_box_frame,
                                  orient=tk.HORIZONTAL,
                                  command=result_list.xview,
                               
                                #   width=15
                                  )
h_scrollbar_result.grid(row=2, column=0, sticky=tk.EW)

# 将 Scrollbar 绑定到 Listbox
result_list.config(yscrollcommand=v_scrollbar_result.set,
                   xscrollcommand=h_scrollbar_result.set)

# 配置 grid 行列权重
result_box_frame.grid_rowconfigure(1, weight=1)
result_box_frame.grid_columnconfigure(0, weight=1)

root.mainloop()
