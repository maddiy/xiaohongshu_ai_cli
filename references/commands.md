# Command Reference

Full CLI reference for `main.py`. All commands are run from the project root.

## `scan` — Scan for unreplied comments

```bash
# Notification mode (default, fast) — reads latest 20 notifications
python3 main.py scan
python3 main.py scan --note-id <note_id>
python3 main.py scan --note-id <note_id> --num-notifications 100

# Full scan mode — pulls ALL comments from a note
python3 main.py scan --note-id <note_id> --full-scan
python3 main.py scan --note-id <note_id> --full-scan --refresh       # ignore cache
python3 main.py scan --note-id <note_id> --full-scan --with-subs     # include sub-comments
python3 main.py scan --note-id <note_id> --full-scan --xsec-token <t>

# Common options
--note-id <id>          Specify a single note
--xsec-token <t>        Required for some notes
-n, --limit <N>         Max comments to pull (full scan mode)
```

## `drafts` — Generate reply drafts

```bash
python3 main.py drafts --note-id <note_id>
python3 main.py drafts --note-id <note_id> --from-scan scan.json --batch reply_map.json --output drafts.json
```

Without `--batch`, opens an interactive prompt for reviewing and entering drafts.

## `send` — Send replies

```bash
python3 main.py send --file drafts.json --dry-run
python3 main.py send --file drafts.json --confirm
```

- `--dry-run` — preview without sending
- `--confirm` — show all pending replies and ask for confirmation

## `analyze` — Analyze comment sentiment

```bash
python3 main.py analyze --note-id <note_id>
python3 main.py analyze --note-id <note_id> --xsec-token <token>
python3 main.py analyze --note-id <note_id> --refresh   # force refresh
```

Outputs sentiment classification, top comments by likes, active users, etc.

## `post` — Publish a new note

```bash
python3 main.py post \
    --title "标题" \
    --body "正文内容" \
    --images /path/to/img1.jpg /path/to/img2.jpg \
    --topics "标签1,标签2" \
    --private   # optional, for draft mode
```

AI-friendly JSON input:

```bash
python3 main.py post --input note.json --dry-run
python3 main.py post --input note.json
```

---

## Python API Quick Reference

### XHSClient (lib/xhs_client.py)

```python
from lib.xhs_client import XHSClient

# Notifications
XHSClient.get_new_comment_notifications(num=20)  # → [{note_id, note_title, new_comments}]

# Comments
XHSClient.get_comments_cached(note_id, xsec_token="", force_refresh=False)  # → (comments, from_cache)

# Reply
XHSClient.reply(note_id, comment_id, content)  # → (ok: bool, error: str, err_type: str)

# Skip list
XHSClient.get_skipped_ids()          # → set of skipped comment IDs
XHSClient.add_skipped_ids([id, ...]) # add to skip list

# Utilities
XHSClient.get_note_id_from_url("https://www.xiaohongshu.com/explore/<id>")
XHSClient.get_note_info(note_id)     # → dict with title, xsec_token
```

### CommentScanner (lib/scanner.py)

```python
from lib.scanner import CommentScanner

scanner = CommentScanner()

# Notification-based scan
result = scanner.scan_via_notifications(note_id=None, verbose=True, num_notifications=20)
# → {note_id, per_note, unreplied_level1, unreplied_subs}

# Full scan
result = scanner.scan_note(note_id, xsec_token="", force_refresh=False, with_subs=False)
# → {note_id, xsec_token, unreplied_level1, unreplied_subs, source: "full_scan"}
```

### Replier (lib/replier.py)

```python
from lib.replier import Replier

replier = Replier()

# Generate one reply
reply_text = replier.generate_reply(comment_dict, strategy="generic")

# Batch reply (smart = interactive, generic = auto)
result = replier.reply_batch(note_id, unreplied, strategy="smart", note_title="")
# → {success: int, fail: int, skip: int}
```

### CommentAnalyzer (lib/analyzer.py)

```python
from lib.analyzer import CommentAnalyzer

result = CommentAnalyzer.analyze(note_id, xsec_token="", note_title="", force_refresh=False)
CommentAnalyzer.print_report(result)
```

### Poster (lib/poster.py)

```python
from lib.poster import publish

publish(
    title="标题",
    body="正文",
    images=["/path/to/img.jpg"],
    topics=["标签"],
    private=False
)
```
