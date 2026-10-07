import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True, scope="session")
def isolated_store(tmp_path_factory):
    """Run every test against a throwaway copy of the synthetic database and a temp log dir,
    so tests never write approvals, advisories or logs into the demo store."""
    tmp = tmp_path_factory.mktemp("agricredit")
    src = ROOT / "data" / "agricredit.db"
    if not src.exists():
        pytest.exit("data/agricredit.db missing: run `make data train` first", returncode=2)
    shutil.copy(src, tmp / "agricredit.db")
    shutil.copytree(ROOT / "fs_mcp_root" / "policy", tmp / "fs_root" / "policy")
    mp = pytest.MonkeyPatch()
    mp.setenv("AGRICREDIT_FS_MCP_ROOT", str(tmp / "fs_root"))
    mp.setenv("AGRICREDIT_DB_PATH", str(tmp / "agricredit.db"))
    mp.setenv("AGRICREDIT_LOG_DIR", str(tmp / "logs"))
    mp.setenv("AGRICREDIT_LLM_BACKEND", "stub")
    yield tmp
    mp.undo()
