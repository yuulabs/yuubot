"""Selector-based model resolution and alias persistence."""

from __future__ import annotations

import re
import os
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import attrs
import yaml
import yuullm
from loguru import logger

from yuubot.config import Config, _find_config

_BAD_TOKENS = ("preview", "beta", "search", "web", "tool", "thinking", "reasoning")
# Built-in fallback families — overridden by 'families:' in llm.yaml
_FAMILY_VISION: dict[str, bool] = {
    "claude": True,
    "gpt": True,
    "gemini": True,
    "deepseek": False,
}


def split_llm_ref(ref: str) -> tuple[str, str]:
    ref = ref.strip()
    if "/" not in ref:
        raise ValueError(f"invalid llm ref: {ref!r}")
    provider, model = ref.split("/", 1)
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        raise ValueError(f"invalid llm ref: {ref!r}")
    return provider, model


def detect_family(model: str, families: dict[str, Any] | None = None) -> str:
    """Return the family name that appears as a substring in *model*.

    *families* is a ``{name: {vision: bool, ...}}`` mapping loaded from
    ``llm.yaml``.  Falls back to the built-in ``_FAMILY_VISION`` keys when
    *families* is ``None`` or empty.
    """
    known = list(families.keys()) if families else list(_FAMILY_VISION.keys())
    lower = model.lower()
    for family in known:
        if family.lower() in lower:
            return family
    return ""


def family_supports_vision(family: str, families: dict[str, Any] | None = None) -> bool:
    """Return True if *family* is known to support vision.

    Consults *families* from config first; falls back to ``_FAMILY_VISION``.
    """
    if families:
        cfg = families.get(family, {})
        if isinstance(cfg, dict):
            return bool(cfg.get("vision", False))
        return False
    return _FAMILY_VISION.get(family, False)


def _default_alias_store_path() -> Path:
    return _find_config().with_name("model_aliases.yaml")


def _normalize_provider_aliases(raw: dict[str, Any]) -> dict[str, str]:
    return {
        str(alias).strip().lower(): str(provider).strip()
        for alias, provider in raw.items()
        if str(alias).strip() and str(provider).strip()
    }


def _normalize_int_mapping(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        try:
            result[str(key).strip()] = int(value)
        except TypeError, ValueError:
            continue
    return result


def _normalize_nested_int_mapping(raw: Any) -> dict[str, dict[str, int]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, int]] = {}
    for pattern, mapping in raw.items():
        nested = _normalize_int_mapping(mapping)
        if nested:
            result[str(pattern).strip()] = nested
    return result


def _merge_provider_priorities(*mappings: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for mapping in mappings:
        merged.update({k: int(v) for k, v in mapping.items() if k})
    return merged


def _merge_provider_affinity(
    *mappings: dict[str, dict[str, int]],
) -> dict[str, dict[str, int]]:
    merged: dict[str, dict[str, int]] = {}
    for mapping in mappings:
        for pattern, provider_weights in mapping.items():
            current = dict(merged.get(pattern, {}))
            current.update({k: int(v) for k, v in provider_weights.items() if k})
            merged[pattern] = current
    return merged


def _merge_llm_roles(*mappings: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for mapping in mappings:
        merged.update({k: str(v).strip() for k, v in mapping.items() if str(v).strip()})
    return merged


@attrs.define
class RoleOverride:
    provider: str | None = None
    selector: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.provider and not self.selector


def build_provider(provider_name: str, config: Config) -> yuullm.Provider:
    providers = config.yuuagents.get("providers", {})
    provider_cfg = providers.get(provider_name, {})
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
    api_type = provider_cfg.get("api_type", "openai-chat-completion")
    api_key_env = provider_cfg.get("api_key_env", "")
    api_key = os.environ.get(api_key_env) if api_key_env else None
    base_url = provider_cfg.get("base_url", "") or None

    default_headers = {
        "User-Agent": "yuubot/1.0",
        "X-Application-Name": "yuubot",
    }

    if api_type == "anthropic-messages":
        return yuullm.providers.AnthropicMessagesProvider(
            api_key=api_key,
            base_url=base_url,
            provider_name=provider_name or "anthropic",
            default_headers=default_headers,
        )
    if provider_name == "openrouter":
        return yuullm.providers.OpenRouterProvider(
            api_key=api_key,
            default_headers=default_headers,
        )
    return yuullm.providers.OpenAIChatCompletionProvider(
        api_key=api_key,
        base_url=base_url,
        provider_name=provider_name or "openai",
        default_headers=default_headers,
    )


def build_llm_client(
    provider_name: str, model: str, config: Config
) -> yuullm.YLLMClient:
    providers = config.yuuagents.get("providers", {})
    provider_cfg = providers.get(provider_name, {})
    default_model = model or (
        provider_cfg.get("default_model", "gpt-4o")
        if isinstance(provider_cfg, dict)
        else "gpt-4o"
    )
    provider = build_provider(provider_name, config)
    return yuullm.YLLMClient(
        provider=provider,
        default_model=default_model,
        price_calculator=yuullm.PriceCalculator(),
    )


def _collect_text(item: Any) -> str:
    if isinstance(item, dict):
        if item.get("type") == "text":
            return str(item.get("text", ""))
        return ""
    value = getattr(item, "item", item)
    if isinstance(value, dict):
        if value.get("type") == "text":
            return str(value.get("text", ""))
    if hasattr(value, "text"):
        return str(getattr(value, "text"))
    return ""


def _score_candidate(selector: str, candidate: str) -> tuple[int, tuple[int, ...], int]:
    selector_l = selector.lower()
    candidate_l = candidate.lower()
    score = 0
    if selector_l in candidate_l:
        score += 100
    for token in _BAD_TOKENS:
        if token in candidate_l:
            score -= 10
    if "latest" in candidate_l:
        score += 3
    if "stable" in candidate_l:
        score += 2
    version = tuple(int(part) for part in re.findall(r"\d+", candidate_l))
    return score, version, -len(candidate)


def _coarse_filter_candidates(selector: str, candidates: list[str]) -> list[str]:
    selector_l = selector.lower().strip()
    return [candidate for candidate in candidates if selector_l in candidate.lower()]


def _summarize_candidates(candidates: list[str], *, limit: int = 8) -> str:
    if not candidates:
        return "(none)"
    if len(candidates) <= limit:
        return ", ".join(candidates)
    visible = ", ".join(candidates[:limit])
    return f"{visible}, ... (+{len(candidates) - limit} more)"


@attrs.define
class ResolvedModel:
    requested_provider: str
    resolved_provider: str
    selector: str
    resolved_model: str
    family: str
    supports_vision: bool
    source: str

    @property
    def resolved_ref(self) -> str:
        return f"{self.resolved_provider}/{self.resolved_model}"


@attrs.define
class SelectorState:
    family: str = ""
    manual_bindings: dict[str, str] = attrs.field(factory=dict)
    auto_cache: dict[str, str] = attrs.field(factory=dict)


@attrs.define
class ModelAliasStore:
    path: Path = attrs.field(factory=_default_alias_store_path)
    selectors: dict[str, SelectorState] = attrs.field(factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "ModelAliasStore":
        store = cls(path=path or _default_alias_store_path())
        if not store.path.exists():
            return store
        raw = yaml.safe_load(store.path.read_text(encoding="utf-8")) or {}
        selectors_raw = raw.get("selectors", {})
        if not isinstance(selectors_raw, dict):
            return store
        for selector, payload in selectors_raw.items():
            if not isinstance(payload, dict):
                continue
            bindings = payload.get("manual_bindings", {})
            cache = payload.get("auto_cache", {})
            state = SelectorState(
                family=str(payload.get("family", "") or ""),
                manual_bindings={
                    str(provider): str(model)
                    for provider, model in (
                        bindings.items() if isinstance(bindings, dict) else []
                    )
                },
                auto_cache={
                    str(provider): str(model)
                    for provider, model in (
                        cache.items() if isinstance(cache, dict) else []
                    )
                },
            )
            store.selectors[str(selector)] = state
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "selectors": {
                selector: {
                    "family": state.family,
                    "manual_bindings": dict(sorted(state.manual_bindings.items())),
                    "auto_cache": dict(sorted(state.auto_cache.items())),
                }
                for selector, state in sorted(self.selectors.items())
                if state.family or state.manual_bindings or state.auto_cache
            }
        }
        self.path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def get(self, selector: str) -> SelectorState | None:
        return self.selectors.get(selector)

    def ensure(self, selector: str) -> SelectorState:
        state = self.selectors.get(selector)
        if state is None:
            state = SelectorState()
            self.selectors[selector] = state
        return state

    def set_manual_binding(
        self, selector: str, provider: str, model: str, family: str
    ) -> None:
        state = self.ensure(selector)
        if not state.family:
            state.family = family
        state.manual_bindings[provider] = model
        state.auto_cache.pop(provider, None)
        self.save()

    def set_auto_cache(
        self, selector: str, provider: str, model: str, family: str
    ) -> None:
        state = self.ensure(selector)
        if not state.family:
            state.family = family
        state.auto_cache[provider] = model
        self.save()

    def refresh(self, selector: str, provider: str | None = None) -> None:
        state = self.selectors.get(selector)
        if state is None:
            return
        if provider is None:
            state.auto_cache.clear()
        else:
            state.auto_cache.pop(provider, None)
        self.save()

    def delete(self, selector: str, provider: str | None = None) -> None:
        state = self.selectors.get(selector)
        if state is None:
            return
        if provider is None:
            del self.selectors[selector]
            self.save()
            return
        state.manual_bindings.pop(provider, None)
        state.auto_cache.pop(provider, None)
        if not state.family and not state.manual_bindings and not state.auto_cache:
            del self.selectors[selector]
        self.save()


class ModelResolver:
    """Resolve provider/model refs using provider aliases, selector bindings, and cache."""

    def __init__(self, config: Config, store: ModelAliasStore | None = None) -> None:
        self.config = config
        self.store = store or ModelAliasStore.load()
        aliases = config.yuuagents.get("provider_aliases", {})
        self.provider_aliases = _normalize_provider_aliases(
            aliases if isinstance(aliases, dict) else {}
        )
        self.provider_priorities = _merge_provider_priorities(
            _normalize_int_mapping(self.config.yuuagents.get("provider_priorities")),
            _normalize_int_mapping(getattr(self.config, "provider_priorities", {})),
        )
        self.provider_affinity = _merge_provider_affinity(
            _normalize_nested_int_mapping(
                self.config.yuuagents.get("provider_affinity")
            ),
            _normalize_nested_int_mapping(
                getattr(self.config, "provider_affinity", {})
            ),
        )
        self.llm_roles = _merge_llm_roles(
            self._llm_roles_from(self.config.yuuagents.get("llm_roles")),
            self._llm_roles_from(getattr(self.config, "llm_roles", {})),
        )
        families_raw = getattr(self.config, "families", {}) or {}
        self._families: dict[str, Any] = (
            families_raw if isinstance(families_raw, dict) else {}
        )
        selectors_raw = getattr(self.config, "selectors", []) or []
        self._selectors: list[str] = (
            [str(s) for s in selectors_raw if s]
            if isinstance(selectors_raw, list)
            else []
        )
        self._role_overrides: dict[str, RoleOverride] = {}
        self._role_sticky_providers: dict[str, str] = {}
        self._provider_model_cache: dict[str, list[yuullm.ProviderModel]] = {}

    @staticmethod
    def _llm_roles_from(raw: Any) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        return {
            str(role).strip(): str(ref).strip()
            for role, ref in raw.items()
            if str(ref).strip()
        }

    def _provider_names(self) -> list[str]:
        providers = self.config.yuuagents.get("providers", {})
        if isinstance(providers, dict):
            return [str(name) for name in providers.keys()]
        return []

    def resolve_provider(self, provider: str) -> str:
        current = provider.strip()
        seen: set[str] = set()
        while current.lower() in self.provider_aliases and current.lower() not in seen:
            seen.add(current.lower())
            current = self.provider_aliases[current.lower()]
        return current

    async def _provider_models(
        self, provider: str, *, refresh: bool = False
    ) -> list[yuullm.ProviderModel]:
        provider = self.resolve_provider(provider)
        if not refresh and provider in self._provider_model_cache:
            cached = self._provider_model_cache[provider]
            logger.info(
                "Model resolution: provider={} using cached online catalog count={}",
                provider,
                len(cached),
            )
            return cached

        logger.info(
            "Model resolution: provider={} listing online models refresh={}",
            provider,
            refresh,
        )
        try:
            models = await build_provider(provider, self.config).list_models()
        except Exception as exc:
            logger.exception(
                "Model resolution: provider={} failed to list online models",
                provider,
            )
            raise RuntimeError(
                f"failed to list models for provider {provider!r}"
            ) from exc
        self._provider_model_cache[provider] = models
        logger.info(
            "Model resolution: provider={} loaded online catalog count={} candidates={}",
            provider,
            len(models),
            _summarize_candidates([model.id for model in models]),
        )
        return models

    def _role_ref(self, role_name: str) -> str:
        role_ref = self.llm_roles.get(role_name, "")
        if not role_ref:
            raise ValueError(f"role {role_name!r} has no selector configured")
        return role_ref

    def _parse_role_ref(self, role_ref: str) -> tuple[str | None, str]:
        if "/" in role_ref:
            provider, selector = split_llm_ref(role_ref)
            return self.resolve_provider(provider), selector
        selector = role_ref.strip()
        if not selector:
            raise ValueError("role selector cannot be empty")
        return None, selector

    def _normalize_role_target(self, target: str | None) -> RoleOverride:
        if not target:
            return RoleOverride()
        raw = target.strip()
        if not raw:
            return RoleOverride()
        if "/" in raw:
            provider, selector = split_llm_ref(raw)
            return RoleOverride(
                provider=self.resolve_provider(provider), selector=selector
            )
        if raw in self._provider_names() or raw.lower() in self.provider_aliases:
            return RoleOverride(provider=self.resolve_provider(raw))
        return RoleOverride(selector=raw)

    def set_role_override(self, role_name: str, target: str) -> RoleOverride:
        override = self._normalize_role_target(target)
        if override.is_empty:
            raise ValueError("role override cannot be empty")
        self._role_overrides[role_name] = override
        return override

    def clear_role_override(self, role_name: str) -> None:
        self._role_overrides.pop(role_name, None)
        self._role_sticky_providers.pop(role_name, None)

    def _role_target(
        self,
        role_name: str,
        *,
        provider_override: str | None = None,
        selector_override: str | None = None,
    ) -> tuple[str | None, str]:
        base_provider, base_selector = self._parse_role_ref(self._role_ref(role_name))
        override = self._role_overrides.get(role_name)
        provider = base_provider
        selector = base_selector
        if override is not None:
            if override.provider:
                provider = override.provider
            if override.selector:
                selector = override.selector
        if provider_override is not None:
            provider = self.resolve_provider(provider_override)
        if selector_override is not None:
            selector = selector_override
        return provider, selector

    def _selector_resolution_error(self, provider: str, selector: str) -> ValueError:
        return ValueError(
            f"provider {provider!r} has no model candidates matching selector {selector!r}"
        )

    async def _resolve_selector_on_provider(
        self,
        provider: str,
        selector: str,
        *,
        requested_provider: str | None = None,
        refresh: bool = False,
        allow_llm_choice: bool = True,
    ) -> ResolvedModel | None:
        provider = self.resolve_provider(provider)
        selector = selector.strip()
        logger.info(
            "Model resolution: trying provider={} selector={} requested_provider={} refresh={}",
            provider,
            selector,
            requested_provider or provider,
            refresh,
        )
        state = self.store.get(selector)
        if state is not None:
            if provider in state.manual_bindings:
                actual_model = state.manual_bindings[provider]
                logger.info(
                    "Model resolution: selector={} provider={} hit manual binding model={} store={}",
                    selector,
                    provider,
                    actual_model,
                    self.store.path,
                )
                return self._make_resolved(
                    requested_provider or provider,
                    provider,
                    selector,
                    actual_model,
                    "manual",
                )
            if not refresh and provider in state.auto_cache:
                actual_model = state.auto_cache[provider]
                logger.info(
                    "Model resolution: selector={} provider={} hit auto cache model={} store={}",
                    selector,
                    provider,
                    actual_model,
                    self.store.path,
                )
                return self._make_resolved(
                    requested_provider or provider,
                    provider,
                    selector,
                    actual_model,
                    "cache",
                )

        models = await self._provider_models(provider, refresh=refresh)
        exact = next(
            (candidate for candidate in models if candidate.id == selector), None
        )
        if exact is not None:
            logger.info(
                "Model resolution: provider={} selector={} matched exact model={}",
                provider,
                selector,
                exact.id,
            )
            return self._make_resolved(
                requested_provider or provider,
                provider,
                selector,
                exact.id,
                "direct",
                supports_vision=exact.supports_vision,
            )

        filtered = [
            candidate
            for candidate in models
            if selector.lower().strip() in candidate.id.lower()
        ]
        if not filtered:
            logger.info(
                "Model resolution: provider={} selector={} matched no online candidates",
                provider,
                selector,
            )
            return None
        logger.info(
            "Model resolution: provider={} selector={} filtered_candidates count={} candidates={}",
            provider,
            selector,
            len(filtered),
            _summarize_candidates([candidate.id for candidate in filtered]),
        )
        if len(filtered) == 1:
            logger.info(
                "Model resolution: provider={} selector={} chose sole candidate={}",
                provider,
                selector,
                filtered[0].id,
            )
            return self._make_resolved(
                requested_provider or provider,
                provider,
                selector,
                filtered[0].id,
                "auto",
                supports_vision=filtered[0].supports_vision,
            )
        if allow_llm_choice:
            try:
                actual_model = await self._choose_candidate(
                    provider,
                    selector,
                    [candidate.id for candidate in filtered],
                )
                logger.info(
                    "Model resolution: provider={} selector={} chose candidate={} via selector model",
                    provider,
                    selector,
                    actual_model,
                )
            except Exception:
                logger.exception(
                    "Selector resolution failed, falling through: provider={} selector={}",
                    provider,
                    selector,
                )
                return None
        else:
            actual_model = sorted(
                [candidate.id for candidate in filtered],
                key=lambda item: _score_candidate(selector, item),
                reverse=True,
            )[0]
            logger.info(
                "Model resolution: provider={} selector={} chose candidate={} via heuristic",
                provider,
                selector,
                actual_model,
            )
        chosen = next(
            candidate for candidate in filtered if candidate.id == actual_model
        )
        return self._make_resolved(
            requested_provider or provider,
            provider,
            selector,
            actual_model,
            "auto",
            supports_vision=chosen.supports_vision,
        )

    async def resolve_role(
        self,
        role_name: str,
        *,
        provider_override: str | None = None,
        selector_override: str | None = None,
        refresh: bool = False,
        update_sticky: bool = True,
        allow_llm_choice: bool = True,
    ) -> ResolvedModel:
        provider_hint, selector = self._role_target(
            role_name,
            provider_override=provider_override,
            selector_override=selector_override,
        )
        sticky_provider = self._role_sticky_providers.get(role_name)
        logger.info(
            "Model resolution: resolve_role role={} selector={} provider_hint={} sticky={} refresh={}",
            role_name,
            selector,
            provider_hint or "(auto)",
            sticky_provider or "(none)",
            refresh,
        )

        if provider_hint is not None:
            resolved = await self._resolve_selector_on_provider(
                provider_hint,
                selector,
                refresh=refresh,
                allow_llm_choice=allow_llm_choice,
            )
            if resolved is None:
                raise self._selector_resolution_error(provider_hint, selector)
            if update_sticky:
                self._role_sticky_providers[role_name] = resolved.resolved_provider
            return resolved

        ordered = self._ordered_providers(selector, sticky_provider=sticky_provider)
        logger.info(
            "Model resolution: role={} provider order={}",
            role_name,
            _summarize_candidates(ordered),
        )
        for provider in ordered:
            resolved = await self._resolve_selector_on_provider(
                provider,
                selector,
                refresh=refresh,
                allow_llm_choice=allow_llm_choice,
            )
            if resolved is None:
                if sticky_provider and provider == sticky_provider:
                    self._role_sticky_providers.pop(role_name, None)
                continue
            if update_sticky:
                self._role_sticky_providers[role_name] = resolved.resolved_provider
            return resolved
        if ordered:
            raise self._selector_resolution_error(ordered[0], selector)
        raise self._selector_resolution_error("", selector)

    async def resolve_role_llm(
        self,
        role_name: str,
        *,
        provider_override: str | None = None,
        selector_override: str | None = None,
        refresh: bool = False,
        update_sticky: bool = True,
        allow_llm_choice: bool = True,
    ) -> tuple[yuullm.YLLMClient, ResolvedModel]:
        resolved = await self.resolve_role(
            role_name,
            provider_override=provider_override,
            selector_override=selector_override,
            refresh=refresh,
            update_sticky=update_sticky,
            allow_llm_choice=allow_llm_choice,
        )
        return build_llm_client(
            resolved.resolved_provider, resolved.resolved_model, self.config
        ), resolved

    def _provider_priority(self, provider: str, selector: str) -> int:
        score = int(self.provider_priorities.get(provider, 0))
        selector_l = selector.lower()
        for pattern, provider_weights in self.provider_affinity.items():
            if fnmatchcase(selector_l, pattern.lower()):
                score += int(provider_weights.get(provider, 0))
        return score

    def _ordered_providers(
        self,
        selector: str,
        *,
        sticky_provider: str | None = None,
    ) -> list[str]:
        providers = self._provider_names()
        provider_index = {provider: idx for idx, provider in enumerate(providers)}
        ordered = sorted(
            providers,
            key=lambda provider: (
                -(self._provider_priority(provider, selector)),
                provider_index[provider],
            ),
        )
        if sticky_provider:
            sticky_provider = self.resolve_provider(sticky_provider)
            if sticky_provider in provider_index:
                ordered = [sticky_provider] + [
                    provider for provider in ordered if provider != sticky_provider
                ]
        return ordered

    def get_agent_llm_ref(self, agent_name: str) -> str:
        agent_llm_ref = getattr(self.config, "agent_llm_ref", None)
        if callable(agent_llm_ref):
            return agent_llm_ref(agent_name)

        ref = str(
            getattr(self.config, "agent_llm_refs", {}).get(agent_name, "") or ""
        ).strip()
        if ref:
            return ref

        from yuubot.characters import CHARACTER_REGISTRY

        char = CHARACTER_REGISTRY.get(agent_name)
        if char is not None:
            llm_ref = str(getattr(char, "llm_ref", "") or "").strip()
            if llm_ref:
                return llm_ref
            provider = str(getattr(char, "provider", "") or "").strip()
            model = str(getattr(char, "model", "") or "").strip()
            if provider and model:
                return f"{provider}/{model}"
        raise ValueError(f"agent {agent_name!r} has no llm ref configured")

    async def resolve_agent(
        self, agent_name: str, *, refresh: bool = False
    ) -> ResolvedModel:
        return await self.resolve_ref(
            self.get_agent_llm_ref(agent_name), refresh=refresh
        )

    async def resolve_ref(
        self, llm_ref: str, *, refresh: bool = False
    ) -> ResolvedModel:
        provider_raw, model_part = split_llm_ref(llm_ref)
        provider = self.resolve_provider(provider_raw)
        logger.info(
            "Model resolution: resolve_ref ref={} provider={} selector={} refresh={}",
            llm_ref,
            provider,
            model_part,
            refresh,
        )
        resolved = await self._resolve_selector_on_provider(
            provider,
            model_part,
            requested_provider=provider_raw,
            refresh=refresh,
        )
        if resolved is None:
            raise self._selector_resolution_error(provider, model_part)
        return resolved

    def _make_resolved(
        self,
        requested_provider: str,
        provider: str,
        selector: str,
        actual_model: str,
        source: str,
        *,
        supports_vision: bool | None = None,
    ) -> ResolvedModel:
        fam = self._families or None
        family = detect_family(actual_model, fam)
        if supports_vision is None:
            supports_vision = family_supports_vision(family, fam)
        if source == "auto":
            self.store.set_auto_cache(selector, provider, actual_model, family)
        logger.info(
            "Model resolution: resolved requested_provider={} provider={} selector={} model={} source={} family={} vision={}",
            requested_provider,
            provider,
            selector,
            actual_model,
            source,
            family or "(unknown)",
            bool(supports_vision),
        )
        return ResolvedModel(
            requested_provider=requested_provider,
            resolved_provider=provider,
            selector=selector,
            resolved_model=actual_model,
            family=family,
            supports_vision=bool(supports_vision),
            source=source,
        )

    async def _auto_resolve(
        self,
        provider: str,
        selector: str,
        candidates: list[str],
        *,
        refresh: bool = False,
    ) -> str:
        del refresh
        filtered = _coarse_filter_candidates(selector, candidates)
        if not filtered:
            raise ValueError(
                f"provider {provider!r} has no model candidates matching selector {selector!r}"
            )
        if len(filtered) == 1:
            return filtered[0]
        chosen = await self._choose_candidate(provider, selector, filtered)
        return chosen

    async def _choose_candidate(
        self, provider: str, selector: str, candidates: list[str]
    ) -> str:
        try:
            client, chooser = await self.resolve_role_llm(
                "selector",
                update_sticky=False,
                allow_llm_choice=False,
            )
            prompt = [
                yuullm.user(
                    "\n".join(
                        [
                            f"Choose the best model for selector {selector!r} on provider {provider!r}.",
                            "Pick exactly one candidate from the list.",
                            "Prefer stable, latest, general-purpose chat models.",
                            "Avoid preview, beta, search, web, thinking, reasoning, or tool-specific variants unless no better option exists.",
                            "Return only the exact candidate string.",
                            "",
                            "Candidates:",
                            *[f"- {candidate}" for candidate in candidates],
                        ]
                    )
                )
            ]
            stream, _store = await client.stream(prompt, model=chooser.resolved_model)
            text = ""
            async for item in stream:
                text += _collect_text(item)
            choice = text.strip().strip("`\"'")
            if choice in candidates:
                return choice
            for candidate in candidates:
                if candidate in choice:
                    return candidate
        except Exception:
            logger.exception(
                "Model-selection role failed, falling back to heuristic: provider={} selector={}",
                provider,
                selector,
            )
        return sorted(
            candidates, key=lambda item: _score_candidate(selector, item), reverse=True
        )[0]

    def bind_resolved(self, resolved: ResolvedModel, selector: str) -> ResolvedModel:
        self.store.set_manual_binding(
            selector,
            resolved.resolved_provider,
            resolved.resolved_model,
            resolved.family,
        )
        return ResolvedModel(
            requested_provider=resolved.requested_provider,
            resolved_provider=resolved.resolved_provider,
            selector=selector,
            resolved_model=resolved.resolved_model,
            family=resolved.family,
            supports_vision=resolved.supports_vision,
            source="manual",
        )

    async def bind_current(self, agent_name: str, selector: str) -> ResolvedModel:
        resolved = await self.resolve_agent(agent_name)
        return self.bind_resolved(resolved, selector)

    async def show_role(self, role_name: str) -> str:
        provider, selector = self._role_target(role_name)
        override = self._role_overrides.get(role_name)
        sticky = self._role_sticky_providers.get(role_name, "")
        resolved = await self.resolve_role(role_name, update_sticky=False)
        lines = [
            f"Role: {role_name}",
            f"Selector: {selector}",
            f"Config provider: {provider or '(auto)'}",
            (
                "Override: "
                + (
                    f"provider={override.provider}, selector={override.selector}"
                    if override is not None and not override.is_empty
                    else "(none)"
                )
            ),
            f"Sticky provider: {sticky or '(none)'}",
            f"Resolved: {resolved.resolved_provider}/{resolved.resolved_model}",
            f"Family: {resolved.family or '(unknown)'}",
            f"Vision: {resolved.supports_vision}",
            f"Source: {resolved.source}",
        ]
        return "\n".join(lines)

    def list_roles(self) -> str:
        if not self.llm_roles:
            return "(空)"
        lines = []
        for role_name in sorted(self.llm_roles):
            provider, selector = self._role_target(role_name)
            override = self._role_overrides.get(role_name)
            sticky = self._role_sticky_providers.get(role_name, "")
            override_text = "(none)"
            if override is not None and not override.is_empty:
                if override.provider and override.selector:
                    override_text = (
                        f"provider={override.provider}, selector={override.selector}"
                    )
                elif override.provider:
                    override_text = f"provider={override.provider}"
                else:
                    override_text = f"selector={override.selector}"
            lines.append(
                f"- {role_name}: selector={selector}, provider={provider or '(auto)'}, "
                f"sticky={sticky or '(none)'}, override={override_text}"
            )
        return "\n".join(lines)

    def list_selectors(self) -> str:
        """Return configured selector names from llm.yaml plus any store entries."""
        names: set[str] = set(self._selectors)
        names.update(self.store.selectors.keys())
        if not names:
            return "(空)"
        lines = []
        for name in sorted(names):
            state = self.store.get(name)
            if state and (state.manual_bindings or state.auto_cache):
                bound = ", ".join(
                    f"{p}={m}"
                    for p, m in sorted(
                        {**state.auto_cache, **state.manual_bindings}.items()
                    )
                )
                tag = " [manual]" if state.manual_bindings else ""
                lines.append(f"- {name}: {bound}{tag}")
            elif name in self._selectors:
                lines.append(f"- {name}: (hint, no binding yet)")
            else:
                lines.append(f"- {name}: (store, unbound)")
        return "\n".join(lines)

    def refresh_role(self, role_name: str) -> None:
        provider, selector = self._role_target(role_name)
        self._role_sticky_providers.pop(role_name, None)
        if provider is None:
            self.store.refresh(selector)
        else:
            self.store.refresh(selector, provider)

    def clear_role(self, role_name: str) -> None:
        self.clear_role_override(role_name)

    def show_selector(self, selector: str) -> str:
        state = self.store.get(selector)
        if state is None:
            return f"未知 selector: {selector}"
        lines = [f"Selector: {selector}"]
        lines.append(f"Family: {state.family or '(unknown)'}")
        lines.append(
            "Manual bindings: "
            + (
                ", ".join(
                    f"{provider}={model}"
                    for provider, model in sorted(state.manual_bindings.items())
                )
                if state.manual_bindings
                else "(none)"
            )
        )
        lines.append(
            "Auto cache: "
            + (
                ", ".join(
                    f"{provider}={model}"
                    for provider, model in sorted(state.auto_cache.items())
                )
                if state.auto_cache
                else "(none)"
            )
        )
        return "\n".join(lines)

    def refresh(self, ref: str) -> ResolvedModel | None:
        if "/" in ref:
            provider_raw, selector = split_llm_ref(ref)
            provider = self.resolve_provider(provider_raw)
            self.store.refresh(selector, provider)
            return None
        self.store.refresh(ref)
        return None

    def delete(self, ref: str) -> None:
        if "/" in ref:
            provider_raw, selector = split_llm_ref(ref)
            provider = self.resolve_provider(provider_raw)
            self.store.delete(selector, provider)
            return
        self.store.delete(ref)
