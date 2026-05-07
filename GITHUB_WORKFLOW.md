# BiliLive 直播助手 — 维护与发布指南

## 项目概况

GitHub 仓库：`fangkuaizhu/bili-live-summary`  
本地路径：`H:\菲伦的文件夹\bili-live-summary\`  
最新版本：v1.2.0（已发布）  
agentskill 包：`H:\菲伦的文件夹\bili-live-summary-v1.2.agentskill.zip`（31KB，14 个纯净文件）

---

## 一、发布新版本（完整流程）

### 1. 修改代码

在本地 `H:\菲伦的文件夹\bili-live-summary\` 中修改代码。

### 2. 更新版本号

编辑 `version.py`：
```python
__version__ = "1.3.0"
VERSION = "1.3.0"
RELEASE_DATE = "2026-05-xx"
```

### 3. 更新 CHANGELOG.md

在 `CHANGELOG.md` 顶部新增一条：
```markdown
## v1.3.0 — 2026-05-xx

### ✨ Features / 🐛 Bug Fixes

- 描述你的改动
```

### 4. 生成 agentskill 安装包

```bash
cd H:\菲伦的文件夹
python -c "
import zipfile, os
skill_dir = 'bili-live-summary'
with zipfile.ZipFile(f'bili-live-summary-v{VERSION}.agentskill.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', 'temp', 'output']]
        for f in files:
            if f.endswith('.pyc') or f == 'config.local.json':
                continue
            fp = os.path.join(root, f)
            z.write(fp, os.path.relpath(fp, skill_dir))
"
```

### 5. 打标签并推送

```bash
cd H:\菲伦的文件夹\bili-live-summary
git add -A
git commit -m "release: v1.3.0"
git tag -a v1.3 -m "v1.3.0 — 简短描述"
git push origin master
git push origin v1.3
```

### 6. 创建 GitHub Release

**方式 A：浏览器（最简单）**
1. 打开 https://github.com/fangkuaizhu/bili-live-summary/releases/new
2. 选刚推送的 tag（如 `v1.3`）
3. 填标题和更新说明
4. 把 `.agentskill.zip` 文件拖到 **Attach binaries** 区域
5. 点 **Publish release**

**方式 B：gh CLI（需先登录）**
```bash
# 首次登录（需要浏览器辅助）
export PATH="$PATH:/c/Program Files/GitHub CLI"
gh auth login

# 后续发布
gh release create v1.3 \
  --title "v1.3.0" \
  --notes "更新说明" \
  H:\菲伦的文件夹\bili-live-summary-v1.3.agentskill.zip
```

---

## 二、安装此 Skill

### 方式 A：从 GitHub 安装

在 Hanako/Claude 对话中：
```
install_skill github_url="https://github.com/fangkuaizhu/bili-live-summary"
```

### 方式 B：从本地 zip 安装

```
install_skill skill_content=$(cat H:\菲伦的文件夹\bili-live-summary-v1.2.agentskill.zip | base64)
```

### 方式 C：手动放置

将 zip 解压到 `.claude/skills/bili-live-summary/`：
```
~/.claude/skills/bili-live-summary/
├── SKILL.md
├── main.py
├── live_capture.py
├── transcriber.py
├── summarizer.py
├── ...（其余源码文件）
```

---

## 三、SKILL.md 元数据规范

本项目的 `SKILL.md` 使用以下 frontmatter：

```yaml
---
name: bili-live-summary
description: Bilibili直播/视频内容采集、转写与AI总结...
version: 1.2.0
source: https://github.com/fangkuaizhu/bili-live-summary
install: |
  git clone https://github.com/fangkuaizhu/bili-live-summary.git
  cd bili-live-summary
  pip install -r requirements.txt
  cp config.example.json config.local.json
  # 编辑 config.local.json 填入 API key
---
```

**SKILL.md 格式要点：**
| 字段 | 必需 | 说明 |
|------|------|------|
| `name` | ✅ | 技能名，用于 `install_skill` 和调用 |
| `description` | ✅ | 一句话描述，Agent 根据它决定何时调用 |
| `version` | ❌ | 建议加上 |
| `source` | ❌ | 源码来源，方便追溯 |
| `install` | ❌ | 安装指引，Agent 读取后执行 |

---

## 四、项目中敏感信息的处理

### 配置文件

- `config.local.json` 包含 API Key，已被 `.gitignore` 排除
- Git 仓库只提交 `config.example.json`（key 为空字符串的模板）

### 禁止提交到仓库的内容

| 路径 | 原因 |
|------|------|
| `config.local.json` | 含 API Key |
| `output/` | 测试音频/转写/总结产物 |
| `temp/` | 临时分段音频 |
| `__pycache__/` | 编译缓存 |
| `*.pyc` | 编译缓存 |

### 如果不慎提交了 API Key

1. **立即 revoke 该 key**（DeepSeek / MiniMax / OpenAI 后台操作）
2. 运行 `git filter-branch` 清理历史（参考本项目的 v1.2 修复过程）
3. `git push --force` 覆盖远程

---

## 五、仓库结构说明

```
H:\菲伦的文件夹\bili-live-summary\
├── .gitignore           # 排除敏感文件
├── .github/workflows/   # （可选）CI/CD 工作流
├── CHANGELOG.md         # 版本变更记录
├── HANDOVER.md          # 维护手册（详细技术说明）
├── README.md            # 项目简介
├── SKILL.md             # 技能入口（agentskill 元数据 + 使用说明）
├── version.py           # 版本号
├── config.example.json  # 配置模板（不含 Key）
├── config.py            # 配置读取
├── main.py              # CLI 入口
├── live_capture.py      # 音频采集
├── transcriber.py       # 转写
├── summarizer.py        # 总结
├── danmaku.py           # 弹幕采集
└── requirements.txt     # Python 依赖
```

---

## 六、常见维护任务

### 修改场景提示词

编辑 `config.py` 中的 `SCENE_PROMPTS` 字典。

### 更换 Whisper 模型

编辑 `config.py`：
```python
WHISPER_MODEL = "large-v3-turbo"  # 可选: tiny/base/small/medium/large-v3
```

### 更换总结 API

编辑 `config.local.json` 中的 `api.platform` 字段（可选值：`deepseek` / `minimax` / `openai`）。

### 直播流获取失败

检查 B站官方 API 是否变更：
```
https://api.live.bilibili.com/room/v1/Room/playUrl?cid={room_id}&platform=web&qn=80
```
如果 API 地址变动，更新 `live_capture.py` 中的 `get_fresh_stream_url` 函数。

---

## 七、Skill 的调试与测试

```bash
# 快速查看直播间
python main.py --url https://live.bilibili.com/xxx --check

# 手动指定时长（秒）
python main.py --url https://live.bilibili.com/xxx --duration 300 --scene general

# 跟播到结束（Ctrl+C 手动停止）
python main.py --url https://live.bilibili.com/xxx --until-end --scene streamer

# 处理视频
python main.py --video https://www.bilibili.com/video/BVxxx

# 处理本地音频
python main.py --audio recording.wav
```

输出始终保存在 `output/{标题}_{上传者}/{时间戳}_{时长}/` 目录中。
