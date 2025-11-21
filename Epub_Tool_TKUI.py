import tkinter as tk
from tkinter import filedialog, messagebox, ttk
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
    from utils.encrypt_font import run_epub_font_encrypt
    from utils.transfer_img import run_epub_img_transfer
except ImportError:

    def mock_run(filepath, outdir, *args):
        time.sleep(0.2)
        return 0

    encrypt_run = decrypt_run = reformat_run = run_epub_font_encrypt = (
        run_epub_img_transfer
    ) = mock_run


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
            font=("Helvetica", 16, "bold"),
            bootstyle="inverse-secondary",
        )
        title_lbl.pack(pady=(40, 30), anchor=CENTER)

        btn_frame = ttk.Frame(sidebar, bootstyle=SECONDARY)
        btn_frame.pack(fill=X, padx=20)

        self.create_sidebar_btn(btn_frame, "📂 添加文件", self.add_files, style="light")
        self.create_sidebar_btn(btn_frame, "📁 添加文件夹", self.add_dir, style="light")

        ttk.Separator(sidebar, bootstyle="light").pack(fill=X, padx=20, pady=15)
        self.create_sidebar_btn(
            btn_frame, "🗑️ 清空列表", self.clear_files, style="danger"
        )

        if DND_AVAILABLE:
            drag_tip = ttk.Label(
                sidebar,
                text="使用说明\n·\n点击上侧按钮添加删除文件\n本程序已支持文件拖拽功能\n·\n点击右侧按钮进行批量处理\n·\n右键框内文件项目查看更多",
                justify=CENTER,
                # font=("Helvetica", 9),
                bootstyle="inverse-secondary",
            )
            drag_tip.pack(pady=(0, 10))

        link_lbl = ttk.Label(
            sidebar,
            text="Github Repository",
            font=("Helvetica", 9, "underline"),
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
            font=("Helvetica", 12, "bold"),
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
            text="⚙️ 设置输出",
            command=self.select_output,
            bootstyle="info-outline",
        ).pack(side=LEFT, padx=5)
        ttk.Button(
            ctrl_frame,
            text="↺ 重置",
            command=self.reset_output,
            bootstyle="secondary-outline",
        ).pack(side=LEFT)

        # 3. 功能按钮
        action_frame = ttk.Frame(main_content)
        action_frame.pack(fill=X, pady=(0, 20))
        actions = [
            ("📖 格式化", reformat_run, "格式化", "primary"),
            ("🔓 文件解密", decrypt_run, "文件名解密", "success"),
            ("🔒 文件加密", encrypt_run, "文件名加密", "warning"),
            ("🅰️ 字体加密", run_epub_font_encrypt, "字体加密", "info"),
            ("🖼️ 图片转换", run_epub_img_transfer, "图片转换", "dark"),
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
        self.log_tree.bind("<Button-3>", self.show_log_menu)

    # --- 右键菜单逻辑 ---
    def create_context_menus(self):
        # 文件列表菜单
        self.file_menu = tk.Menu(self, tearoff=0)
        self.file_menu.add_command(
            label="📂 打开所在文件夹", command=self.open_file_location
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(
            label="🗑️ 移除此项", command=self.remove_selected_file
        )

        # 日志列表菜单
        self.log_menu = tk.Menu(self, tearoff=0)
        self.log_menu.add_command(
            label="📂 打开输出位置", command=self.open_log_location
        )
        self.log_menu.add_separator()
        self.log_menu.add_command(
            label="📝 打开日志文件(log.txt)", command=self.open_log_file
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

    def start_task(self, func, task_name):
        items = self.file_tree.get_children()
        if not items:
            messagebox.showwarning("提示", "请先添加文件！")
            return

        self.progress["value"] = 1
        self.progress["maximum"] = len(items) + 1

        file_data = [self.file_tree.item(i, "values")[2] for i in items]
        self.file_tree.delete(*items)
        self.file_map.clear()

        threading.Thread(
            target=self._worker, args=(func, file_data, self.output_dir), daemon=True
        ).start()

    def _worker(self, func, files, out_dir):
        for i, f_path in enumerate(files):
            f_name = os.path.basename(f_path)

            # 确定实际输出路径 (如果没有指定 out_dir，则默认为源文件目录)
            real_out_dir = out_dir if out_dir else os.path.dirname(f_path)

            try:
                ret = func(f_path, out_dir)
                if ret == 0:
                    tag, status = ("success", "✅ 成功")
                elif ret == "skip":
                    tag, status = ("skip", "⏭️ 跳过")
                else:
                    tag, status = ("error", f"❌ 失败: {ret}")

                msg = f"输出至: {real_out_dir}"
            except Exception as e:
                tag, status, msg = ("error", "⚠️ 异常", str(e))

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
