# XiaoA Core

[中文说明](README.md) | English manual

> [!WARNING]
> **The current AGY integration carries a risk of account suspension or termination.** Google's FAQ states that wrapping `agy` in third-party applications is not supported under consumer plans, and third-party access can result in account suspension or termination. Personal use, low usage, or keeping the project private does not guarantee account safety. This project does not promise uninterrupted service, unlimited quota, or protection from account restrictions. Read the [official FAQ](https://antigravity.google/docs/faq) before installation.

**Version 1.2.0 — experimental preview.** XiaoA Core is a self-hosted personal WeChat assistant for Windows. It connects messages to an external model through Antigravity CLI. It does not include or train a model and is not an official Google or WeChat product. A general-purpose API backend and a local model backend are not implemented yet.

## What it does

- Handles text, images, and files from the bound WeChat user.
- Merges nearby messages, preserves multiple images in arrival order, and serializes foreground tasks.
- Uses one model request for an ordinary multi-image turn; a requested web lookup may require a second request. It does not make one request per image.
- Archives ordinary conversations in local SQLite and selects a bounded amount of history for each prompt.
- Supports editable personal memories, temporary facts, document tools, a personal library, topic watching, and optional proactive chat.
- Keeps downloaded image originals for **8 days**. While the app runs, a background check runs approximately every **10 minutes** and removes expired generated image-cache files when foreground work is idle. Queued and active image tasks defer cleanup.
- Saves available image descriptions as explicitly labeled, model-generated text in conversation history. These descriptions can be recalled after the originals are deleted. They may be incomplete or inaccurate and are not automatically treated as user-confirmed personal facts.

If you need to inspect original visual details after deletion, send the image again. Images spaced too far apart can form separate turns. Cleanup does not run while the computer or application is off; overdue files are handled when the application resumes.

## Requirements

- Windows 10 or 11, x64. ARM64 has not been validated.
- 64-bit Python 3.11–3.13; the installer can prepare Python 3.13 through winget when Python is missing.
- Network access to Python dependencies, WeChat services, and Antigravity.
- Your own Google login, available model quota, and your own WeChat QR authorization, subject to the service restrictions above.

## Installation and startup

1. Download the repository using **Code → Download ZIP**, or use a prepared release archive. Extract the entire directory before running anything.
2. Double-click **`一键安装小A.cmd`** (Install XiaoA). The setup checks the file manifest and prepares an independent Python environment and dependencies. It installs the official AGY CLI if missing.
3. Complete Google authentication yourself if requested. Follow the installer instructions to return from the CLI login session.
4. Scan and approve the WeChat QR code using your own account.
5. Start the assistant using the desktop **XiaoA Core** shortcut. The running app has a tray icon.

The current installer and chat commands are primarily Chinese; this document is an English guide, not a fully translated user interface. First-time authentication cannot be performed automatically on another person's behalf.

Program files are installed under `%LOCALAPPDATA%\XiaoACore`; user data is stored under its `data` directory. Reinstallation preserves that data. Proactive chat starts disabled, with no creator-specific schedule or watch topics bundled.

## Everyday commands

Send the Chinese command exactly as shown. Replace example numbers and content with your own values.

| Command | Meaning |
|---|---|
| `助手帮助` | Show assistant features and limits |
| `记忆列表` / `记忆列表 2` | List stored personal memories, with pagination |
| `查看记忆 M12` | Inspect a memory and its source |
| `纠正记忆 M12：新内容` | Replace a memory with corrected content |
| `忘记记忆 M12` | Exclude that memory and its associated source turns from recall |
| `记住7天：内容` | Save a temporary fact for seven days |
| `回忆：关键词` | Search local archived history |
| `重置对话` | Begin a new context while retaining the local archive and memories |
| `关注：公开主题` | Add a public topic to watch |
| `开启主动聊天` / `关闭主动聊天` | Enable or disable proactive chat |

Proactive chat is bounded and pauses when the previous proactive message is unanswered. WeChat server-side delivery restrictions can still prevent a notification. Continuous topic monitoring and proactive analysis may consume model quota.

## Data and privacy

The distribution contains source, tests, scripts, and documentation. It excludes the creator's credentials, chat databases, memories, images, private persona, logs, runtime environments, and personal library.

Local storage does **not** mean fully offline operation: selected prompts, conversation context, memories, and images are sent to the external model; WeChat carries messages; search providers receive search terms. Third-party retention rules are independent of the local eight-day image policy.

Incoming images are stored in `data/inbound_images`; logs are in `data/logs`, search cache in `data/cache`, and chat and memory data in `data/assistant_data/memory`. Cleanup targets expired generated images there and legacy images directly in the data directory. It does not recursively delete backups, document-library files, or arbitrary user photos. When upgrading, the previous memory database and legacy files are retained as recovery copies; remove them manually if no longer needed. Text descriptions and chat archives remain. Forgetting a memory is not secure erasure of the database or old backups. AGY manages its own credentials and session data outside this project's image cache.

The repository's Git commit identity follows the publisher's GitHub account settings. File-content sanitization does not hide commit author emails. See [PRIVACY.md](PRIVACY.md) for the Chinese data policy.

## Stop and uninstall

- **`停止小A.cmd`**: stop the Core installation.
- **`打开数据目录.cmd`**: open its local data directory.
- **`卸载小A.cmd`**: uninstall; choose to keep data, delete data, or cancel. Windows Installed Apps also provides an uninstall entry.

Uninstall is restricted to a marked XiaoA Core installation. It preserves shared Python and AGY installations and does not sign you out of Google. It does not uninstall another XiaoA installation elsewhere on the computer.

## Troubleshooting

- **Only one of several images appears in the answer:** use version 1.2.0 or later and send the images close together with the question. Separate turns do not automatically reattach original images.
- **An image cannot be read:** resend it. Missing local files and failed downloads should produce an explicit notice rather than a fabricated visual answer.
- **Model errors or quota limits:** check the official AGY client and your plan. This project cannot bypass provider restrictions.
- **A fact is outdated:** request a current lookup; network failure or insufficient sources can still prevent verification.
- **Installation fails:** extract the full archive and confirm network access and a supported x64 Python. Manifest errors mean files are missing or changed; download a fresh copy or rebuild the manifest from reviewed source.

Do not post QR codes, account files, tokens, or private conversations in public issues. See [SECURITY.md](SECURITY.md).

## Development and verification

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test_uninstall.ps1
.\.venv\Scripts\python.exe tools\build_release.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -VerifyOnly
```

Tests use synthetic data and mocked network/model operations. The uninstall tests use temporary fixtures and mock process and registry changes. Passing these tests is not a guarantee of real-account authorization, visual accuracy, or compatibility with future service changes.

The builder only packages `release-files.json` entries, creates SHA-256 manifests, and places reproducible archives under `dist/`. Never publish a running data directory. See [ARCHITECTURE.md](ARCHITECTURE.md) for module boundaries.

## License and attribution

This distribution uses **AGPL-3.0**, with adapted upstream components retaining their notices. Proactive scheduling includes AGPL-3.0 upstream code; message deduplication includes MIT upstream code. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Source-code licensing does not override the terms of Google, WeChat, or any other service.
