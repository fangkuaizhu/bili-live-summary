"""
bili-live-summary 全自动发布脚本

用法：
  1. 首次：cp .env.example .env  # 填入 GH_TOKEN
  2. 发布：python release.py v1.3 "更新说明"

流程：
  - 更新 version.py
  - 生成 agentskill zip
  - git commit + tag
  - 推送 tag（触发 release）
  - 上传 zip 到 release
"""

import os
import re
import sys
import subprocess
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
ZIP_NAME = "bili-live-summary-v{version}.agentskill.zip"

# ── 读取 token ──
def load_token():
    env_path = HERE / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("GH_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("GH_TOKEN", "")


# ── 生成 agentskill zip ──
def build_zip(version: str) -> Path:
    out = HERE.parent / ZIP_NAME.format(version=version)
    skip_dirs = {".git", "__pycache__", "temp", "output"}
    skip_files = {"config.local.json"}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(HERE):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f.endswith(".pyc") or f in skip_files:
                    continue
                fp = os.path.join(root, f)
                z.write(fp, os.path.relpath(fp, HERE))
    print(f"[zip] {out.name} ({out.stat().st_size / 1024:.0f} KB)")
    return out


# ── 更新 version.py ──
def update_version(version: str):
    vf = HERE / "version.py"
    content = vf.read_text(encoding="utf-8")
    content = re.sub(r'__version__ = ".*"', f'__version__ = "{version}"', content)
    content = re.sub(r'VERSION = ".*"', f'VERSION = "{version}"', content)
    content = re.sub(r'RELEASE_DATE = ".*"', f'RELEASE_DATE = "{__import__("datetime").date.today()}"', content)
    vf.write_text(content, encoding="utf-8")
    print(f"[ver] version.py -> {version}")


# ── 主流程 ──
def main():
    if len(sys.argv) < 2:
        print("用法: python release.py v1.3 [更新说明]")
        sys.exit(1)

    version = sys.argv[1].lstrip("v")
    tag = f"v{version}"
    notes = sys.argv[2] if len(sys.argv) > 2 else f"release: v{version}"
    token = load_token()

    # 1. 更新版本号
    update_version(version)

    # 2. 生成 zip
    zip_path = build_zip(version)

    # 3. git commit + tag
    subprocess.run(["git", "add", "-A"], cwd=HERE, check=True)
    subprocess.run(["git", "commit", "-m", f"release: v{version}"], cwd=HERE, check=True)
    subprocess.run(["git", "tag", "-a", tag, "-m", notes], cwd=HERE, check=True)

    # 4. 推送
    subprocess.run(["git", "push", "origin", "master"], cwd=HERE, check=True)
    subprocess.run(["git", "push", "origin", tag], cwd=HERE, check=True)

    # 5. 创建 release + 上传 zip
    gh = "C:/Program Files/GitHub CLI/gh.exe"
    env = os.environ.copy()
    env["GH_TOKEN"] = token

    subprocess.run(
        [gh, "release", "create", tag, "--title", f"v{version}", "--notes", notes, str(zip_path)],
        cwd=HERE, env=env, check=True,
    )

    print(f"\n✅ v{version} 发布完成！")
    print(f"   https://github.com/fangkuaizhu/bili-live-summary/releases/tag/{tag}")


if __name__ == "__main__":
    main()
