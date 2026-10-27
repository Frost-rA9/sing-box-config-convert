# Android import QR

Serve one sing-box config over the LAN and print a one-time import QR for sing-box for Android.

```bash
uv sync
uv run python tools/qr_import.py local/config-android.json
```

The tool:

- detects the LAN IPv4 address, or uses `--host`;
- serves only the config file, with a random token;
- writes a QR to `local/<name>-qr.png` (or `--qr-out`);
- keeps serving until `--ttl` expires or you press Ctrl+C;
- encodes a `sing-box://import-remote-profile` link, not a plain HTTP URL.

Scan the QR with sing-box for Android, then confirm the new remote profile.

## Options

| Option | Meaning |
|---|---|
| `config` | config file; default `local/config-android.json` |
| `--host` | LAN IPv4 to bind and put in the URL; detected by default |
| `--port` | TCP port; 0 picks a free port |
| `--ttl` | seconds before the server stops; default 1800, 0 disables |
| `--name` | profile name in the import link; defaults to the config stem |
| `--qr-out` | QR PNG path; defaults to `local/<name>-qr.png` |

## Notes

- The phone must reach the host IP on the same LAN. If detection picks a virtual adapter, pass `--host`.
- The config can contain node credentials. Use a trusted network and delete the QR after import.
- The client creates a remote profile with auto-update. Turn auto-update off after import, because the URL is temporary.
- For a local profile, download the JSON on the phone and import it from a file. That profile does not depend on this server.
- With `adb reverse tcp:PORT tcp:PORT`, use `--host 127.0.0.1 --port PORT`.
