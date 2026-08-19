import asyncio
import subprocess
from pathlib import Path
from typing import Protocol

import yaml

# 鍵名已對 graphrag 原始碼驗證:格式是 input.type(InputConfig.type,無 file_type 欄位;
# 儲存後端是另一個頂層區段 input_storage.type)。file_pattern 是 regex(TextFileReader
# 預設 r".*\.txt$"),不是 glob。text 對 txt+md(spec §2 白名單)。
_FILE_PATTERNS = {"text": r".*\.(txt|md)$", "csv": r".*\.csv$", "json": r".*\.json$"}
_ALLOWED = set(_FILE_PATTERNS)

# graphrag init 的 --model/--embedding 在 typer 宣告了 prompt=:stdin 非 TTY(subprocess
# /容器)時 click 讀到 EOF 會直接 Abort(已實測)。必須顯式傳值才能非互動執行;
# 值 = graphrag 3.1.0 的 graphrag.config.defaults 預設(gpt-4.1 / text-embedding-3-large)。
_INIT_MODEL = "gpt-4.1"
_INIT_EMBEDDING = "text-embedding-3-large"


class WorkspaceInitError(RuntimeError):
    """graphrag init 失敗。由 api 層轉成 HTTP — services 不得 import FastAPI。"""


class WorkspaceInitializer(Protocol):
    async def init(self, root: Path, input_file_type: str) -> None: ...


class GraphragInitInitializer:
    async def init(self, root: Path, input_file_type: str) -> None:
        if input_file_type not in _ALLOWED:
            msg = f"unsupported input_file_type: {input_file_type}"
            raise ValueError(msg)
        # subprocess.run 是阻塞的,直接寫在 async route 會卡住整個 event loop
        #(單副本部署 = 全服務凍結數秒)
        await asyncio.to_thread(self._run, root, input_file_type)

    def _run(self, root: Path, input_file_type: str) -> None:
        root.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["graphrag", "init", "--root", str(root),
                 "--model", _INIT_MODEL, "--embedding", _INIT_EMBEDDING],
                check=True, capture_output=True, timeout=120)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError) as e:   # FileNotFoundError = CLI 不在 PATH
            raise WorkspaceInitError(str(e)) from e
        settings_path = root / "settings.yaml"
        data = yaml.safe_load(settings_path.read_text())
        input_cfg = data.setdefault("input", {})
        input_cfg["type"] = input_file_type
        input_cfg["file_pattern"] = _FILE_PATTERNS[input_file_type]
        settings_path.write_text(yaml.safe_dump(data, sort_keys=False))
        # InputConfig 是 extra="allow":寫錯鍵名不會報錯而是靜默忽略。
        # 寫入後回讀斷言,防止未來 graphrag 版本改鍵名時靜默壞掉。
        check = yaml.safe_load(settings_path.read_text())
        if check.get("input", {}).get("type") != input_file_type:
            msg = f"settings.yaml input.type patch failed: {check.get('input')}"
            raise WorkspaceInitError(msg)


class FakeInitializer:
    """單元測試用:建目錄與最小 settings.yaml,不 fork CLI。"""

    async def init(self, root: Path, input_file_type: str) -> None:
        (root / "input").mkdir(parents=True, exist_ok=True)
        (root / "settings.yaml").write_text(yaml.safe_dump(
            {"input": {"type": input_file_type,
                       "file_pattern": _FILE_PATTERNS[input_file_type]}}))
