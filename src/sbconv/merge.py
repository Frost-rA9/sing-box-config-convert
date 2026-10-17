"""Merge a base template and a user profile into one artifact. The insertion point in the base template decides where the rules go."""

from __future__ import annotations

from .artifacts import USER_RULES_SLOT, Artifact, Profile


class MergeError(Exception):
    """A profile and a base template do not agree."""


def merge(base: Artifact, profile: Profile | None = None) -> Artifact:
    if profile is None:
        return base
    _reject_tag_conflicts(base, profile)

    rules = list(base.route.rules)
    at = base.route.user_rules_at
    if at is None:
        if profile.route.user_rules:
            raise MergeError(
                f"策略文件有 {len(profile.route.user_rules)} 条 route.user_rules，"
                f"但基础模板没有插入点；要在 route.rules 里留一条 {{insert: {USER_RULES_SLOT}}}"
            )
        at = len(rules)
    rules[at:at] = profile.route.user_rules

    return base.model_copy(
        update={
            # Base groups come first, so a profile group can reference them.
            "groups": list(base.groups) + list(profile.groups),
            "rule_sets": list(base.rule_sets) + list(profile.rule_sets),
            "route": base.route.model_copy(update={"rules": rules}),
        }
    )


def _reject_tag_conflicts(base: Artifact, profile: Profile) -> None:
    pairs = (
        ("分组", [g.tag for g in base.groups], [g.tag for g in profile.groups]),
        ("规则集", [r.tag for r in base.rule_sets], [r.tag for r in profile.rule_sets]),
    )
    for label, base_tags, profile_tags in pairs:
        shared = sorted(set(base_tags) & set(profile_tags))
        if shared:
            raise MergeError(f"策略文件的{label}与基础模板重名：{shared}")
