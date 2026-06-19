from __future__ import annotations

import yuullm
from attrs import define


@define
class ProviderPoolSessionFactory:
    pool: yuullm.ProviderPool
    selector: str = ""

    def create_session(self, history: yuullm.History) -> yuullm.YuuSession:
        if not self.selector:
            raise ValueError("ProviderPoolSessionFactory requires a model selector")
        return self.pool.create_session(self.selector, history=history)

    def with_selector(self, selector: str) -> ProviderPoolSessionFactory:
        return ProviderPoolSessionFactory(pool=self.pool, selector=selector)


def select_llm_session_factory(
    factory: ProviderPoolSessionFactory,
    selector: str,
) -> ProviderPoolSessionFactory:
    return factory.with_selector(selector)
