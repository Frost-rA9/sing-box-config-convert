"""Schema for the declarative artifacts: the skeleton and the requirements in one file."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SUPPORTED_MIN_MAJOR_MINOR = (1, 12)

# The base template marks the insertion points for the profile:
# route.rules takes the rules, groups takes the profile groups.
USER_RULES_SLOT = "user_rules"
PROFILE_GROUPS_SLOT = "profile_groups"
INSERT_KEY = "insert"


class Meta(BaseModel):
    name: str
    singbox_version: str = "1.14"
    strategy: Literal["prefer_ipv4", "prefer_ipv6", "ipv4_only", "ipv6_only"] = "prefer_ipv4"

    @field_validator("singbox_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        parts = v.split(".")
        try:
            major, minor = int(parts[0]), int(parts[1])
        except (IndexError, ValueError) as exc:
            raise ValueError(f"版本号要写成 1.14 或 1.14.3，收到 {v!r}") from exc
        if (major, minor) < SUPPORTED_MIN_MAJOR_MINOR:
            need = ".".join(str(x) for x in SUPPORTED_MIN_MAJOR_MINOR)
            raise ValueError(f"只支持 {need} 及以上的新 DNS 语法，收到 {v!r}")
        return v


class RegionKey(BaseModel):
    type: Literal["chinese_name", "flag_emoji", "iso_code"]


class RegionOverride(BaseModel):
    match_name: str
    region: str


class NodeRules(BaseModel):
    drop_name_keywords: list[str] = []
    drop_duplicate_by: list[str] = ["server", "server_port"]
    plugin_alias: dict[str, str] = {}
    region_keys: list[RegionKey] = []
    region_overrides: list[RegionOverride] = []
    fallback_region: str = "XX"  # XX = unknown region. See regions.REGION_NAMES.


class GroupSpec(BaseModel):
    """One group. Member sources stack: `groups` takes tags, `regions` takes
    nodes, and `exclude_regions`, `include`, and `exclude` refine them.
    """

    tag: str
    type: Literal["selector", "urltest"] = "selector"
    regions: list[str] = []
    exclude_regions: list[str] = []
    groups: list[str] = []
    include: str | None = None
    exclude: str | None = None
    default: str | None = None
    url: str | None = None
    interval: str | None = None
    tolerance: int | None = None

    @field_validator("groups", mode="before")
    @classmethod
    def _wrap_single(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


class DnsServer(BaseModel):
    model_config = ConfigDict(extra="allow")

    tag: str
    type: str
    server: str | None = None
    detour: str | None = None


class DnsSpec(BaseModel):
    servers: list[DnsServer]
    rules: list[dict[str, Any]] = []
    final: str | None = None


class RuleSetSpec(BaseModel):
    tag: str
    url: str
    type: Literal["remote"] = "remote"
    format: Literal["binary", "source"] = "binary"


class HttpClientSpec(BaseModel):
    """Download channel for remote rule sets. A failed startup fetch is a FATAL error."""

    tag: str
    detour: str | None = None
    version: int | None = None


class RouteSpec(BaseModel):
    # Required from sing-box 1.14 on. Without it the core stops with FATAL.
    default_domain_resolver: str
    # Index of the insertion point, filled by the Artifact validator. The output
    # does not contain this field.
    user_rules_at: int | None = Field(default=None, exclude=True)
    # Empty means the core fetches rule sets through its default outbound and
    # reports a deprecation warning. A test saw the fetch go through a proxy
    # node, where an unavailable node stops the core.
    default_http_client: str | None = None
    rules: list[dict[str, Any]] = []
    final: str | None = None
    auto_detect_interface: bool = True


class Artifact(BaseModel):
    meta: Meta
    nodes: NodeRules = NodeRules()
    groups: list[GroupSpec]
    dns: DnsSpec
    http_clients: list[HttpClientSpec] = []
    rule_sets: list[RuleSetSpec] = []
    route: RouteSpec
    inbounds: list[dict[str, Any]] = []
    # Index of the profile-group insertion point, filled by the validator below.
    # The output does not contain this field.
    profile_groups_at: int | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _strip_group_slot(cls, data: Any) -> Any:
        """Remove the profile-group sentinel from the raw groups list and keep its index.

        The sentinel must go before the field validator: it is not a group, and
        `GroupSpec` would reject it.
        """
        if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
            return data
        kept: list[Any] = []
        at: int | None = None
        for item in data["groups"]:
            if not isinstance(item, dict) or INSERT_KEY not in item:
                kept.append(item)
                continue
            if set(item) != {INSERT_KEY} or item[INSERT_KEY] != PROFILE_GROUPS_SLOT:
                raise ValueError(f"插入点只能写 {{{INSERT_KEY}: {PROFILE_GROUPS_SLOT}}}，收到 {item!r}")
            if at is not None:
                raise ValueError("插入点只能有一处，groups 里出现了两处")
            at = len(kept)
        return {**data, "groups": kept, "profile_groups_at": at}

    @model_validator(mode="after")
    def _resolve_insert_point(self) -> Artifact:
        """Remove the insertion-point sentinel from route.rules and keep its index.

        The core rejects a configuration that contains the `insert` field.
        """
        rules: list[dict[str, Any]] = []
        slot: int | None = None
        for rule in self.route.rules:
            if INSERT_KEY not in rule:
                rules.append(rule)
                continue
            if set(rule) != {INSERT_KEY} or rule[INSERT_KEY] != USER_RULES_SLOT:
                raise ValueError(f"插入点只能写 {{{INSERT_KEY}: {USER_RULES_SLOT}}}，收到 {rule!r}")
            if slot is not None:
                raise ValueError("插入点只能有一处，route.rules 里出现了两处")
            slot = len(rules)
        self.route.rules = rules
        self.route.user_rules_at = slot
        return self

    @model_validator(mode="after")
    def _domain_resolver_exists(self) -> Artifact:
        tags = {s.tag for s in self.dns.servers}
        if self.route.default_domain_resolver not in tags:
            raise ValueError(
                f"route.default_domain_resolver={self.route.default_domain_resolver!r} 不在 dns.servers 里，"
                f"可选：{sorted(tags)}"
            )
        return self

    @model_validator(mode="after")
    def _http_client_exists(self) -> Artifact:
        if self.route.default_http_client is None:
            return self
        tags = {c.tag for c in self.http_clients}
        if self.route.default_http_client not in tags:
            raise ValueError(
                f"route.default_http_client={self.route.default_http_client!r} 不在 http_clients 里，"
                f"可选：{sorted(tags)}"
            )
        return self

    @model_validator(mode="after")
    def _tags_unique(self) -> Artifact:
        for label, tags in (
            ("groups", [g.tag for g in self.groups]),
            ("dns.servers", [s.tag for s in self.dns.servers]),
            ("rule_sets", [r.tag for r in self.rule_sets]),
            ("http_clients", [c.tag for c in self.http_clients]),
        ):
            reject_duplicate_tags(label, tags)
        return self


def reject_duplicate_tags(label: str, tags: list[str]) -> None:
    dup = {t for t in tags if tags.count(t) > 1}
    if dup:
        raise ValueError(f"{label} 里有重名 tag：{sorted(dup)}")


class ProfileRoute(BaseModel):
    """A profile adds rules to route.rules only. The base template decides where they go.

    A rule must not set `action`, except `reject`, and `direct` is a no-op on 1.14.3.
    """

    user_rules: list[dict[str, Any]] = []

    # Every profile field is optional, so a typo in a key name would do nothing
    # silently. Reject unknown keys.
    model_config = ConfigDict(extra="forbid")

    @field_validator("user_rules")
    @classmethod
    def _rules_are_safe(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for i, rule in enumerate(v):
            action = rule.get("action")
            if action is not None and action != "reject":
                raise ValueError(
                    f"route.user_rules[{i}] 用了 action: {action}，"
                    "策略规则只能写匹配条件加 outbound，或 action: reject"
                )
            if action is None and not rule.get("outbound"):
                raise ValueError(f"route.user_rules[{i}] 既没有 outbound 也没有 action: reject，命中后不产生任何效果")
        return v


class Profile(BaseModel):
    """A user profile holds additions only.

    The base template holds the skeleton, the DNS, and the route order, so a
    base-template update does not force a profile update.
    """

    model_config = ConfigDict(extra="forbid")

    groups: list[GroupSpec] = []
    rule_sets: list[RuleSetSpec] = []
    route: ProfileRoute = ProfileRoute()

    @model_validator(mode="after")
    def _tags_unique(self) -> Profile:
        reject_duplicate_tags("groups", [g.tag for g in self.groups])
        reject_duplicate_tags("rule_sets", [r.tag for r in self.rule_sets])
        return self


def _load_yaml(path: str) -> Any:
    import yaml

    from pathlib import Path

    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_artifact(path: str) -> Artifact:
    return Artifact.model_validate(_load_yaml(path))


def load_profile(path: str) -> Profile:
    return Profile.model_validate(_load_yaml(path))
