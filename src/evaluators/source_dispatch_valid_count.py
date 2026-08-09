def perform_eval(run, example=None):
    """统计各来源调用、有效结果和证据数，并准确统计 Playwright body 写入次数。"""
    outputs = run.get("outputs") or {}
    if not isinstance(outputs, dict):
        outputs = {}

    nested = outputs.get("output")
    if isinstance(nested, dict):
        outputs = {**outputs, **nested}

    tool_to_source = {
        "tavily_mcp_search": "tavily",
        "context7_mcp_query": "context7",
        "you_com_api_search": "ydc",
        "serp_api_search": "serp",
    }

    records = outputs.get("web_tool_evaluation_records") or []
    if not isinstance(records, list):
        records = []

    evidence_items = outputs.get("evidence_items") or {}
    if not isinstance(evidence_items, dict):
        evidence_items = {}

    calls = {}
    raw_results = {}
    playwright_calls = 0
    playwright_body_ok = 0

    for record in records:
        if not isinstance(record, dict):
            continue

        tool_name = str(record.get("tool_name") or "")

        if tool_name.startswith("playwright_mcp_fetch"):
            playwright_calls += 1

            # 直接读取实际候选写入信号，不再使用 valid_result_count 代理。
            if record.get("body_appended") is True:
                playwright_body_ok += 1

            continue

        source = tool_to_source.get(tool_name)
        if not source:
            continue

        calls[source] = calls.get(source, 0) + 1

        try:
            valid_result_count = int(record.get("valid_result_count") or 0)
        except (TypeError, ValueError):
            valid_result_count = 0

        if (
            source in {"tavily", "ydc", "serp"}
            and record.get("ok")
            and valid_result_count > 0
        ):
            raw_results[source] = (
                raw_results.get(source, 0) + valid_result_count
            )

    evidence_by_source = {}

    for item in evidence_items.values():
        if not isinstance(item, dict):
            continue

        source = str(item.get("source_name") or "").strip()
        if not source:
            continue

        evidence_by_source[source] = (
            evidence_by_source.get(source, 0) + 1
        )

    parts = []

    for source in ("tavily", "ydc", "serp"):
        call_count = calls.get(source, 0)
        evidence_count = evidence_by_source.get(source, 0)

        if call_count == 0 and evidence_count == 0:
            continue

        parts.append(
            f"{source}(url): "
            f"calls={call_count}, "
            f"results={raw_results.get(source, 0)}, "
            f"evidence={evidence_count}"
        )

    context7_calls = calls.get("context7", 0)
    context7_evidence = evidence_by_source.get("context7", 0)

    if context7_calls or context7_evidence:
        parts.append(
            "context7(library): "
            f"calls={context7_calls}, "
            f"evidence={context7_evidence}"
        )

    if playwright_calls:
        parts.append(
            "playwright(page_fetch): "
            f"calls={playwright_calls}, "
            f"body_ok={playwright_body_ok}"
        )

    return {
        "source_dispatch_valid_count": "; ".join(parts) if parts else ""
    }