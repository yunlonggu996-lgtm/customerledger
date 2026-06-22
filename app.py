"""客户台账数据更新 - tkinter GUI 入口。

依赖：仅使用 Python 标准库（tkinter / sqlite3 / urllib / threading）。
启动：python3 app.py
"""

import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime

from api_client import ApiClient, ApiError
from config import UI_DISPLAY_COLUMNS, DEFAULT_PAGE_SIZE
from db import Database, PRIMARY_KEY


# 允许本地编辑的字段（白名单）
EDITABLE_FIELDS = {
    "组织简称",
    "组织名称",
    "客户成功",
    "RPA教练",
    "自定义标签",
    "最近跟进时间",
    "备注",  # 备注字段若远端不存在，本地新增；同步时保留
}


def _to_display(value):
    """将 API 返回值（多为 list/None）规范成可读文本。"""
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        if all(isinstance(x, str) for x in value):
            return ", ".join(value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (dict, int, float, bool)):
        return json.dumps(value, ensure_ascii=False) if not isinstance(value, bool) else str(value)
    return str(value)


def _to_storage(value):
    """将用户输入的字符串保存为合适的 Python 对象。"""
    v = value.strip()
    if not v:
        return []
    if "," in v:
        return [x.strip() for x in v.split(",") if x.strip()]
    return [v]


class CustomerLedgerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("客户台账数据更新")
        self.root.geometry("1280x780")
        self.root.minsize(960, 600)

        self.api = ApiClient()
        self.db = Database()
        self._stop_flag = False
        self._fetch_thread = None

        self._columns = self.db.get_columns() or UI_DISPLAY_COLUMNS
        self._display_columns = [c for c in UI_DISPLAY_COLUMNS if c in self._columns] \
            or [c for c in UI_DISPLAY_COLUMNS]
        self._all_records = []  # 当前列表
        self._current_records = []  # 过滤后

        self._build_ui()
        self._refresh_status()
        # 启动后异步加载本地数据
        self.root.after(100, self._load_local)

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        self._build_toolbar()
        self._build_table()
        self._build_detail_panel()
        self._build_statusbar()

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="关键字:").pack(side=tk.LEFT)
        self.var_keyword = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.var_keyword, width=28)
        ent.pack(side=tk.LEFT, padx=(4, 8))
        ent.bind("<Return>", lambda e: self._apply_search())

        ttk.Button(bar, text="搜索", command=self._apply_search).pack(side=tk.LEFT)
        ttk.Button(bar, text="清空", command=self._clear_search).pack(
            side=tk.LEFT, padx=(4, 12)
        )

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        self.btn_refresh = ttk.Button(
            bar, text="从接口拉取数据", command=self._on_refresh_click
        )
        self.btn_refresh.pack(side=tk.LEFT)
        ttk.Button(bar, text="停止", command=self._on_stop_click).pack(
            side=tk.LEFT, padx=(4, 12)
        )

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Button(bar, text="编辑选中行", command=self._edit_selected).pack(side=tk.LEFT)
        ttk.Button(bar, text="放弃本地修改", command=self._discard_local_edit).pack(
            side=tk.LEFT, padx=(4, 12)
        )

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        self.progress = ttk.Progressbar(bar, mode="determinate", length=180)
        self.progress.pack(side=tk.LEFT)

    def _build_table(self):
        container = ttk.Frame(self.root, padding=(8, 0))
        container.pack(fill=tk.BOTH, expand=True)

        cols = self._display_columns
        self.tree = ttk.Treeview(
            container, columns=cols, show="headings", height=18
        )
        for c in cols:
            self.tree.heading(c, text=c)
            # 根据列名粗略设宽度
            width = 140
            if c in ("组织名称", "组织简称"):
                width = 200
            elif c in ("RPA到期日期", "客户编号"):
                width = 160
            elif c in ("业务区域名称",):
                width = 120
            elif c in ("健康度", "RPA剩余天数"):
                width = 90
            self.tree.column(c, width=width, anchor=tk.W)

        vsb = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(container, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)
        self.tree.bind("<Double-1>", lambda e: self._edit_selected())

    def _build_detail_panel(self):
        outer = ttk.LabelFrame(self.root, text="详情（双击或选中后点击「编辑选中行」可修改）",
                                padding=(8, 4))
        outer.pack(side=tk.TOP, fill=tk.BOTH, expand=False, padx=8, pady=4)

        self.detail_text = tk.Text(outer, height=10, wrap=tk.WORD, font=("Menlo", 11))
        self.detail_text.configure(state=tk.DISABLED)
        self.detail_text.pack(fill=tk.BOTH, expand=True)

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="就绪")
        bar = ttk.Frame(self.root, relief=tk.SUNKEN, padding=(8, 3))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(bar, textvariable=self.status_var).pack(side=tk.LEFT)
        self.local_count_var = tk.StringVar(value="本地 0 条")
        ttk.Label(bar, textvariable=self.local_count_var).pack(side=tk.RIGHT, padx=8)
        self.last_sync_var = tk.StringVar(value="上次同步: --")
        ttk.Label(bar, textvariable=self.last_sync_var).pack(side=tk.RIGHT, padx=8)

    # ---------------- 数据加载 ----------------
    def _load_local(self):
        self._all_records = self.db.search(keyword="", limit=10000)
        self._current_records = list(self._all_records)
        self._render_table()
        self._refresh_status()
        if not self._all_records:
            self._set_status("本地无数据，点击「从接口拉取数据」开始首次同步。")
        else:
            self._set_status(f"已加载本地 {len(self._all_records)} 条数据。")

    def _render_table(self):
        self.tree.delete(*self.tree.get_children())
        for rec in self._current_records:
            values = [_to_display(rec.get(c)) for c in self._display_columns]
            local_fields = rec.get("__local_updated_fields__") or {}
            tags = ("edited",) if local_fields else ()
            self.tree.insert("", tk.END, iid=rec.get("__id__"), values=values, tags=tags)
        self.tree.tag_configure("edited", background="#FFF6D6")

    def _refresh_status(self):
        self.local_count_var.set(f"本地 {self.db.count()} 条")
        last = self.db.get_last_sync()
        self.last_sync_var.set(f"上次同步: {last or '--'}")

    def _set_status(self, msg):
        self.status_var.set(msg)

    # ---------------- 搜索 ----------------
    def _apply_search(self):
        kw = self.var_keyword.get().strip()
        # 关键字搜索：仅在内存中过滤
        if not kw:
            self._current_records = list(self._all_records)
        else:
            k = kw.lower()
            self._current_records = [
                r for r in self._all_records
                if any(k in _to_display(v).lower() for v in r.values())
            ]
        self._render_table()
        self._set_status(f"匹配 {len(self._current_records)} 条")

    def _clear_search(self):
        self.var_keyword.set("")
        self._current_records = list(self._all_records)
        self._render_table()
        self._set_status(f"显示全部 {len(self._current_records)} 条")

    # ---------------- 拉取 ----------------
    def _on_refresh_click(self):
        if self._fetch_thread and self._fetch_thread.is_alive():
            messagebox.showinfo("提示", "已有同步任务在执行中，请先停止。")
            return
        if not messagebox.askyesno("确认", "将从接口拉取并覆盖本地缓存的远端字段（保留本地编辑），是否继续？"):
            return
        self._stop_flag = False
        self.btn_refresh.configure(state=tk.DISABLED)
        self.progress.configure(value=0, maximum=100)

        def _progress(done, total):
            pct = int(done * 100 / max(total, 1))
            self.root.after(0, lambda: self.progress.configure(value=pct))
            self.root.after(0, lambda: self._set_status(f"拉取中 {done}/{total}"))

        def _stop():
            return self._stop_flag

        def _run():
            try:
                records, columns, total = self.api.fetch_all(
                    keyword="", page_size=DEFAULT_PAGE_SIZE,
                    progress_cb=_progress, stop_cb=_stop,
                )
                if self._stop_flag:
                    self.root.after(0, lambda: self._set_status("已中止。"))
                    return
                self.db.replace_all(records, columns)
                self._columns = columns
                self._display_columns = [c for c in UI_DISPLAY_COLUMNS if c in self._columns] \
                    or UI_DISPLAY_COLUMNS[:]
                self.root.after(0, self._after_fetch_done)
            except ApiError as e:
                self.root.after(0, lambda: messagebox.showerror("拉取失败", str(e)))
                self.root.after(0, lambda: self._set_status(f"拉取失败: {e}"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("异常", f"{type(e).__name__}: {e}"))
            finally:
                self.root.after(0, lambda: self.btn_refresh.configure(state=tk.NORMAL))

        self._fetch_thread = threading.Thread(target=_run, daemon=True)
        self._fetch_thread.start()

    def _on_stop_click(self):
        self._stop_flag = True
        self._set_status("正在中止…")

    def _after_fetch_done(self):
        self.progress.configure(value=100)
        self._refresh_status()
        self._load_local()

    # ---------------- 详情 / 编辑 ----------------
    def _on_select_row(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        rec_id = sel[0]
        rec = next((r for r in self._current_records if r.get("__id__") == rec_id), None)
        if not rec:
            rec = self.db.get(rec_id)
        if rec:
            self._show_detail(rec)

    def _show_detail(self, rec):
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        local_fields = rec.get("__local_updated_fields__") or {}
        keys = list(self._columns) if self._columns else list(rec.keys())
        # 把 __id__ 放最前
        keys = [k for k in keys if not k.startswith("__")] + ["__id__"]
        for k in keys:
            if k.startswith("__") and k != "__id__":
                continue
            label = k if k != "__id__" else "记录ID"
            v = rec.get(k, "")
            mark = " *" if (k in local_fields and k != "__local_updated_fields__") else ""
            line = f"{label}{mark}: {_to_display(v)}\n"
            self.detail_text.insert(tk.END, line)
        if local_fields:
            self.detail_text.insert(tk.END, "\n# * 表示该字段被本地编辑过\n")
        self.detail_text.configure(state=tk.DISABLED)

    def _edit_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在表格中选择一行。")
            return
        rec_id = sel[0]
        rec = self.db.get(rec_id) or next(
            (r for r in self._current_records if r.get("__id__") == rec_id), None
        )
        if not rec:
            messagebox.showerror("错误", "未找到对应记录。")
            return
        EditDialog(self.root, rec, self._columns, on_save=self._on_edit_saved)

    def _on_edit_saved(self, rec_id, field, new_value):
        try:
            self.db.update_field(rec_id, field, new_value)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        # 更新内存中记录
        for r in self._all_records:
            if r.get("__id__") == rec_id:
                r[field] = new_value
                edited = dict(r.get("__local_updated_fields__") or {})
                edited[field] = new_value
                r["__local_updated_fields__"] = edited
                break
        # 重新过滤 + 渲染
        self._apply_search() if self.var_keyword.get().strip() else self._render_table()
        # 刷新详情
        rec = self.db.get(rec_id)
        if rec:
            self._show_detail(rec)
        self._set_status(f"已保存 {rec_id} / {field}")

    def _discard_local_edit(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一行。")
            return
        rec_id = sel[0]
        rec = self.db.get(rec_id) or {}
        if not (rec.get("__local_updated_fields__") or {}):
            messagebox.showinfo("提示", "该记录没有本地修改。")
            return
        if not messagebox.askyesno("确认", "放弃该记录的所有本地编辑，恢复为远端值？"):
            return
        self.db.clear_local_edits(rec_id)
        self._load_local()
        rec = self.db.get(rec_id)
        if rec:
            self.tree.selection_set(rec_id)
            self._show_detail(rec)
        self._set_status(f"已放弃 {rec_id} 的本地修改。")


class EditDialog(tk.Toplevel):
    """编辑对话框：列出所有字段，可编辑白名单内字段。"""

    def __init__(self, parent, record, columns, on_save):
        super().__init__(parent)
        self.title(f"编辑记录 - {record.get('组织名称') or record.get('__id__', '')}")
        self.geometry("720x600")
        self.transient(parent)
        self.grab_set()

        self.record = dict(record)
        self.columns = columns or list(record.keys())
        self.on_save = on_save
        self._widgets = {}

        self._build()

    def _build(self):
        wrapper = ttk.Frame(self, padding=8)
        wrapper.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(wrapper, borderwidth=0, highlightthickness=0)
        scroll = ttk.Scrollbar(wrapper, orient=tk.VERTICAL, command=canvas.yview)
        self.body = ttk.Frame(canvas)
        self.body.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        local_fields = self.record.get("__local_updated_fields__") or {}
        keys = [k for k in self.columns if not k.startswith("__")] + ["__id__"]
        # 去重
        seen = set()
        ordered_keys = []
        for k in keys:
            if k in seen:
                continue
            seen.add(k)
            ordered_keys.append(k)

        for row, key in enumerate(ordered_keys):
            label_text = "记录ID" if key == "__id__" else key
            mark = "*" if key in local_fields else ""
            ttk.Label(self.body, text=f"{label_text}{mark}:").grid(
                row=row, column=0, sticky=tk.NW, padx=(0, 8), pady=2
            )
            editable = (key in EDITABLE_FIELDS)
            val = self.record.get(key, "")
            text = _to_display(val)
            ent = ttk.Entry(self.body, width=70)
            ent.insert(0, text)
            if not editable:
                ent.configure(state=tk.DISABLED, foreground="#888")
            ent.grid(row=row, column=1, sticky=tk.EW, pady=2)
            self._widgets[key] = ent
        self.body.columnconfigure(1, weight=1)

        # 底部按钮
        btns = ttk.Frame(self)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=8)
        ttk.Button(btns, text="保存", command=self._on_save).pack(side=tk.RIGHT)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=(0, 6))

    def _on_save(self):
        rec_id = self.record.get("__id__")
        for key, ent in self._widgets.items():
            if key not in EDITABLE_FIELDS or key == "__id__":
                continue
            new_text = ent.get()
            old_text = _to_display(self.record.get(key, ""))
            if new_text == old_text:
                continue
            new_value = _to_storage(new_text)
            try:
                self.on_save(rec_id, key, new_value)
            except Exception as e:
                messagebox.showerror("保存失败", str(e), parent=self)
                return
        self.destroy()


def main():
    root = tk.Tk()
    # 使用更现代的 ttk 主题（macOS 上默认 aqua 已经很好了）
    try:
        style = ttk.Style()
        if "aqua" not in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    app = CustomerLedgerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
