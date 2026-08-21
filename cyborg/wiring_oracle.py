"""Нервы Oracle: обёртки над oracle_scan, oracle_plan, deliver_oracle.

Oracle — третья дорожка: фиксированная цепочка органов вне идейного роутера/мозга.
"""

from wiring_runtime import _content_llm


def _run_oracle_scan(inputs, env):
    import wiring

    e = {"oracle_project": env.get("oracle_project"), "projects_root": env.get("projects_root")}
    return wiring.oracle_scan.run(inputs or {}, e)


def _run_oracle_plan(inputs, env):
    import wiring

    e = {
        "oracle_goal": env.get("oracle_goal"),
        "llm": _content_llm(env),
    }
    return wiring.oracle_plan.run(inputs or {}, e)


def _run_deliver_oracle(inputs, env):
    import wiring

    e = {
        "oracle_project": env.get("oracle_project"),
        "oracle_goal": env.get("oracle_goal"),
    }
    out = wiring.deliver_oracle_organ.run(inputs or {}, e)
    # Адаптируем под контракт органов: результат доступен по ключу delivered.
    if out.get("ok"):
        return {"delivered": out, **out}
    return out
