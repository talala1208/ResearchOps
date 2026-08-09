def perform_eval(run, example=None):
    """检测 ResearchOps 是否降级。"""
    outputs = run.get("outputs") or {}
    if not isinstance(outputs, dict):
        outputs = {}

    nested = outputs.get("output")
    if isinstance(nested, dict):
        outputs = {**outputs, **nested}

    metrics = outputs.get("evaluation_metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}

    raw = outputs.get("degraded", metrics.get("degraded", False))
    if isinstance(raw, bool):
        is_degraded = raw
    elif isinstance(raw, str):
        is_degraded = raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        is_degraded = bool(raw)

    return {"is_degraded": is_degraded}