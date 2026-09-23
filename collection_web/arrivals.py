"""Review additions to an existing Drift shelf without changing its identity."""
from copy import deepcopy
from datetime import datetime, timezone

from .curation import (BATCH_REVIEW_PROMPT, BATCH_REVIEW_SCHEMA, LOCKED_REVIEW_PROMPT, LOCKED_REVIEW_SCHEMA,
                       MIN_REVIEW_SCORE, _call, _facts, _fingerprint, _number, _proof_facts, _text,
                       validate_candidate)
from .store import DomainError


def review_drift_additions(source, replacement, state, llm):
    if validate_candidate(source, state["library"]):
        raise DomainError("This Drift shelf needs a fresh review before new titles can join it.")
    original_ids = {str(row["id"]) for row in source["items"]}
    ids = {str(row["id"]) for row in replacement["items"]}
    library = {str(row["id"]): row for row in state["library"]}
    if (not original_ids < ids or len(ids) != len(replacement["items"])
            or any(str(row["id"]) not in library or _proof_facts(row, source) != _proof_facts(library[str(row["id"])], source)
                   for row in replacement["items"])):
        raise DomainError("The additions are not verified against the current Plex library.")
    result = deepcopy(replacement)
    public = {key: result.get(key) for key in ("id", "name", "concept", "thesis", "source_family", "media_type",
                                              "minimum_owned_hits", "size_reason", "entity_axis", "entity_name", "scoped")}
    public["inclusion_basis"] = ("verified_entity_credit" if result["source_family"] in
                                 {"person_creator", "franchise", "studio_label"} and not result.get("scoped")
                                 else "stated_narrative_scope")
    public["items"] = [_facts(row) for row in result["items"]]
    try:
        review = _call(llm, LOCKED_REVIEW_PROMPT + "\nThis is an ARRIVAL REVIEW of an existing shelf. Keep its supplied name and thesis EXACTLY. Judge every item against that existing rule; reject additions that require changing the rule or name. Do not rename or drop existing items.",
                       LOCKED_REVIEW_SCHEMA, {"stage": "locked_review", "candidate": public, "new_ids": sorted(ids - original_ids)})
    except Exception:
        raise DomainError("The Drift arrival review could not finish. The original shelf is unchanged.") from None
    reviews = review.get("item_reviews", [])
    if (review.get("approved") is not True or review.get("name") != source["name"] or review.get("thesis") != source["thesis"]
            or any(not MIN_REVIEW_SCORE <= _number(review.get(key)) <= 10 for key in ("coherence", "quality", "originality"))
            or not isinstance(reviews, list) or len(reviews) != len(ids)
            or any(not isinstance(row, dict) or not MIN_REVIEW_SCORE <= _number(row.get("fit")) <= 10
                   or not _text(row.get("reason")) for row in reviews)
            or {str(row.get("id")) for row in reviews} != ids):
        raise DomainError("The new titles did not pass the Drift theme review. They remain available to review; the shelf is unchanged.")
    others = [row for row in state["collections"] if row.get("origin") == "drift" and row.get("status") != "archived" and row["id"] != source["id"]]
    batch = [result, *others]
    editorial = [{key: row.get(key) for key in ("id", "name", "thesis", "source_family", "media_type")} |
                 {"items": [_facts(item, False) for item in row["items"]]} for row in batch]
    try:
        edited = _call(llm, BATCH_REVIEW_PROMPT + "\nAn existing shelf has new arrivals. Check it against the current batch; no shelf can be removed or renamed during this operation. Reject if the additions compromise the batch.",
                       BATCH_REVIEW_SCHEMA, {"stage": "batch_review", "candidates": editorial, "minimum_batch_size": len(batch), "target_batch_size": len(batch)})
    except Exception:
        raise DomainError("The Drift batch check could not finish. The original shelf is unchanged.") from None
    keep = edited.get("keep_ids")
    if (edited.get("approved") is not True or not isinstance(keep, list)
            or len(keep) != len(batch) or set(keep) != {row["id"] for row in batch} or not _text(edited.get("reason"))):
        raise DomainError("The additions did not pass the Drift batch check. The original shelf is unchanged.")
    now = datetime.now(timezone.utc).isoformat()
    result["review"].update(approved=True, reviewed_at=now,
                            **{key: _number(review[key]) for key in ("coherence", "quality", "originality")},
                            item_reviews=[{"id": str(row["id"]), "fit": _number(row["fit"]), "reason": _text(row["reason"], 500)} for row in reviews],
                            batch={"approved": True, "keep_ids": keep, "reason": _text(edited["reason"]), "reviewed_at": now})
    result["review"]["fingerprint"] = _fingerprint(result)
    if validate_candidate(result, state["library"]):
        raise DomainError("The reviewed additions could not be verified. The original shelf is unchanged.")
    return result
