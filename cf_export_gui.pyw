"""Local GUI exporter for an account owner's own Codeforces submissions.

API credentials stay in this process and are sent only to Codeforces over HTTPS.
The handle must belong to the API key owner. This app does not bypass API limits.
"""

from __future__ import annotations

import json
import base64
import hashlib
import queue
import secrets
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API_BASE = "https://codeforces.com/api/"
PAGE_SIZE = 1000
MIN_INTERVAL_SECONDS = 2.1


def call_api(method: str, params: dict[str, str], api_key: str, api_secret: str) -> object:
    signed_params = {**params, "apiKey": api_key, "time": str(int(time.time()))}
    ordered = sorted(signed_params.items(), key=lambda pair: (pair[0], pair[1]))
    canonical = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in ordered)
    rand = secrets.token_hex(3)
    digest = hashlib.sha512(f"{rand}/{method}?{canonical}#{api_secret}".encode("utf-8")).hexdigest()
    signed_params["apiSig"] = rand + digest

    url = API_BASE + method + "?" + urlencode(signed_params)
    request = Request(url, headers={"User-Agent": "cf-own-submissions-export/1.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Codeforces API request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Codeforces API returned invalid JSON") from exc

    if payload.get("status") != "OK":
        raise RuntimeError(f"Codeforces API error: {payload.get('comment', 'unknown error')}")
    return payload["result"]


class ExportApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codeforces 提交导出")
        self.root.geometry("650x430")
        self.root.minsize(600, 400)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None

        self.handle_var = tk.StringVar()
        self.key_var = tk.StringVar()
        self.secret_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(Path.cwd() / "codeforces_submissions.jsonl"))
        self.accepted_only_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="填写账号所有者的 API 信息，然后开始导出。")

        self._build()
        self.root.after(150, self._poll_events)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Codeforces 提交导出", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        ttk.Label(
            frame,
            text="仅导出 API 密钥所属账号的提交。密钥只在本机内存中使用，不会写入导出文件。",
            wraplength=600,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))

        ttk.Label(frame, text="账号 Handle").grid(row=2, column=0, sticky="w", pady=5)
        self.handle_entry = ttk.Entry(frame, textvariable=self.handle_var)
        self.handle_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(frame, text="API Key").grid(row=3, column=0, sticky="w", pady=5)
        self.key_entry = ttk.Entry(frame, textvariable=self.key_var, show="•")
        self.key_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(frame, text="API Secret").grid(row=4, column=0, sticky="w", pady=5)
        self.secret_entry = ttk.Entry(frame, textvariable=self.secret_var, show="•")
        self.secret_entry.grid(row=4, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(frame, text="导出文件").grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.output_var).grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Button(frame, text="选择…", command=self._choose_output).grid(row=5, column=2, padx=(8, 0), pady=5)

        ttk.Checkbutton(
            frame,
            text="只导出 Accepted 提交（推荐）",
            variable=self.accepted_only_var,
        ).grid(row=6, column=1, columnspan=2, sticky="w", pady=(8, 12))

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=1, columnspan=2, sticky="w")
        self.start_button = ttk.Button(buttons, text="开始导出", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="取消", command=self.cancel_event.set, state="disabled")
        self.cancel_button.pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(18, 8))
        ttk.Label(frame, textvariable=self.status_var, wraplength=600).grid(
            row=9, column=0, columnspan=3, sticky="w"
        )

        note = (
            "API Key 和 Secret 由账号所有者在 codeforces.com/settings/api 创建。"
            "官方接口每 2 秒最多请求 1 次；题目正文不包含在提交接口中。"
        )
        ttk.Label(frame, text=note, foreground="#555555", wraplength=600).grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(18, 0)
        )

    def _choose_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存导出数据",
            defaultextension=".jsonl",
            filetypes=[("JSON Lines", "*.jsonl"), ("All files", "*.*")],
            initialfile="codeforces_submissions.jsonl",
        )
        if path:
            self.output_var.set(path)

    def _start(self) -> None:
        handle = self.handle_var.get().strip()
        api_key = self.key_var.get().strip()
        api_secret = self.secret_var.get().strip()
        output = self.output_var.get().strip()
        if not all((handle, api_key, api_secret, output)):
            messagebox.showerror("信息不完整", "请填写 Handle、API Key、API Secret，并选择导出文件。")
            return
        if not messagebox.askyesno(
            "确认导出",
            "请确认这个 Handle 属于 API Key 所有者。\n\n导出的 JSONL 将包含提交源码，请妥善保管。",
        ):
            return

        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.start(12)
        self.status_var.set("正在连接 Codeforces API…")
        self.worker = threading.Thread(
            target=self._export_worker,
            args=(handle, api_key, api_secret, Path(output), self.accepted_only_var.get()),
            daemon=True,
        )
        self.worker.start()

    def _export_worker(
        self, handle: str, api_key: str, api_secret: str, output: Path, accepted_only: bool
    ) -> None:
        exported = 0
        scanned = 0
        without_source = 0
        start_index = 1
        last_request_at: float | None = None
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("w", encoding="utf-8") as out:
                while not self.cancel_event.is_set():
                    if last_request_at is not None:
                        delay = MIN_INTERVAL_SECONDS - (time.monotonic() - last_request_at)
                        if delay > 0 and self.cancel_event.wait(delay):
                            break

                    last_request_at = time.monotonic()
                    batch = call_api(
                        "user.status",
                        {
                            "handle": handle,
                            "from": str(start_index),
                            "count": str(PAGE_SIZE),
                            "includeSources": "true",
                        },
                        api_key,
                        api_secret,
                    )
                    if not isinstance(batch, list) or not batch:
                        break

                    retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    for submission in batch:
                        scanned += 1
                        if accepted_only and submission.get("verdict") != "OK":
                            continue
                        encoded_source = submission.pop("sourceBase64", None)
                        if not submission.get("source") and encoded_source:
                            try:
                                submission["source"] = base64.b64decode(encoded_source, validate=True).decode(
                                    "utf-8", errors="replace"
                                )
                            except (ValueError, TypeError):
                                submission["source"] = ""
                        if not submission.get("source"):
                            without_source += 1
                        record = {
                            "handle": handle,
                            "retrievedAt": retrieved_at,
                            "submission": submission,
                        }
                        out.write(json.dumps(record, ensure_ascii=False) + "\n")
                        exported += 1

                    out.flush()
                    start_index += len(batch)
                    self.events.put(("progress", (scanned, exported)))
                    if len(batch) < PAGE_SIZE:
                        break

            self.events.put(("done", (exported, scanned, without_source, str(output), self.cancel_event.is_set())))
        except Exception as exc:  # surface API and local file errors in the UI
            self.events.put(("error", str(exc)))
        finally:
            # Drop local references as soon as the export ends.
            api_key = ""
            api_secret = ""

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    scanned, exported = payload  # type: ignore[misc]
                    self.status_var.set(f"已扫描 {scanned} 条，导出 {exported} 条…")
                elif kind == "done":
                    exported, scanned, without_source, output, cancelled = payload  # type: ignore[misc]
                    self._finish()
                    prefix = "已取消。" if cancelled else "导出完成。"
                    message = f"{prefix}\n扫描 {scanned} 条，写入 {exported} 条。\n文件：{output}"
                    if without_source:
                        message += f"\n其中 {without_source} 条未返回源码。"
                    self.status_var.set(message)
                    if not cancelled:
                        messagebox.showinfo("导出结果", message)
                elif kind == "error":
                    self._finish()
                    self.status_var.set("导出失败。")
                    messagebox.showerror("导出失败", str(payload))
        except queue.Empty:
            pass
        self.root.after(150, self._poll_events)

    def _finish(self) -> None:
        self.progress.stop()
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.key_var.set("")
        self.secret_var.set("")


def main() -> None:
    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        ExportApp(root)
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(800, lambda: root.attributes("-topmost", False))
        root.mainloop()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        try:
            messagebox.showerror("Codeforces 导出工具启动失败", detail, parent=root)
        except Exception:
            pass


if __name__ == "__main__":
    main()
