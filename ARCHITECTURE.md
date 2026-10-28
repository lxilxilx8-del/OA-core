# Kernel structure / 内核结构

The host coordinates these modules; user data is stored separately from source.

| Module | Responsibility |
|---|---|
| `clawbot_service.py` | Bound-user transport, foreground routing, bounded AGY invocation and background coordination |
| `core_runtime.py` | Single-worker inbox, text/image debounce, ordered multi-image batches and atomic writes |
| `clawbot_plugins.py` | Public lookups and ordered image download/decryption |
| `image_cache.py` | Eight-day cache retention, ten-minute maintenance interval and bounded visual text metadata |
| `conversation_store.py` | SQLite archive, explicit memory provenance, forgetting and bounded retrieval |
| `conversation_engine.py` | Reply formatting, prompt history budget and memory response parsing |
| `message_dedup.py` | Message-ID deduplication scoped to account and sender |
| `assistant_hub.py` | File/library/watch/proactive orchestration |
| `weixin_delivery.py` | Delivery pacing and transport error handling |

## Message lifecycle

1. Verify the bound sender and filter repeated message IDs.
2. Download attachments, preserving all image paths and reporting failures.
3. Merge nearby fragments; image turns use a longer settling interval, within the existing maximum wait.
4. Execute one foreground task at a time. Ordinary multi-image chat uses one model call; a web lookup can require a second.
5. Strip internal metadata from the user-facing reply. Archive available visual notes as model-generated assistant text, not user-confirmed facts.
6. Mark message IDs complete after successful processing. Crash or uncertain transport outcomes still prevent an exactly-once guarantee.

## Storage and cleanup

Recent context is bounded; the SQLite archive is retained separately. Older user messages and explicitly labeled visual descriptions can be recalled. Ordinary historical assistant claims are excluded from search recall. Owner, epoch and forget filters still apply.

Cache maintenance acquires the foreground lock and defers while messages are queued or model work is active. It only removes matching, expired direct-child cache files; it does not follow links or walk into libraries and backups. The eight-day rule applies to local originals, not third-party service retention.

## Release boundary

The published repository contains a standalone, data-free core. `release-files.json` is the packaging boundary. Tests exercise the actual public host as well as queue, memory and retention modules. The creator's running configuration, credentials and private tools are not release inputs. User authentication is required after installation.
