def perform_eval(run, example=None):
    """返回各 source_name 的引用计数 used/total，并标明计数单位。"""
    outputs = run.get("outputs") or {}
    if not isinstance(outputs, dict):
        outputs = {}

    nested = outputs.get("output")
    if isinstance(nested, dict):
        outputs = {**outputs, **nested}

    evidence_items = outputs.get("evidence_items") or {}
    if not isinstance(evidence_items, dict) or not evidence_items:
        return {"source_citation_count": ""}

    # EvidenceItem 粒度单位：Context7 一库一条，其余来源一 URL/路径一条
    UNIT_BY_SOURCE = {
        "context7": "library",
    }
    DEFAULT_UNIT = "url"

    totals = {}
    used = {}

    for item in evidence_items.values():
        if not isinstance(item, dict):
            continue
        source_name = item.get("source_name")
        if not source_name:
            continue
        source_name = str(source_name)
        totals[source_name] = totals.get(source_name, 0) + 1
        if item.get("used_in_final_report") is True:
            used[source_name] = used.get(source_name, 0) + 1

    parts = []
    for source_name in sorted(totals):
        total = totals[source_name]
        cited = used.get(source_name, 0)
        unit = UNIT_BY_SOURCE.get(source_name, DEFAULT_UNIT)
        parts.append(f"{source_name}({unit}): {cited}/{total}")

    return {"source_citation_count": ", ".join(parts)}