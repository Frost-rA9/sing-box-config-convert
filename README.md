# sing-box-config-convert

Convert a Clash subscription into a configuration that sing-box loads.

## How it works

The subscription supplies the nodes. It is a URL or a local Clash YAML file. Two YAML files supply the rest.

| File | Holds |
|---|---|
| `artifacts/base.yaml` | The base template: DNS, inbounds, the route order, the fixed groups |
| `artifacts/services.yaml` | The profile: one group and one rule per service |

The tool merges the two files and renders the products. The agent does not write JSON.

A profile holds additions only, so a base template update does not force a profile update.

## Requirements

- Python 3.12 or later, managed by `uv`
- sing-box 1.14.x

## For an agent

```bash
uv sync                                                                     # install
uv run python -m sbconv report <subscription> -p artifacts/services.yaml     # cleaning report
uv run python -m sbconv render <subscription> -o local/config-tun.json -p artifacts/services.yaml --singbox <sing-box path>
uv run python -m sbconv render <subscription> -o local/config-test.json -p <profile> --test-inbound 17890 --singbox <path> --run 8
uv run pytest
```

Add `--proxy-mode` to keep a TUN that captures no traffic, and write the result to `local/config-proxy.json`. The client then sets the system proxy, and only applications that honour it use the routing rules.

Add `--android` to keep the TUN inbound only, and write the result to `local/config-android.json`. An Android client runs the TUN through its VPN service.

Work steps:

1. Read the request and the two files in `artifacts/`.
2. Write the new groups, rule sets, and rules into a profile.
3. Render the configuration.
4. Read the exit code, the warnings, and the check output. Change the profile until all checks pass.
5. Test the routing of a new rule, because the two checks cannot see two overlapping rule sets.

| Exit code | Meaning |
|---|---|
| 0 | The static check and `sing-box check` passed |
| 2 | The artifact or the profile is invalid |
| 3 | The static check failed |
| 4 | `sing-box check` failed |
| 5 | sing-box stopped before the timeout |

## Conventions

- The first matching rule wins, so write a specific rule before a broad rule.
- To split one rule set, write a `domain_suffix` rule before it. See `artifacts/example.yaml`.
- A group without `default` takes its first member.
- A base template places the profile groups with `{insert: profile_groups}`.
- `Direct` is the tag of the direct outbound, with a capital D.
- Products live in `local/`, which `.gitignore` excludes.

## Checks

Every render runs two checks:

- A static check for tag references, empty groups, and rule-set references. `sing-box check` ignores these.
- `sing-box check`, when `--singbox` gives its path.

`--run 8` starts sing-box for 8 seconds and reports an early exit. Use `--test-inbound` to replace the TUN inbound with one mixed inbound on a free port.

To test a new rule, set `log.level` to `debug`, start sing-box, and read the `router: match` lines for each domain.

## License

GPL-3.0-or-later. See `LICENSE`.
