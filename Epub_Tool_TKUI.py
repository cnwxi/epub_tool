import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import os
import threading
import queue
import time
import webbrowser
import subprocess
import re
import sys

# --- 引入 ttkbootstrap ---
import ttkbootstrap
from ttkbootstrap import Style
from ttkbootstrap.constants import *

# --- 尝试引入拖拽库 tkinterdnd2 ---
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    BaseClass = TkinterDnD.Tk
    DND_AVAILABLE = True
except ImportError:
    print("未检测到 tkinterdnd2，拖拽功能将禁用。请运行 pip install tkinterdnd2")
    BaseClass = tk.Tk
    DND_AVAILABLE = False

# 模拟导入功能模块
try:
    from utils.encrypt_epub import run as encrypt_run
    from utils.decrypt_epub import run as decrypt_run
    from utils.reformat_epub import run as reformat_run
    from utils.encrypt_font import (
        run_epub_font_encrypt,
        list_epub_font_encrypt_targets,
    )
    from utils.transfer_img import run_epub_img_transfer
except ImportError:

    def mock_run(filepath, outdir, *args):
        time.sleep(0.2)
        return 0

    def list_epub_font_encrypt_targets(epub_path):
        return {"font_families": []}

    encrypt_run = decrypt_run = reformat_run = run_epub_font_encrypt = (
        run_epub_img_transfer
    ) = mock_run


class FontEncryptSelectionDialog(simpledialog.Dialog):

    def __init__(self, parent, font_options):
        self.font_options = font_options
        self.result = None
        super().__init__(parent, title="字体加密筛选")

    def _toggle_selection(self, listbox):
        selected = set(listbox.curselection())
        listbox.select_clear(0, tk.END)
        for index in range(listbox.size()):
            if index not in selected:
                listbox.select_set(index)

    def _select_all(self, listbox):
        listbox.select_set(0, tk.END)

    def body(self, master):
        # simpledialog.Dialog 默认把 body frame 用 pack 且不扩展，这里改为可随窗口拉伸
        master.pack_configure(fill=tk.BOTH, expand=True)

        self.resizable(True, True)
        self.geometry("900x520")
        self.minsize(700, 420)

        master.grid_columnconfigure(0, weight=1)
        master.grid_columnconfigure(1, weight=0)
        master.grid_rowconfigure(1, weight=1)

        ttk.Label(
            master,
            text="请勾选要参与字体加密的字体（默认全选，支持 Shift+鼠标左键范围多选）",
            bootstyle="secondary",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        font_frame = ttk.Labelframe(master, text="字体 Family")
        font_frame.grid(row=1, column=0, sticky="nsew")
        font_frame.grid_rowconfigure(0, weight=1)
        font_frame.grid_columnconfigure(0, weight=1)

        self.font_listbox = tk.Listbox(
            font_frame, selectmode=tk.EXTENDED, exportselection=False, height=15
        )
        self.font_listbox.grid(row=0, column=0, sticky="nsew")
        font_scroll = ttk.Scrollbar(
            font_frame, orient=tk.VERTICAL, command=self.font_listbox.yview
        )
        font_scroll.grid(row=0, column=1, sticky="ns")
        font_scroll_x = ttk.Scrollbar(
            font_frame, orient=tk.HORIZONTAL, command=self.font_listbox.xview
        )
        font_scroll_x.grid(row=1, column=0, sticky="ew")
        self.font_listbox.config(
            yscrollcommand=font_scroll.set,
            xscrollcommand=font_scroll_x.set,
        )

        for item in self.font_options:
            self.font_listbox.insert(tk.END, item["label"])
        if self.font_options:
            self.font_listbox.select_set(0, tk.END)

        font_btns = ttk.Frame(font_frame)
        font_btns.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        btn_group = ttk.Frame(font_btns)
        btn_group.pack(anchor=tk.CENTER)
        ttk.Button(
            btn_group,
            text="全选",
            command=lambda: self._select_all(self.font_listbox),
            bootstyle="secondary-outline",
            width=8,
        ).pack(side=tk.LEFT)
        ttk.Button(
            btn_group,
            text="反选",
            command=lambda: self._toggle_selection(self.font_listbox),
            bootstyle="secondary-outline",
            width=8,
        ).pack(side=tk.LEFT, padx=(8, 0))

        return self.font_listbox

    def validate(self):
        selected_font_idx = self.font_listbox.curselection()
        if self.font_options and not selected_font_idx:
            messagebox.showwarning("提示", "请至少选择一个字体 family")
            return False
        return True

    def apply(self):
        self.result = {
            "selected_font_targets": [
                self.font_options[index] for index in self.font_listbox.curselection()
            ]
        }


class ModernEpubTool(BaseClass):

    def __init__(self):
        super().__init__()
        self.title("Epub Tool")
        self.geometry("980x700")

        # 手动应用主题
        self.style = Style(theme="flatly")

        # 窗口居中
        self.update_idletasks()
        width = 980
        height = 700

        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(width, height)
        self.file_map = {}
        self.output_dir = None
        self.msg_queue = queue.Queue()

        self.setup_ui()

        # 注册拖拽 (如果可用)
        if DND_AVAILABLE:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self.on_drop)

        self.after(100, self.process_queue)

    def setup_ui(self):
        # ================= 主布局 =================
        sidebar = ttk.Frame(self, bootstyle=SECONDARY, width=220)
        sidebar.pack(side=LEFT, fill=Y)
        sidebar.pack_propagate(False)

        main_content = ttk.Frame(self, padding=25)
        main_content.pack(side=RIGHT, fill=BOTH, expand=True)

        # ================= 侧边栏 =================
        title_lbl = ttk.Label(
            sidebar,
            text="EPUB TOOL",
            font=("TkDefaultFont", 16, "bold"),
            bootstyle="inverse-secondary",
        )
        title_lbl.pack(pady=(40, 30), anchor=CENTER)

        btn_frame = ttk.Frame(sidebar, bootstyle=SECONDARY)
        btn_frame.pack(fill=X, padx=20)

        self.create_sidebar_btn(btn_frame, "添加文件", self.add_files, style="light")
        self.create_sidebar_btn(btn_frame, "添加文件夹", self.add_dir, style="light")

        ttk.Separator(sidebar, bootstyle="light").pack(fill=X, padx=20, pady=15)
        self.create_sidebar_btn(btn_frame, "清空列表", self.clear_files, style="danger")

        if DND_AVAILABLE:
            drag_tip = ttk.Label(
                sidebar,
                text="使用说明\n·\n点击上侧按钮添加删除文件\n本程序已支持文件拖拽功能\n·\n点击右侧按钮进行批量处理\n·\n右键框内文件项目查看更多\n·",
                justify=CENTER,
                font=("TkDefaultFont", 10),
                bootstyle="inverse-secondary",
            )
            drag_tip.pack(pady=(0, 10))

        link_lbl = ttk.Label(
            sidebar,
            text="Github Repository",
            font=("TkDefaultFont", 9, "underline"),
            cursor="hand2",
            bootstyle="inverse-secondary",
        )
        link_lbl.pack(side=BOTTOM, pady=20)
        link_lbl.bind(
            "<Button-1>",
            lambda e: webbrowser.open("https://github.com/cnwxi/epub_tool"),
        )

        # ================= 主内容区 =================
        # 1. 文件列表
        list_label = ttk.Label(
            main_content,
            text="待处理文件",
            font=("TkDefaultFont", 12, "bold"),
            bootstyle="primary",
        )
        list_label.pack(anchor=W, pady=(0, 10))

        tree_frame = ttk.Frame(main_content)
        tree_frame.pack(fill=BOTH, expand=True, pady=(0, 20))

        columns = ("index", "name", "path")
        self.file_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", height=10, bootstyle="primary"
        )
        self.file_tree.heading("index", text="序号", anchor=CENTER)
        self.file_tree.column("index", width=80, anchor=CENTER, stretch=False)
        self.file_tree.heading("name", text="文件名", anchor=W)
        self.file_tree.column("name", width=200, anchor=W)
        self.file_tree.heading("path", text="完整路径", anchor=W)
        self.file_tree.column("path", anchor=W)

        tree_scroll = ttk.Scrollbar(
            tree_frame, orient=VERTICAL, command=self.file_tree.yview
        )
        self.file_tree.configure(yscrollcommand=tree_scroll.set)
        self.file_tree.pack(side=LEFT, fill=BOTH, expand=True)
        tree_scroll.pack(side=RIGHT, fill=Y)

        # 绑定右键菜单
        self.create_context_menus()

        if sys.platform.startswith("darwin"):
            self.file_tree.bind("<Button-2>", self.show_file_menu)

        else:
            self.file_tree.bind("<Button-3>", self.show_file_menu)

        # 2. 路径与操作
        ctrl_frame = ttk.Frame(main_content)
        ctrl_frame.pack(fill=X, pady=(0, 20))
        self.path_var = tk.StringVar(value="默认: 源文件同级目录")
        path_entry = ttk.Entry(
            ctrl_frame, textvariable=self.path_var, state="readonly", width=40
        )
        path_entry.pack(side=LEFT, padx=(0, 10), fill=X, expand=True)
        ttk.Button(
            ctrl_frame,
            text="设置输出路径",
            command=self.select_output,
            bootstyle="info-outline",
        ).pack(side=LEFT, padx=5)
        ttk.Button(
            ctrl_frame,
            text="重置路径",
            command=self.reset_output,
            bootstyle="secondary-outline",
        ).pack(side=LEFT)

        # 3. 功能按钮
        action_frame = ttk.Frame(main_content)
        action_frame.pack(fill=X, pady=(0, 20))
        actions = [
            ("格式化", reformat_run, "格式化", "primary"),
            ("文件解密", decrypt_run, "文件名解密", "success"),
            ("文件加密", encrypt_run, "文件名加密", "warning"),
            ("字体加密（不推荐）", run_epub_font_encrypt, "字体加密", "info"),
            ("图片转换", run_epub_img_transfer, "图片转换", "dark"),
        ]
        for idx, (text, func, name, b_style) in enumerate(actions):
            btn = ttk.Button(
                action_frame,
                text=text,
                command=lambda f=func, n=name: self.start_task(f, n),
                bootstyle=b_style,
                width=12,
            )
            btn.pack(side=LEFT, padx=(0 if idx == 0 else 10, 0), fill=X, expand=True)

        # 4. 进度条
        self.progress = ttk.Progressbar(
            main_content,
            bootstyle="success-striped",
            mode="determinate",
            orient=HORIZONTAL,
            length=100,
        )
        self.progress.pack(fill=X, pady=(0, 20))

        # 5. 日志区域 (增加 output_path 列，虽然不显示，但用于存储数据)
        log_labelframe = ttk.Labelframe(
            main_content, text="执行日志", padding=10, bootstyle="default"
        )
        log_labelframe.pack(fill=BOTH, expand=True)
        log_frame = ttk.Frame(log_labelframe)
        log_frame.pack(fill=BOTH, expand=True)

        # 注意：增加了 output_path 列
        self.log_tree = ttk.Treeview(
            log_frame,
            columns=("status", "file", "msg", "output_path"),
            show="headings",
            height=5,
            bootstyle="secondary",
        )
        self.log_tree.heading("status", text="状态")
        self.log_tree.column("status", width=80, anchor=CENTER, stretch=False)
        self.log_tree.heading("file", text="文件名", anchor=W)
        self.log_tree.column("file", width=200, anchor=W)
        self.log_tree.heading("msg", text="详情信息", anchor=W)
        self.log_tree.column("msg", stretch=True, anchor=W)
        # 隐藏 output_path 列
        self.log_tree.column("output_path", width=0, stretch=False)

        log_scroll = ttk.Scrollbar(
            log_frame, orient=VERTICAL, command=self.log_tree.yview
        )
        self.log_tree.configure(yscrollcommand=log_scroll.set)
        self.log_tree.pack(side=LEFT, fill=BOTH, expand=True)
        log_scroll.pack(side=RIGHT, fill=Y)

        self.log_tree.tag_configure("success", foreground="#198754")
        self.log_tree.tag_configure("error", foreground="#dc3545")
        self.log_tree.tag_configure("skip", foreground="#fd7e14")

        # 绑定日志右键
        if sys.platform.startswith("darwin"):
            self.log_tree.bind("<Button-2>", self.show_log_menu)
        else:
            self.log_tree.bind("<Button-3>", self.show_log_menu)

    # --- 右键菜单逻辑 ---
    def create_context_menus(self):
        # 文件列表菜单
        self.file_menu = tk.Menu(self, tearoff=0)
        self.file_menu.add_command(
            label="打开所在文件夹", command=self.open_file_location
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(label="移除此项", command=self.remove_selected_file)

        # 日志列表菜单
        self.log_menu = tk.Menu(self, tearoff=0)
        self.log_menu.add_command(label="打开输出位置", command=self.open_log_location)
        self.log_menu.add_separator()
        self.log_menu.add_command(
            label="打开日志文件(log.txt)", command=self.open_log_file
        )

    def show_file_menu(self, event):
        item = self.file_tree.identify_row(event.y)
        if item:
            self.file_tree.selection_set(item)
            self.file_menu.post(event.x_root, event.y_root)

    def show_log_menu(self, event):
        item = self.log_tree.identify_row(event.y)
        if item:
            self.log_tree.selection_set(item)
            self.log_menu.post(event.x_root, event.y_root)

    def open_file_location(self):
        selection = self.file_tree.selection()
        if not selection:
            return
        path = self.file_tree.item(selection[0], "values")[2]
        self._open_path(os.path.dirname(path))

    def remove_selected_file(self):
        selection = self.file_tree.selection()
        if not selection:
            return
        path = self.file_tree.item(selection[0], "values")[2]
        if path in self.file_map:
            del self.file_map[path]
        self.file_tree.delete(selection[0])
        # 重新编号
        for idx, item in enumerate(self.file_tree.get_children()):
            self.file_tree.set(item, "index", idx + 1)

    def open_log_location(self):
        selection = self.log_tree.selection()
        if not selection:
            return
        # 从隐藏的第4列(index 3)获取输出路径
        output_path = self.log_tree.item(selection[0], "values")[3]

        if output_path and os.path.exists(output_path):
            self._open_path(output_path)
        else:
            # 备选方案
            if self.output_dir and os.path.exists(self.output_dir):
                self._open_path(self.output_dir)
            else:
                messagebox.showwarning("提示", "无法找到有效的输出路径记录")

    def open_log_file(self):
        log_path = os.path.join(
            os.path.dirname(os.path.abspath(sys.argv[0])), "log.txt"
        )
        if os.path.exists(log_path):
            self._open_path(log_path)
        else:
            messagebox.showwarning("提示", f"未找到日志文件:\n{log_path}")

    def _open_path(self, path):
        """通用打开文件/文件夹方法"""
        try:
            if sys.platform.startswith("darwin"):  # macOS
                subprocess.run(["open", path])
            elif os.name == "nt":  # Windows
                os.startfile(path)
            elif os.name == "posix":  # Linux
                subprocess.run(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("错误", f"无法打开路径:\n{e}")

    # --- 拖拽逻辑 ---
    def on_drop(self, event):
        data = event.data
        files = self._parse_dnd_files(data)
        found_epubs = []
        for path in files:
            if os.path.isfile(path) and path.lower().endswith(".epub"):
                found_epubs.append(path)
            elif os.path.isdir(path):
                for root, _, filenames in os.walk(path):
                    for f in filenames:
                        if f.lower().endswith(".epub"):
                            found_epubs.append(os.path.join(root, f))
        if found_epubs:
            self._update_file_list(found_epubs)

    def _parse_dnd_files(self, data):
        if not data:
            return []
        pattern = r"\{.*?\}|\S+"
        matches = re.findall(pattern, data)
        cleaned_paths = []
        for match in matches:
            path = match.strip("{}")
            if os.path.exists(path):
                cleaned_paths.append(os.path.normpath(path))
        return cleaned_paths

    # --- 基础功能 ---
    def create_sidebar_btn(self, parent, text, command, style="primary"):
        btn = ttk.Button(parent, text=text, command=command, bootstyle=style)
        btn.pack(fill=X, pady=8, ipady=5)
        return btn

    def add_files(self):
        files = filedialog.askopenfilenames(
            title="选择EPUB文件", filetypes=[("EPUB Files", "*.epub *.EPUB")]
        )
        self._update_file_list(files)

    def add_dir(self):
        folder = filedialog.askdirectory()
        if folder:
            found = [
                os.path.join(r, f)
                for r, _, fs in os.walk(folder)
                for f in fs
                if f.lower().endswith(".epub")
            ]
            self._update_file_list(found)

    def _update_file_list(self, files):
        for f in files:
            norm = os.path.normpath(f)
            if norm not in self.file_map:
                self.file_map[norm] = True
                idx = len(self.file_tree.get_children()) + 1
                self.file_tree.insert(
                    "", "end", values=(idx, os.path.basename(norm), norm)
                )

    def clear_files(self):
        self.file_tree.delete(*self.file_tree.get_children())
        self.file_map.clear()

    def select_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_dir = os.path.normpath(path)
            self.path_var.set(f"输出路径: {path}")

    def reset_output(self):
        self.output_dir = None
        self.path_var.set("默认: 源文件同级目录")

    def _ask_font_encrypt_options(self, file_data):
        font_option_map = {}
        for one_file in file_data:
            try:
                result = list_epub_font_encrypt_targets(one_file)
                epub_path = os.path.normpath(one_file)
                epub_name = os.path.basename(epub_path)
                for font_family in result.get("font_families", []):
                    key = (epub_path.lower(), (font_family or "").lower())
                    if key in font_option_map:
                        continue
                    font_option_map[key] = {
                        "label": f"[{epub_name}] {font_family}",
                        "font_family": font_family,
                        "epub_path": epub_path,
                    }
            except Exception:
                continue

        dialog = FontEncryptSelectionDialog(
            self,
            sorted(font_option_map.values(), key=lambda item: item["label"].lower()),
        )
        if dialog.result is None:
            return None

        target_font_families_by_file = {}
        for item in dialog.result.get("selected_font_targets", []):
            epub_path = os.path.normpath(item.get("epub_path", ""))
            font_family = (item.get("font_family") or "").strip()
            if not epub_path or not font_family:
                continue
            target_font_families_by_file.setdefault(epub_path, set()).add(font_family)

        return {
            "target_font_families_by_file": {
                epub_path: sorted(fonts, key=str.lower)
                for epub_path, fonts in target_font_families_by_file.items()
            }
        }

    def start_task(self, func, task_name):
        items = self.file_tree.get_children()
        if not items:
            messagebox.showwarning("提示", "请先添加文件！")
            return

        file_data = [self.file_tree.item(i, "values")[2] for i in items]
        task_kwargs = {}
        if func == run_epub_font_encrypt:
            options = self._ask_font_encrypt_options(file_data)
            if options is None:
                return
            task_kwargs = options

        self.progress["value"] = 1
        self.progress["maximum"] = len(items) + 1
        self.file_tree.delete(*items)
        self.file_map.clear()

        threading.Thread(
            target=self._worker,
            args=(func, file_data, self.output_dir, task_kwargs),
            daemon=True,
        ).start()

    def _worker(self, func, files, out_dir, task_kwargs=None):
        for i, f_path in enumerate(files):
            f_name = os.path.basename(f_path)

            # 确定实际输出路径 (如果没有指定 out_dir，则默认为源文件目录)
            real_out_dir = out_dir if out_dir else os.path.dirname(f_path)

            try:
                if func == run_epub_font_encrypt and task_kwargs:
                    target_map = task_kwargs.get("target_font_families_by_file", {})
                    one_file_targets = target_map.get(os.path.normpath(f_path), [])
                    if not one_file_targets:
                        tag, status = ("skip", "跳过")
                        msg = "未为该EPUB选择目标字体，已跳过"
                        self.msg_queue.put((status, f_name, msg, real_out_dir, tag))
                        self.msg_queue.put("step")
                        continue
                    ret = func(f_path, out_dir, target_font_families=one_file_targets)
                elif task_kwargs:
                    ret = func(f_path, out_dir, **task_kwargs)
                else:
                    ret = func(f_path, out_dir)
                if ret == 0:
                    tag, status = ("success", "成功")
                elif ret == "skip":
                    tag, status = ("skip", "跳过")
                else:
                    tag, status = ("error", f"失败: {ret}")

                msg = f"输出至: {real_out_dir}"
            except Exception as e:
                tag, status, msg = ("error", "异常", str(e))

            # 传递 real_out_dir 到队列
            self.msg_queue.put((status, f_name, msg, real_out_dir, tag))
            self.msg_queue.put("step")

        self.msg_queue.put("done")

    def process_queue(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                if item == "step":
                    self.progress.step(1)
                elif item == "done":
                    self.progress["value"] = self.progress["maximum"]
                    messagebox.showinfo("完成", "所有任务处理完毕")
                else:
                    # 解析包含 output_path 的数据包
                    status, fname, info, out_path, tag = item
                    self.log_tree.insert(
                        "", 0, values=(status, fname, info, out_path), tags=(tag,)
                    )
                self.msg_queue.task_done()
        except queue.Empty:
            pass
        self.after(100, self.process_queue)


if __name__ == "__main__":
    app = ModernEpubTool()
    app.mainloop()
