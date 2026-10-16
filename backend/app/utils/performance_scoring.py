"""
Turning a completed review into a number, and a form into something the
frontend can render without knowing what a competency is.

The score exists so that people can be compared — for an increment, for a
promotion, for employee of the month — which is exactly why it has to be built
from stated weights on a stated scale rather than from a manager's overall
impression. An impression is still in there, in every rating; the arithmetic
just stops it being the only thing.
"""
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.performance import (Goal, GoalStatusEnum, Review, ReviewTemplate)
from app.models.user import User
from app.utils.review_templates import RATING_SCALE


def template_for(db: Session, designation: Optional[str]) -> Optional[ReviewTemplate]:
    """
    The form this role is reviewed on.

    Named match first, then whichever template is marked default. A designation
    nobody has written a template for still gets a review rather than an error.
    """
    templates = (db.query(ReviewTemplate)
                 .filter(ReviewTemplate.is_active.is_(True)).all())
    if designation:
        for t in templates:
            if designation in (t.designations or []):
                return t
    for t in templates:
        if t.is_default:
            return t
    return templates[0] if templates else None


def _weighted_mean(pairs) -> Optional[float]:
    """(value, weight) pairs to a mean. Equal weights when none are set."""
    pairs = [(v, w) for v, w in pairs if v is not None]
    if not pairs:
        return None
    total_weight = sum(w for _, w in pairs)
    if total_weight <= 0:
        return round(sum(v for v, _ in pairs) / len(pairs), 2)
    return round(sum(v * w for v, w in pairs) / total_weight, 2)


def goals_for(db: Session, review: Review) -> List[Goal]:
    return (db.query(Goal)
            .filter(Goal.employee_id == review.employee_id,
                    Goal.cycle_id == review.cycle_id)
            .order_by(Goal.created_at).all())


def score_review(db: Session, review: Review,
                 template: Optional[ReviewTemplate] = None) -> dict:
    """
    Compute and store the three scores. Returns the working, not just the answer.

    Only the manager's ratings count towards the score. The employee's ratings
    are kept and shown, because the gap between the two is one of the more
    useful things on the page — but a self-assessment that scored itself is not
    an assessment.
    """
    template = template or (
        db.query(ReviewTemplate).filter(ReviewTemplate.id == review.template_id).first()
        if review.template_id else None)

    # --- goals ---------------------------------------------------------
    goals = [g for g in goals_for(db, review) if g.status != GoalStatusEnum.DROPPED]
    goal_score = _weighted_mean([(g.rating, float(g.weight or 0)) for g in goals])
    rated_goals = sum(1 for g in goals if g.rating is not None)

    # --- competencies --------------------------------------------------
    competencies = (template.competencies if template else []) or []
    manager_scores = review.manager_scores or {}
    self_scores = review.self_scores or {}

    detail = []
    manager_pairs, self_pairs = [], []
    for c in competencies:
        key, weight = c.get("key"), float(c.get("weight") or 0)
        m = (manager_scores.get(key) or {}).get("rating")
        s = (self_scores.get(key) or {}).get("rating")
        manager_pairs.append((m, weight))
        self_pairs.append((s, weight))
        detail.append({
            "key": key, "title": c.get("title"), "weight": weight,
            "manager_rating": m, "self_rating": s,
            "gap": round(s - m, 2) if (s is not None and m is not None) else None,
            "manager_comment": (manager_scores.get(key) or {}).get("comment"),
            "self_comment": (self_scores.get(key) or {}).get("comment"),
        })

    competency_score = _weighted_mean(manager_pairs)
    self_competency_score = _weighted_mean(self_pairs)

    # --- combine -------------------------------------------------------
    gw = float(template.goal_weight if template else 60.0)
    cw = float(template.competency_weight if template else 40.0)
    parts = []
    if goal_score is not None:
        parts.append((goal_score, gw))
    if competency_score is not None:
        parts.append((competency_score, cw))
    # Renormalised rather than penalised: a review with no goals set is scored
    # on competencies alone, not scored as though the goals were all zero.
    final = _weighted_mean(parts)

    review.goal_score = goal_score
    review.competency_score = competency_score
    review.final_score = final

    return {
        "goal_score": goal_score,
        "competency_score": competency_score,
        "self_competency_score": self_competency_score,
        "final_score": final,
        "goal_weight": gw,
        "competency_weight": cw,
        "goals_rated": rated_goals,
        "goals_total": len(goals),
        "competencies_rated": sum(1 for d in detail if d["manager_rating"] is not None),
        "competencies_total": len(detail),
        "competency_detail": detail,
        # Positive means the employee rated themselves above their manager.
        "self_assessment_gap": (round(self_competency_score - competency_score, 2)
                                if (self_competency_score is not None
                                    and competency_score is not None) else None),
        "band": band_for(final),
    }


def band_for(score: Optional[float]) -> Optional[str]:
    """The word that goes with the number, from the same scale as the ratings."""
    if score is None:
        return None
    for entry in reversed(RATING_SCALE):
        if score >= entry["value"] - 0.5:
            return entry["label"]
    return RATING_SCALE[0]["label"]


def build_form(db: Session, review: Review, template: Optional[ReviewTemplate],
               viewer: User, is_manager: bool) -> dict:
    """
    The whole form, with every field labelled by who fills it and whether the
    person asking can fill it right now.

    Built server-side so the frontend never has to encode the rules about who
    answers what and when — the same rules the write endpoints enforce.
    """
    from app.models.performance import ReviewStatusEnum

    is_subject = viewer.id == review.employee_id
    shared = review.status in (ReviewStatusEnum.SHARED, ReviewStatusEnum.CLOSED)
    employee_can_edit = is_subject and review.status in (
        ReviewStatusEnum.DRAFT, ReviewStatusEnum.SELF_SUBMITTED)
    manager_can_edit = is_manager and review.status in (
        ReviewStatusEnum.SELF_SUBMITTED, ReviewStatusEnum.MANAGER_SUBMITTED)

    self_scores = review.self_scores or {}
    manager_scores = review.manager_scores or {}
    self_sections = review.self_sections or {}
    manager_sections = review.manager_sections or {}

    def visible_manager_value(value):
        # Nothing of the manager's reaches the employee before it is shared.
        return None if (is_subject and not shared and not is_manager) else value

    competencies = []
    for c in (template.competencies if template else []):
        key = c.get("key")
        competencies.append({
            **{k: c.get(k) for k in ("key", "title", "description", "weight", "rated_by")},
            "self": {
                "rating": (self_scores.get(key) or {}).get("rating"),
                "comment": (self_scores.get(key) or {}).get("comment"),
                "editable": employee_can_edit,
            },
            "manager": {
                "rating": visible_manager_value((manager_scores.get(key) or {}).get("rating")),
                "comment": visible_manager_value((manager_scores.get(key) or {}).get("comment")),
                "editable": manager_can_edit,
            },
        })

    sections = []
    for s in (template.sections if template else []):
        by_employee = s.get("rated_by") == "employee"
        raw = (self_sections if by_employee else manager_sections).get(s.get("key"))
        sections.append({
            **{k: s.get(k) for k in ("key", "title", "prompt", "rated_by", "required")},
            "value": raw if by_employee else visible_manager_value(raw),
            "editable": employee_can_edit if by_employee else manager_can_edit,
        })

    goals = []
    for g in goals_for(db, review):
        goals.append({
            "id": g.id, "title": g.title, "description": g.description,
            "target": g.target, "weight": g.weight, "source": g.source.value,
            "status": g.status.value,
            "employee_comment": g.employee_comment,
            "rating": visible_manager_value(g.rating),
            "rating_comment": visible_manager_value(g.rating_comment),
            "employee_can_comment": employee_can_edit,
            "manager_can_rate": manager_can_edit,
        })

    return {
        "review_id": review.id,
        "status": review.status.value,
        "template_name": template.name if template else None,
        "template_id": template.id if template else None,
        "rating_scale": RATING_SCALE,
        "goal_weight": float(template.goal_weight) if template else 60.0,
        "competency_weight": float(template.competency_weight) if template else 40.0,
        "your_part": ("employee" if is_subject else "manager" if is_manager else "viewer"),
        "you_can_edit": employee_can_edit or manager_can_edit,
        "waiting_on": _waiting_on(review),
        "competencies": competencies,
        "sections": sections,
        "goals": goals,
    }


def _waiting_on(review: Review) -> str:
    from app.models.performance import ReviewStatusEnum
    return {
        ReviewStatusEnum.DRAFT: "the employee's self-assessment",
        ReviewStatusEnum.SELF_SUBMITTED: "the manager's review",
        ReviewStatusEnum.MANAGER_SUBMITTED: "the manager to share it",
        ReviewStatusEnum.SHARED: "the employee to acknowledge it",
        ReviewStatusEnum.CLOSED: "nothing — this review is complete",
    }.get(review.status, "")
