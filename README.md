# mySilo

![Version](https://img.shields.io/badge/version-1.2.0-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![AWS S3](https://img.shields.io/badge/storage-AWS%20S3-orange)

A personal file sync daemon that keeps a local folder in sync with an AWS S3 bucket. Drop files into the folder and they upload automatically. Files added from another machine are detected and downloaded within seconds.

## How it works

- A **filesystem watcher** detects local changes in real time and uploads immediately (with a 1 s debounce so rapid saves collapse into one upload).
- A **scheduler** runs a full bidirectional reconciliation on startup and then on a configurable interval as a safety net.
- A **remote state poller** checks S3 every 30 s (one HEAD call) for changes made by other machines. If the remote state changed, it triggers an immediate reconcile — no waiting for the next interval.
- A **Lambda function** updates a remote state file (`.mysilo_remote_state.json`) on every S3 PUT/DELETE, so local clients can detect multi-machine changes cheaply without listing the entire bucket.
- A **local state file** (`.mysilo_state.json`) tracks every file's hash, size, and sync status so the daemon knows exactly what changed on either side.
- **Parallel uploads** — reconciliation fans work out across a thread pool, so pasting 10 files uploads all 10 simultaneously.
- **Retry with backoff** — failed uploads/downloads are retried with exponential backoff. Persistently failing files are marked `FAILED` and retried on every reconciliation.
- **Conflict handling** — if both the local file and the S3 version changed since the last sync, the remote copy is saved alongside the original as `filename.conflict_YYYYMMDD_HHMMSS.ext`. No data is ever silently overwritten.
- **SSE-KMS encryption** — all files are stored in S3 with server-side encryption. Uses the AWS-managed key by default; supply your own KMS key ARN for full control.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- [Terraform](https://www.terraform.io/) >= 1.5 (for deploying the Lambda)
- AWS credentials configured (`~/.aws/credentials` or environment variables)

## Deploy the Lambda (one time)

The Lambda keeps the remote state file up to date so all machines can detect changes without expensive S3 list calls.

```bash
cd mySilo/remote/terraform

# Copy and fill in your values
cp terraform.tfvars.example terraform.tfvars

terraform init
terraform apply
```

Reserved concurrency is set to 1 in Terraform to prevent concurrent Lambda executions from corrupting the remote state file.

## Configuration

Edit `mySilo/local/config.yaml` before running:

```yaml
local_directory: /Users/you/mySilo   # folder to watch and sync
bucket: your-s3-bucket               # S3 bucket name
region: eu-west-1                    # AWS region

sync_interval_minutes: 60            # full reconciliation interval (minutes)
upload_workers: 5                    # parallel upload/download threads

max_retries: 3                       # retry attempts per failed upload/download
retry_backoff_seconds: 2.0           # initial backoff (doubles each attempt: 2s, 4s, 8s)

remote_state_key: .mysilo_remote_state.json   # must match Terraform REMOTE_STATE_KEY
remote_poll_seconds: 30                       # how often to check for remote changes

# Optional - SSE-KMS with your own key.
kms_key_id: "arn:aws:kms:eu-west-1:123456789012:key/your-key-id"
```

### Environment variables

Environment variables take precedence over `config.yaml`.

| Variable | Config key | Description |
|---|---|---|
| `MYSILO_LOCAL_DIR` | `local_directory` | Local folder to sync |
| `MYSILO_REMOTE_BUCKET` | `bucket` | S3 bucket name |
| `MYSILO_REGION` | `region` | AWS region |
| `MYSILO_KMS_KEY_ID` | `kms_key_id` | KMS key ARN for SSE-KMS |

## Usage

### Install

```bash
cd mySilo/local
uv sync
```

This installs the `mysilo` CLI into the project's virtual environment. Activate it or use `uv run` prefix for one-off commands.

### Start the daemon

```bash
mysilo start
```

Runs a full reconciliation on startup, starts the filesystem watcher, the scheduler, and the remote state poller.

### CLI commands

| Command | Description |
|---|---|
| `mysilo start` | Start the sync daemon |
| `mysilo reconcile` | Run one full reconciliation and exit |
| `mysilo conflicts` | List all unresolved conflicts |
| `mysilo failed` | List all persistently failing files |
| `mysilo start --config path/to/config.yaml` | Use a custom config file |
| `mysilo --help` | Show all commands |

### Resolving conflicts

When a conflict is detected the remote version is saved next to the original:

```
document.pdf                          ← your local version (untouched)
document.conflict_20260406_143022.pdf ← remote version
```

Run `--conflicts` to list them. Delete the copy you don't want, the conflict marker clears automatically on the next reconciliation.

### Handling failed uploads

If a file fails all retry attempts it is marked `FAILED` in the local state and retried on every subsequent reconciliation.

```bash
mysilo failed
# FAILED  photos/img.jpg  (retried 3x — attempt 4 of 4 failed)
```

Check your network connection or AWS credentials, then run `mysilo reconcile` to retry immediately.

## Logs

The daemon writes to both stdout and `/your-local-dir/.mysilo/mysilo.log`. The `.mysilo/` folder is never synced to S3. Conflicts and failures are logged at `WARNING` level so they stand out.

## Project structure

```
mySilo/
├── local/
│   ├── config.yaml          # all configuration
│   ├── pyproject.toml       # dependencies (managed by uv)
│   ├── uv.lock              # pinned dependency tree
│   ├── mysilo/
│   │   ├── main.py          # entry point, CLI, scheduler, remote poller
│   │   ├── config.py        # config loader with env var overrides
│   │   ├── state.py         # local JSON state file, thread-safe
│   │   ├── s3_client.py     # boto3 wrapper + remote state file methods
│   │   ├── syncer.py        # sync logic, retry, conflict handling, thread pool
│   │   └── watcher.py       # filesystem watcher with debouncing
│   └── tests/
│       ├── conftest.py      # shared fixtures
│       ├── test_config.py
│       ├── test_s3_client.py
│       ├── test_state.py
│       └── test_syncer.py
└── remote/
    ├── lambda/
    │   └── handler.py       # Lambda — updates remote state on S3 events
    └── terraform/
        ├── main.tf          # Lambda, IAM, S3 event notification
        ├── variables.tf
        ├── outputs.tf
        └── terraform.tfvars.example
```

## Multi-machine sync flow

```
Machine A drops a file
  → mySilo uploads to S3
    → S3 fires event → Lambda updates .mysilo_remote_state.json

Machine B (within 30 s)
  → poller HEADs remote state → ETag changed
  → triggers reconcile immediately
  → downloads .mysilo_remote_state.json (1 GET)
  → diffs against local state → downloads only the new file
```

> Without the Lambda deployed, mySilo falls back to `list_objects` automatically and logs a warning — everything still works, just less efficiently.
