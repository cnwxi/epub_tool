import tkinter as tk

# from tkinter.filedialog import askopenfiles
from tkinter.font import Font
from tkinter import filedialog, ttk, messagebox
import os
from utils.encrypt_epub import run as encrypt_run
from utils.decrypt_epub import run as decrypt_run
from utils.reformat_epub import run as reformat_run
from utils.encrypt_font import FontEncrypt
import sys
import threading
import subprocess
import webbrowser

root = tk.Tk()
style = ttk.Style()

root.title("Epub Tool")
min_width = 500
min_height = 780
root.geometry(f"{min_width}x{min_height}")
root.minsize(min_width, min_height)
root.maxsize(min_height, min_height)
root.resizable(True, False)
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

style.configure(
    "Intro.TLabel",
    font=("TkDefaultFont", 14, "bold"),
    fg="#333",
    padding=10,
)
intro_label = ttk.Label(
    intro_frame,
    text="欢迎使用 Epub Tool\n此工具可帮助您处理电子书文件",
    style="Intro.TLabel",
    justify="center",
)
intro_label.pack(side=tk.TOP)


def open_link(event):
    webbrowser.open_new("https://github.com/cnwxi/epub_tool")


style.configure(
    "Link.TLabel",
    foreground="royalblue",
    # font=(default_font, 10, "underline"),
    font=("TkDefaultFont", 10, "underline"),
)
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
    for i, file_path in enumerate(files):
        file_name = os.path.basename(file_path)
        file_name = file_name.rsplit(".", 1)[0]
        file_list.insert(
            "",
            "end",
            values=(
                f" {i+1} ",
                f" {file_name} ",
                file_path,
            ),
        )


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
    # 获取所有选中的项（返回的是一个元组，包含项目的 ID）
    selected_items = file_list.selection()
    if not selected_items:
        messagebox.showwarning("Warning", "未选中任何文件")
        return
    # 从后往前删除选中的项目
    for item in reversed(selected_items):
        # 获取项目的值
        file = file_list.item(item, "values")[2]
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


add_files_btn = ttk.Button(
    add_frame,
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

delete_all_button = ttk.Button(
    add_frame,
    text="删除全部",
    command=delete_all,
)
delete_all_button.pack(side=tk.LEFT, padx=5)

# 创建一个 Frame 用于放置 Listbox 和 Scrollbar
listbox_frame = ttk.Frame(root)
listbox_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

file_list = ttk.Treeview(
    listbox_frame,
    selectmode="extended",
    columns=(
        "index",
        "file_name",
        "path",
    ),
    show="headings",
)
# file_list.config(borderwidth=2, relief="solid")
file_list.heading("index", text="序号", anchor="center")
file_list.column("index", width=int(min_width * 0.1), anchor="center", stretch=False)
file_list.heading("file_name", text="书名", anchor="center")
file_list.column("file_name", anchor="w", stretch=True)
file_list.heading("path", text="文件路径")
file_list.column("path", width=1, stretch=False)
file_list["displaycolumns"] = ("index", "file_name")
file_list.grid(row=1, column=0, sticky=tk.NSEW)


def show_context_menu(event):
    item = file_list.identify_row(event.y)
    if item:
        file_list.selection_set(item)
        context_menu.post(event.x_root, event.y_root)


def open_selected_file_dir():
    selected_items = file_list.selection()
    if not selected_items:
        messagebox.showwarning("Warning", "未选中任何文件")
        return
    for item in selected_items:
        file_path = file_list.item(item, "values")[2]
        file_path = os.path.dirname(file_path)
        if os.path.exists(file_path):
            try:
                if sys.platform.startswith("darwin"):  # macOS

                    subprocess.run(["open", file_path])
                elif os.name == "nt":  # Windows
                    os.startfile(file_path)
                elif os.name == "posix":  # Linux
                    subprocess.run(["xdg-open", file_path])
                else:
                    messagebox.showerror("Warning", "不支持的操作系统")
            except Exception as e:
                messagebox.showerror("Warning", f"无法打开路径: {e}")
        else:
            messagebox.showwarning("Warning", f"文件不存在: {file_path}")


context_menu = tk.Menu(file_list, tearoff=0)
context_menu.add_command(label="打开所在文件夹", command=open_selected_file_dir)
context_menu.add_command(label="删除选中文件", command=delete_selected)
file_list.bind("<Button-3>", show_context_menu)


class Tooltip:
    def __init__(self, widget):
        self.widget = widget
        self.tooltip_window = None  # 用于存储当前显示的 Tooltip 窗口
        self.widget.bind("<Motion>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

        # 获取默认字体（Treeview 使用的字体）
        self.font = Font(font="TkDefaultFont")

    def show_tooltip(self, event=None):
        # 首先隐藏任何已有的 Tooltip
        self.hide_tooltip()

        # 获取鼠标所在的行和列
        row_id = self.widget.identify_row(event.y)
        column = self.widget.identify_column(event.x)
        # print(f"row_id: {row_id}, column: {column}")

        if not row_id or not column:  # 如果没有找到行或列，直接返回
            return

        # 获取单元格内容
        try:
            # print(self.widget.item(row_id, 'values'))
            cell_value = self.widget.item(row_id, "values")[(int(column[1:]) - 1) * 2]
            # 获取列的宽度（单位：像素）
            # col_width = self.widget.column(column, "width")

            # 计算文字的实际宽度（单位：像素）
            # text_width = self.font.measure(cell_value)

            # 如果文字宽度超过列宽，显示 Tooltip
            # if text_width > col_width:
            # 如果不是第一列
            if column != "#1" and row_id != "" and cell_value != "":
                box = self.widget.bbox(row_id, column)  # 获取单元格位置
                if box is not None:
                    x, y, w, h = box[0], box[1], box[2], box[3]
                    if (
                        x + (w / 2) < 0 or y + (h / 2) < 0
                    ):  # bbox 返回无效值时（如不在可见区域），不显示 Tooltip
                        return

                    x += self.widget.winfo_rootx()  # 调整 Tooltip 的 X 坐标
                    y += self.widget.winfo_rooty() + h  # 调整 Tooltip 的 Y 坐标

                    # 创建 Tooltip 窗口
                    self.tooltip_window = tw = tk.Toplevel(self.widget)
                    tw.wm_overrideredirect(True)  # 去掉窗口边框
                    tw.wm_geometry(f"+{x}+{y}")  # 设置 Tooltip 的位置

                    label = tk.Label(
                        tw,
                        text=cell_value,
                        background="lightyellow",
                        relief="solid",
                        borderwidth=1,
                    )
                    label.pack()
        except IndexError:
            return

    def hide_tooltip(self, event=None):
        # 销毁 Tooltip 窗口
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


Tooltip(file_list)


# file_list.bind("<Motion>", on_treeview_motion)


# 创建垂直 Scrollbar
v_scrollbar = ttk.Scrollbar(
    listbox_frame,
    orient=tk.VERTICAL,
    command=file_list.yview,
    #    width=15
)
v_scrollbar.grid(row=1, column=1, sticky=tk.NS)


# 创建水平 Scrollbar
# h_scrollbar = ttk.Scrollbar(listbox_frame,
#                            orient=tk.HORIZONTAL,
#                            command=file_list.xview,
#                         #    width=15
#                            )
# h_scrollbar.grid(row=2, column=0, sticky=tk.EW)

# 将 Scrollbar 绑定到 Listbox
file_list.configure(
    yscrollcommand=v_scrollbar.set,
    #  xscrollcommand=h_scrollbar.set
)

# 配置 grid 行列权重
# listbox_frame.grid_rowconfigure(1, weight=1)
listbox_frame.grid_columnconfigure(0, weight=1)

# 添加分界线
separator = ttk.Separator(root, orient="horizontal")  # 创建水平分界线
separator.pack(fill="x", padx=5, pady=5)


def select_output_dir():
    global defalut_output_dir
    output_dir = os.path.normpath(filedialog.askdirectory(title="选择输出文件夹"))
    if output_dir != "." and os.path.exists(output_dir):
        defalut_output_dir = output_dir
        if len(output_dir) > 30:
            length = len(output_dir) - 15
            output_dir = output_dir[:15] + "..." + output_dir[length:]
        output_dir_label.config(text=f"输出路径: {output_dir}")
        style.configure(
            "FileLink.TLabel",
            font=("TkDefaultFont", 10, "underline"),
            foreground="royalblue",
        )
        output_dir_label.config(style="FileLink.TLabel", cursor="hand2")
        output_dir_label.update()
        # result_list.insert(
        # "",
        # "end",
        # values=(
        #     "^_^",
        #     "",
        #     "",
        #     "路径设置成功",
        #     f"设置输出路径成功: {output_dir}",
        # ),
        # )
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
    style.configure(
        "FileLink.TLabel", font=("TkDefaultFont", 10, "underline"), foreground="DimGray"
    )
    output_dir_label.config(style="FileLink.TLabel", cursor="")
    output_dir_label.update()
    result_list.insert(
        "",
        "end",
        values=(
            "^_^",
            "",
            "",
            "已重置路径",
            "重置输出路径为原文件所在目录",
        ),
    )
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

style.configure(
    "FileLink.TLabel", font=("TkDefaultFont", 10, "underline"), foreground="DimGray"
)
output_dir_label = ttk.Label(
    frame4,
    text="输出路径: 默认文件所在路径",
    style="FileLink.TLabel",
)
output_dir_label.pack(side=tk.LEFT, padx=5)
output_dir_label.bind("<Button-1>", open_output_dir)

# 添加分界线
separator = ttk.Separator(root, orient="horizontal")  # 创建水平分界线
separator.pack(fill="x", pady=5, padx=5)


def start_progress(func, func_name, output_dir, *args):
    # 创建一个新的线程来运行传入的函数
    thread = threading.Thread(
        target=lambda: run_in_thread(func, func_name, output_dir, *args)
    )
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
        file_path = file_list.item(item, "values")[2]
        file_list.delete(item)
        tmp_files_dic.pop(file_path)
        file_name = os.path.basename(file_path)
        file_name = file_name.rsplit(".", 1)[0]
        # 执行操作
        try:
            ret = func(file_path, output_dir, *args)
            if output_dir == None:
                tmp_output_dir = os.path.dirname(file_path)
            if ret == 0:
                emoji = "^_^"
                result = f" 成功 "
                info = f"{func_name}成功，输出路径：{tmp_output_dir}"
            elif ret == "skip":
                emoji = "O_o"
                result = f" 跳过 "
                info = f"文件已被{func_name}处理，跳过{func_name}操作"
            else:
                emoji = "T_T"
                result = f" 失败"
                info = f"{func_name}失败，错误信息：{ret}"
        except Exception as e:
            emoji = "@_@"
            result = f" 错误 "
            info = f"{func_name}错误，错误信息：{e}"

        # 显示处理结果
        result_list.insert(
            "",
            "end",
            values=(
                emoji,
                file_name,
                tmp_output_dir,
                result,
                info,
            ),
        )
        progress["value"] += 1
        root.update_idletasks()


op_frame = ttk.Frame(root)
op_frame.pack(padx=10, pady=5)
reformat_btn = ttk.Button(
    op_frame,
    text="批量格式化",
    command=lambda: start_progress(reformat_run, "格式化", defalut_output_dir),
)
reformat_btn.pack(side=tk.LEFT, padx=5)

decrypt_btn = ttk.Button(
    op_frame,
    text="批量文件名解密",
    command=lambda: start_progress(decrypt_run, "解密", defalut_output_dir),
)
decrypt_btn.pack(side=tk.LEFT, padx=5)

encrypt_btn = ttk.Button(
    op_frame,
    text="批量文件名加密",
    command=lambda: start_progress(encrypt_run, "加密", defalut_output_dir),
)
encrypt_btn.pack(side=tk.LEFT, padx=5)


def run_font_encrypt():
    file_count = len(file_list.get_children())
    if file_count == 0:
        messagebox.showwarning("Warning", "未添加任何文件")
        return
    if file_count > 1:
        messagebox.showwarning("Warning", "只能选择一个文件进行加密")
        return
    progress["value"] = 0
    progress["maximum"] = 2
    root.update_idletasks()
    item = file_list.get_children()[0]
    # 获取文件路径
    file_path = file_list.item(item, "values")[2]
    file_list.delete(item)
    tmp_files_dic.pop(file_path)
    fe = FontEncrypt(file_path, defalut_output_dir)
    fe.get_mapping()
    the_font_file_mapping = {}
    messagebox.showwarning(
        "Warning", f"此EPUB文件包含{len(fe.fonts)}个字体文件:\n{'\n'.join(fe.fonts)}"
    )
    sub_window = tk.Toplevel(root)
    sub_window.title("字体映射")
    sub_window.geometry("500x400")
    sub_window.minsize(500, 400)
    sub_label_frame = ttk.Frame(sub_window)
    sub_label_frame.pack(pady=10)
    sub_label = ttk.Label(
        sub_label_frame,
        text="请为每个字体文件选择对应的字体文件路径：\n（若已对内嵌字体进行过字体子集化，请不要跳过此流程）",
    )
    sub_label.pack(pady=10, padx=10)
    sub_label.config(font=("TkDefaultFont", 12, "bold"), justify="center")

    def select_font_file(font_file, parent_window, status_label):
        file_path = filedialog.askopenfilename(
            title=f"选择 {font_file} 对应字体文件",
            filetypes=[("字体文件", "*.ttf *.otf"), ("所有文件", "*.*")],
        )
        root.update_idletasks()
        if file_path:
            file_path = os.path.normpath(file_path)
            if os.path.exists(file_path):
                the_font_file_mapping[font_file] = file_path
                # print(f"已将 {font_file} 映射到 {file_path}")
                status_label.config(
                    text=f"已映射到 {os.path.basename(file_path)}"
                )  # 更新状态标签
            else:
                pass
        else:
            pass

    canvas = tk.Canvas(sub_window)
    scrollbar = ttk.Scrollbar(sub_window, orient="vertical", command=canvas.yview)
    scrollable_frame = ttk.Frame(canvas)

    # 配置 Canvas
    scrollable_frame.bind(
        "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    # 布局 Canvas 和 Scrollbar
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y",)
    # 绑定鼠标滚轮事件
    def on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", on_mousewheel)  # Windows 鼠标滚轮
    canvas.bind_all("<Button-4>", on_mousewheel)   # Linux 向上滚动
    canvas.bind_all("<Button-5>", on_mousewheel)   # Linux 向下滚动
    for i, font_file in enumerate(fe.fonts):
        if font_file in fe.font_to_char_mapping.keys():
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill="x", pady=5)

            label = tk.Label(frame, text=f"字体文件: {font_file}")
            label.pack(side="left", padx=5)

            status_label = tk.Label(frame, text="未映射")  # 初始化状态
            status_label.pack(side="right", padx=5)

            font_select_btn = ttk.Button(
                frame,
                text="选择字体文件",
                command=lambda f=font_file, s=status_label: select_font_file(
                    f, sub_window, s
                ),
            )
            font_select_btn.pack(side="left", padx=5)
    progress["value"] += 1
    root.update_idletasks()

    bottom_frame = ttk.Frame(scrollable_frame)
    # bottom_frame = ttk.Frame(sub_window)
    bottom_frame.pack(side="bottom", fill="x", pady=10)
    encrypt_btn = ttk.Button(
        bottom_frame,
        text="开始加密",
        command=lambda: start_font_encrypt(fe, the_font_file_mapping),
    )
    encrypt_btn.pack(fill='x',padx=5,pady=5)
    
    sub_window.protocol("WM_DELETE_WINDOW", lambda: sub_window.destroy())

    def start_font_encrypt(fe, font_mapping):
        # print(font_mapping)
        if len(font_mapping) != len(fe.fonts):
            messagebox.showwarning("Warning", "未指定所有字体文件")
            # progress["value"] = 0
            # root.update_idletasks()
            # return
        try:
            sub_window.destroy()
            fe.read_unchanged_fonts(font_mapping)
            fe.encrypt_font()
            fe.read_html()
            fe.close_file()
            # messagebox.showinfo("Success", "字体加密成功！")
            if defalut_output_dir == None:
                outdir = os.path.dirname(file_path)
            result_list.insert(
                "",
                "end",
                values=(
                    "^_^",
                    os.path.basename(file_path).rsplit(".", 1)[0],
                    outdir,
                    "成功",
                    f"字体加密成功，输出路径：{outdir}",
                ),
            )
        except Exception as e:
            fe.close_file()
            # messagebox.showerror("Error", f"字体加密失败: {e}")
        progress["value"] += 1
        root.update_idletasks()


font_encrypt_btn = ttk.Button(
    op_frame,
    text="字体加密（仅单文件）",
    command=run_font_encrypt,
)
font_encrypt_btn.pack(side=tk.LEFT, padx=5)

# 创建一个 Frame 用于放置进度条
progress_frame = ttk.Frame(root)
progress_frame.pack(fill=tk.X, padx=10, pady=0)

# 创建进度条
progress = ttk.Progressbar(
    progress_frame,
    orient=tk.HORIZONTAL,
    length=400,
    mode="determinate",
)
progress.pack(fill=tk.X, padx=5, pady=0)

# 创建一个 Frame 用于放置 Listbox 和 Scrollbar
result_box_frame = ttk.Frame(root)
result_box_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

result_list = ttk.Treeview(
    result_box_frame,
    columns=(
        "emoji",
        "file_name",
        "file_path",
        "result",
        "info",
    ),
    show="headings",
    #   height=10,
)
result_list.heading("emoji", text="状态", anchor="center")
result_list.column("emoji", width=int(min_width * 0.1), anchor="center", stretch=False)
result_list.heading("file_name", text="书名", anchor="center")
result_list.column("file_name", anchor="w", stretch=True)
result_list.column("file_path", width=0, stretch=False)
result_list.heading("result", text="执行结果", anchor="center")
result_list.column(
    "result", width=int(min_width * 0.15), anchor="center", stretch=False
)
result_list.column("info", width=0, stretch=False)
result_list["displaycolumns"] = ("emoji", "file_name", "result")
result_list.grid(row=1, column=0, sticky=tk.NSEW)


def show_context_menu_result(event):
    item = result_list.identify_row(event.y)
    if item:
        result_list.selection_set(item)
        context_menu_result.post(event.x_root, event.y_root)


def open_selected_file_output_dir():
    selected_items = result_list.selection()
    if not selected_items:
        messagebox.showwarning("Warning", "未选中任何文件")
        return
    for item in selected_items:
        file_path = result_list.item(item, "values")[2]
        # file_path = os.path.dirname(file_path)
        if os.path.exists(file_path):
            try:
                if sys.platform.startswith("darwin"):  # macOS

                    subprocess.run(["open", file_path])
                elif os.name == "nt":  # Windows
                    os.startfile(file_path)
                elif os.name == "posix":  # Linux
                    subprocess.run(["xdg-open", file_path])
                else:
                    messagebox.showerror("Warning", "不支持的操作系统")
            except Exception as e:
                messagebox.showerror("Warning", f"无法打开路径: {e}")
        else:
            messagebox.showwarning("Warning", f"文件不存在: {file_path}")


context_menu_result = tk.Menu(result_list, tearoff=0)
context_menu_result.add_command(
    label="打开输出文件夹", command=open_selected_file_output_dir
)

result_list.bind("<Button-3>", show_context_menu_result)


# 创建垂直 Scrollbar
v_scrollbar_result = ttk.Scrollbar(
    result_box_frame,
    orient=tk.VERTICAL,
    command=result_list.yview,
    #   width=10
)
v_scrollbar_result.grid(row=1, column=1, sticky=tk.NS)

# 创建水平 Scrollbar
# h_scrollbar_result = ttk.Scrollbar(result_box_frame,
#                                   orient=tk.HORIZONTAL,
#                                   command=result_list.xview,

#                                 #   width=15
#                                   )
# h_scrollbar_result.grid(row=2, column=0, sticky=tk.EW)

# 将 Scrollbar 绑定到 Listbox
result_list.config(yscrollcommand=v_scrollbar_result.set)

# 配置 grid 行列权重
# result_box_frame.grid_rowconfigure(1, weight=1)
result_box_frame.grid_columnconfigure(0, weight=1)


def adjust_column_width(event):
    # 获取窗口当前宽度
    new_width = file_list.winfo_width()

    # 设置列宽为窗口宽度的一部分（例如 80%）
    file_list.column(
        "index", width=int(min_width * 0.1), anchor="center", stretch=False
    )
    # file_list.column(
    #     "file_name", width=int(new_width * 0.84), anchor="center", stretch=True
    # )
    result_list.column(
        "emoji", width=int(min_width * 0.1), anchor="center", stretch=False
    )
    # result_list.column(
    #     "file_name", width=int(new_width * 0.7), anchor="center", stretch=True
    # )
    result_list.column(
        "result", width=int(min_width * 0.15), anchor="center", stretch=False
    )


# 绑定窗口大小变化事件
root.bind("<Configure>", adjust_column_width)
Tooltip(result_list)
root.mainloop()
