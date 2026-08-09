import re


def perform_eval(run, example=None):
    """证据有效率 = final_report 中出现的 Exx 数 / 全部 evidence_ids 数。"""
    outputs = run.get("outputs") or {}
    if not isinstance(outputs, dict):
        outputs = {}

    nested = outputs.get("output")
    if isinstance(nested, dict):
        outputs = {**outputs, **nested}

    evidence_id_pattern = re.compile(r"\bE\d+\b")
    all_evidence_ids = set()

    evidence_items = outputs.get("evidence_items") or {}
    if isinstance(evidence_items, dict):
        for evidence_id in evidence_items.keys():
            if evidence_id_pattern.fullmatch(str(evidence_id)):
                all_evidence_ids.add(str(evidence_id))

    evidence_matrix = outputs.get("evidence_matrix") or {}
    if isinstance(evidence_matrix, dict):
        for row in evidence_matrix.values():
            if not isinstance(row, dict):
                continue
            for evidence_id in row.get("evidence_ids") or []:
                if evidence_id_pattern.fullmatch(str(evidence_id)):
                    all_evidence_ids.add(str(evidence_id))

    entity_index = outputs.get("entity_index") or {}
    if isinstance(entity_index, dict):
        by_q = entity_index.get("evidence_ids_by_question_id") or {}
        if isinstance(by_q, dict):
            for ids in by_q.values():
                if not isinstance(ids, list):
                    continue
                for evidence_id in ids:
                    if evidence_id_pattern.fullmatch(str(evidence_id)):
                        all_evidence_ids.add(str(evidence_id))

    final_report = outputs.get("final_report") or ""
    if not isinstance(final_report, str):
        final_report = str(final_report)

    used_in_report = all_evidence_ids & set(evidence_id_pattern.findall(final_report))
    total = len(all_evidence_ids)
    rate = (len(used_in_report) / total) if total > 0 else 0.0

    return {"evidence_utilization_rate": round(rate, 4)}