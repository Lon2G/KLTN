"""Literal Portuguese review retrieval, not complaint labels or model truth."""

import json
import re
import unicodedata

import pandas as pd

PATTERNS = {
    "delivery_topic": r"\b(?:entreg\w*|receb\w*|cheg\w*|prazo\w*|correios|transportador\w*)\b",
    "delay_wording": r"\b(?:atras\w*|demor\w*)\b",
    "nonreceipt_wording": r"\bnao\s+(?:(?:o|a|os|as|ainda)\s+)?(?:recebi|recebemos|recebeu|chegou|chegaram|foi entregue|foram entregues)\b",
    "on_time_or_early_wording": r"\b(?:antes do prazo|antes da data|dentro do prazo|no prazo|adiantad\w*|antecipad\w*|rapid[oa]s?)\b",
    "negated_delay_wording": r"\b(?:sem\s+(?:atras\w*|demor\w*)|nao\s+(?:houve\s+)?(?:atras\w*|demor\w*))\b",
}


def normalize(text):
    text = "" if pd.isna(text) else str(text)
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def screen(title, message):
    fields = {"title": normalize(title), "message": normalize(message)}
    hits = [{"field": field, "kind": kind, "normalized_match": match.group()}
            for field, value in fields.items() for kind, pattern in PATTERNS.items()
            for match in re.finditer(pattern, value)]
    flags = {kind: any(hit["kind"] == kind for hit in hits) for kind in PATTERNS}
    flags["mixed_wording"] = ((flags["delay_wording"] or flags["nonreceipt_wording"])
                              and (flags["on_time_or_early_wording"] or flags["negated_delay_wording"]))
    return {**flags, "literal_matches_json": json.dumps(hits, ensure_ascii=False),
            "wording_status": "literal_matches_need_reading" if hits else "no_listed_phrase_not_a_negative_label"}


def enrich_reviews(source, orders):
    if source.clean_source_record.isna().any() or not source.clean_source_record.is_unique:
        raise ValueError("Review records need distinct source locators")
    if not set(source.order_id).issubset(orders.order_id):
        raise ValueError("Review outside the frozen test scope")
    rows = source.sort_values(["order_id", "clean_source_record"]).reset_index(drop=True).merge(
        orders[["order_id", "order_purchase_timestamp", "order_delivered_customer_date", "order_estimated_delivery_date"]],
        on="order_id", how="left", validate="many_to_one")
    rows["has_text"] = (rows.review_comment_title.fillna("").str.strip().ne("")
                        | rows.review_comment_message.fillna("").str.strip().ne(""))
    rows["valid_review_dates"] = (rows.review_creation_date.ge(rows.order_purchase_timestamp)
                                  & rows.review_answer_timestamp.ge(rows.review_creation_date)).fillna(False)
    rows["score_usable"] = rows.valid_review_dates & rows.review_score.between(1, 5).fillna(False)
    rows["text_usable"] = rows.valid_review_dates & rows.has_text
    rows["answered_before_recorded_delivery"] = rows.review_answer_timestamp.lt(rows.order_delivered_customer_date).astype("boolean").where(
        rows.review_answer_timestamp.notna() & rows.order_delivered_customer_date.notna())
    rows["answered_after_promised_calendar_date"] = rows.review_answer_timestamp.dt.normalize().gt(rows.order_estimated_delivery_date.dt.normalize()).astype("boolean").where(
        rows.review_answer_timestamp.notna() & rows.order_estimated_delivery_date.notna())
    matches = pd.DataFrame([screen(row.review_comment_title, row.review_comment_message) for row in rows.itertuples(index=False)],
                           columns=[*PATTERNS, "mixed_wording", "literal_matches_json", "wording_status"])
    rows = pd.concat([rows, matches], axis=1)
    rows["evidence_locator"] = "olist_order_reviews_dataset.csv:clean_record="+rows.clean_source_record.astype(str)
    rows["evidence_status"] = "retrospective_customer_statement_not_verified_cause_or_label"
    return rows


def aggregate_reviews(reviews, order_ids):
    grouped = {key: group for key, group in reviews.groupby("order_id", sort=True)}
    records = []
    for oid in sorted(order_ids):
        group = grouped.get(oid, reviews.iloc[:0])
        scores, text = group.loc[group.score_usable], group.loc[group.text_usable]
        row = {"order_id": oid, "review_records": len(group), "review_text_records": int(group.has_text.sum()),
               "usable_score_records": len(scores), "usable_text_records": len(text),
               "invalid_or_missing_review_date_records": int((~group.valid_review_dates).sum()),
               "review_score_min": scores.review_score.min(), "review_score_max": scores.review_score.max(),
               "multiple_different_scores": bool(scores.review_score.nunique() > 1),
               "any_low_score_1_2": bool(scores.review_score.le(2).any()) if len(scores) else pd.NA,
               "has_usable_text": bool(len(text)),
               "review_evidence_status": "no_source_review" if group.empty else "usable_text" if len(text) else
                   "text_with_invalid_or_missing_dates" if group.has_text.any() else "no_written_comment",
               "review_source_records_json": json.dumps(group.clean_source_record.tolist()),
               "review_answer_first": group.review_answer_timestamp.min(), "review_answer_last": group.review_answer_timestamp.max()}
        for field in [*PATTERNS, "mixed_wording"]:
            row["any_"+field] = bool(text[field].any()) if len(text) else pd.NA
        row["any_delay_or_nonreceipt_wording"] = bool((text.delay_wording | text.nonreceipt_wording).any()) if len(text) else pd.NA
        records.append(row)
    result = pd.DataFrame(records)
    for field in ["review_score_min", "review_score_max"]:
        result[field] = result[field].astype("Int64")
    for field in ["any_low_score_1_2", *["any_"+name for name in [*PATTERNS, "mixed_wording"]], "any_delay_or_nonreceipt_wording"]:
        result[field] = result[field].astype("boolean")
    return result
