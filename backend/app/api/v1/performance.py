"""
Performance: goals, review cycles, appraisals and one-to-ones.

The sequence is the design. An employee writes their own assessment before
seeing the manager's, the manager writes before anything is shared, and nothing
reaches the employee until someone deliberately shares it. Each of those is
enforced here rather than left to the interface.
"""
import calendar
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from app.api.deps import (get_current_user, is_privileged, require_admin,
                          require_admin_or_hr)
from app.database import get_db
from app.models.document import JobProfile
from app.models.notification import (Notification, NotificationTypeEnum,
                                     RelatedTypeEnum)
from app.models.performance import (AwardNomination, AwardStatusEnum,
                                    CycleStatusEnum, Goal, GoalSourceEnum,
                                    GoalStatusEnum, MonthlyAward, OneOnOne,
                                    RecommendationEnum, Review, ReviewCycle,
                                    ReviewStatusEnum, ReviewTemplate)
from app.models.user import RoleEnum, User
from app.utils import audit
from app.utils.notifications import create_notification
from app.utils.performance_scoring import (band_for, build_form, goals_for,
                                           score_review, template_for)
from app.utils.review_templates import RATING_SCALE

router = APIRouter()


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _manages(db: Session, actor: User, employee_id: int) -> bool:
    employee = db.query(User).filter(User.id == employee_id).first()
    return bool(employee and employee.manager_id == actor.id)


def _may_see(db: Session, actor: User, employee_id: int) -> bool:
    return (actor.id == employee_id or _manages(db, actor, employee_id)
            or is_privileged(actor, db))


def _require_visible(db: Session, actor: User, employee_id: int) -> None:
    if not _may_see(db, actor, employee_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")


def _merge_scores(existing: dict, incoming: dict, template, wanted: str) -> dict:
    """
    Fold submitted ratings into what is already there, checking as we go.

    Unknown keys and out-of-range ratings are refused rather than stored: a
    rating of 47 against a competency that does not exist would sail through
    into the score and quietly distort it.
    """
    valid = {c["key"] for c in ((template.competencies if template else []) or [])}
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if valid and key not in valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{key}' is not a competency on this review's form.")
        rating = value.get("rating")
        if rating is not None and not 1 <= float(rating) <= 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A rating is 1 to 5. '{key}' got {rating}.")
        entry = dict(merged.get(key) or {})
        if rating is not None:
            entry["rating"] = float(rating)
        if "comment" in value:
            entry["comment"] = value.get("comment")
        merged[key] = entry
    return merged


def _merge_sections(existing: dict, incoming: dict, template, side: str) -> dict:
    valid = {sec["key"] for sec in ((template.sections if template else []) or [])
             if sec.get("rated_by") == side}
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if valid and key not in valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{key}' is not a section the {side} fills on this form.")
        merged[key] = value
    return merged


def _require_something(narrative, scores, sections, what: str) -> None:
    """Refuse an empty submission. A blank review is not a completed one."""
    if not (narrative or "").strip() and not (scores or {}) and not any(
            (v or "").strip() for v in (sections or {}).values() if isinstance(v, str)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"There is nothing in this {what} yet. Fill it in before submitting.")


def _require_required_sections(template, filled: dict, side: str) -> None:
    missing = [sec["title"] for sec in ((template.sections if template else []) or [])
               if sec.get("rated_by") == side and sec.get("required")
               and not (filled or {}).get(sec["key"], "").strip()]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Still to fill in: " + ", ".join(missing))


# --------------------------------------------------------------------------
# Review cycles
# --------------------------------------------------------------------------

class CycleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    period_start: date
    period_end: date
    self_assessment_due: Optional[date] = None
    manager_review_due: Optional[date] = None
    instructions: Optional[str] = None


class CycleOut(BaseModel):
    id: int
    name: str
    period_start: date
    period_end: date
    self_assessment_due: Optional[date] = None
    manager_review_due: Optional[date] = None
    status: CycleStatusEnum
    instructions: Optional[str] = None
    review_count: int = 0
    completed_count: int = 0

    class Config:
        from_attributes = True


def _cycle_out(db: Session, c: ReviewCycle) -> CycleOut:
    o = CycleOut.model_validate(c, from_attributes=True)
    reviews = db.query(Review).filter(Review.cycle_id == c.id).all()
    o.review_count = len(reviews)
    o.completed_count = sum(1 for r in reviews if r.status in
                            (ReviewStatusEnum.SHARED, ReviewStatusEnum.CLOSED))
    return o


@router.get("/cycles", response_model=List[CycleOut])
def list_cycles(db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """Draft cycles are HR's business until they open."""
    q = db.query(ReviewCycle)
    if not is_privileged(current_user, db):
        q = q.filter(ReviewCycle.status != CycleStatusEnum.DRAFT)
    return [_cycle_out(db, c) for c in q.order_by(ReviewCycle.period_start.desc()).all()]


@router.post("/cycles", response_model=CycleOut, status_code=status.HTTP_201_CREATED)
def create_cycle(payload: CycleIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    if payload.period_end <= payload.period_start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The period must end after it starts")
    cycle = ReviewCycle(**payload.model_dump(), created_by=current_user.id)
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    audit.record(db, "review_cycle.created", actor=current_user, request=request,
                 target_type="review_cycle", target_id=cycle.id,
                 detail={"name": cycle.name})
    return _cycle_out(db, cycle)


@router.post("/cycles/{cycle_id}/open", response_model=CycleOut)
def open_cycle(cycle_id: int, request: Request,
               include_probationers: bool = False,
               db: Session = Depends(get_db),
               current_user: User = Depends(require_admin_or_hr)):
    """
    Open the cycle and raise one review per active employee.

    Everyone gets a row now rather than on first use: a review that only exists
    once somebody clicks is a review that never appears on anyone's list.
    """
    cycle = db.query(ReviewCycle).filter(ReviewCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    if cycle.status == CycleStatusEnum.CLOSED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This cycle is closed")

    from app.utils.probation import current_status
    from app.models.probation import ProbationStatusEnum

    created = 0
    for employee in db.query(User).filter(User.is_active.is_(True)).all():
        if getattr(employee, "attendance_exempt", False) and not employee.manager_id:
            # Payroll-only people have no manager and no appraisal.
            continue
        if not include_probationers and current_status(employee) == \
                ProbationStatusEnum.IN_PROBATION.value:
            continue
        existing = db.query(Review).filter(Review.cycle_id == cycle.id,
                                           Review.employee_id == employee.id).first()
        if existing:
            continue
        template = template_for(db, employee.designation)
        db.add(Review(cycle_id=cycle.id, employee_id=employee.id,
                      manager_id=employee.manager_id,
                      template_id=template.id if template else None))
        created += 1
        create_notification(
            db, employee.id, f"Performance review open: {cycle.name}",
            "Your self-assessment is ready to complete in the portal."
            + (f" It is due by {cycle.self_assessment_due.strftime('%d %B %Y')}."
               if cycle.self_assessment_due else ""),
            NotificationTypeEnum.INFO, related_id=cycle.id)

    cycle.status = CycleStatusEnum.OPEN
    db.commit()
    db.refresh(cycle)
    audit.record(db, "review_cycle.opened", actor=current_user, request=request,
                 target_type="review_cycle", target_id=cycle.id,
                 detail={"reviews_created": created})
    return _cycle_out(db, cycle)


@router.post("/cycles/{cycle_id}/close", response_model=CycleOut)
def close_cycle(cycle_id: int, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(require_admin_or_hr)):
    cycle = db.query(ReviewCycle).filter(ReviewCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    cycle.status = CycleStatusEnum.CLOSED
    db.commit()
    db.refresh(cycle)
    audit.record(db, "review_cycle.closed", actor=current_user, request=request,
                 target_type="review_cycle", target_id=cycle.id)
    return _cycle_out(db, cycle)


# --------------------------------------------------------------------------
# Goals
# --------------------------------------------------------------------------

class GoalIn(BaseModel):
    employee_id: int
    cycle_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=300)
    description: Optional[str] = None
    weight: float = Field(0.0, ge=0, le=100)
    target: Optional[str] = Field(None, max_length=300)
    source: GoalSourceEnum = GoalSourceEnum.MANAGER


class GoalUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    description: Optional[str] = None
    weight: Optional[float] = Field(None, ge=0, le=100)
    target: Optional[str] = Field(None, max_length=300)
    status: Optional[GoalStatusEnum] = None
    rating: Optional[float] = Field(None, ge=1, le=5)
    rating_comment: Optional[str] = None
    employee_comment: Optional[str] = None


class GoalOut(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    cycle_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    source: GoalSourceEnum
    weight: float
    target: Optional[str] = None
    status: GoalStatusEnum
    rating: Optional[float] = None
    rating_comment: Optional[str] = None
    employee_comment: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


def _goal_out(g: Goal) -> GoalOut:
    o = GoalOut.model_validate(g, from_attributes=True)
    o.employee_name = g.employee.full_name if g.employee else None
    return o


@router.get("/goals", response_model=List[GoalOut])
def list_goals(employee_id: Optional[int] = None, cycle_id: Optional[int] = None,
               db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    target = employee_id or current_user.id
    _require_visible(db, current_user, target)
    q = (db.query(Goal).options(joinedload(Goal.employee))
         .filter(Goal.employee_id == target))
    if cycle_id is not None:
        q = q.filter(Goal.cycle_id == cycle_id)
    return [_goal_out(g) for g in q.order_by(Goal.created_at).all()]


@router.post("/goals", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
def create_goal(payload: GoalIn, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """
    Anyone may add a goal for themselves; a manager or HR may add one for
    someone they are responsible for.
    """
    if payload.employee_id != current_user.id and not (
            _manages(db, current_user, payload.employee_id)
            or is_privileged(current_user, db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    source = (GoalSourceEnum.EMPLOYEE if payload.employee_id == current_user.id
              else payload.source)
    goal = Goal(**{**payload.model_dump(), "source": source},
                created_by=current_user.id)
    db.add(goal)
    db.commit()
    db.refresh(goal)
    audit.record(db, "goal.created", actor=current_user, request=request,
                 target_type="goal", target_id=goal.id,
                 detail={"employee_id": goal.employee_id, "title": goal.title})
    return _goal_out(goal)


@router.post("/goals/seed-from-profile", response_model=List[GoalOut],
             status_code=status.HTTP_201_CREATED)
def seed_goals(employee_id: int, cycle_id: Optional[int] = None,
               request: Request = None, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """
    Turn the KPIs on someone's job profile into goals for a cycle.

    This is the join that makes the whole module worth having: the KPIs come
    from the job profile their signed contract quotes, so nobody is measured
    against a standard they never agreed to. Already-seeded KPIs are skipped,
    so running it twice adds nothing.
    """
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if not (_manages(db, current_user, employee_id) or is_privileged(current_user, db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if not employee.designation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This employee has no designation, so there is no job profile to read")

    profile = (db.query(JobProfile)
               .filter(JobProfile.designation == employee.designation).first())
    if not profile or not profile.kpis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No KPIs are recorded on the job profile for '{employee.designation}'")

    existing = {g.title for g in db.query(Goal).filter(
        Goal.employee_id == employee_id, Goal.cycle_id == cycle_id).all()}
    kpis = [k for k in profile.kpis if isinstance(k, str) and k.strip()]
    fresh = [k.strip() for k in kpis if k.strip() not in existing]
    # An even split is a starting point, not a verdict — the manager reweights.
    weight = round(100.0 / len(kpis), 2) if kpis else 0.0

    created = []
    for kpi in fresh:
        goal = Goal(employee_id=employee_id, cycle_id=cycle_id, title=kpi,
                    source=GoalSourceEnum.JOB_PROFILE, weight=weight,
                    created_by=current_user.id)
        db.add(goal)
        created.append(goal)
    db.commit()
    for g in created:
        db.refresh(g)
    if request is not None:
        audit.record(db, "goal.seeded_from_profile", actor=current_user, request=request,
                     target_type="user", target_id=employee_id,
                     detail={"designation": employee.designation, "created": len(created)})
    return [_goal_out(g) for g in created]


@router.put("/goals/{goal_id}", response_model=GoalOut)
def update_goal(goal_id: int, payload: GoalUpdate, request: Request,
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    goal = (db.query(Goal).options(joinedload(Goal.employee))
            .filter(Goal.id == goal_id).first())
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    changes = payload.model_dump(exclude_unset=True)

    is_owner = goal.employee_id == current_user.id
    can_assess = _manages(db, current_user, goal.employee_id) or is_privileged(current_user, db)
    if not (is_owner or can_assess):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    # The person being measured does not set their own rating.
    if is_owner and not can_assess:
        for field in ("rating", "rating_comment", "weight", "status"):
            changes.pop(field, None)

    for field, value in changes.items():
        setattr(goal, field, value)
    db.commit()
    db.refresh(goal)
    audit.record(db, "goal.updated", actor=current_user, request=request,
                 target_type="goal", target_id=goal.id, detail=changes)
    return _goal_out(goal)


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(goal_id: int, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    goal = db.query(Goal).filter(Goal.id == goal_id).first()
    if not goal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    if goal.rating is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This goal has been rated. Set it to dropped rather than deleting it.")
    if not (_manages(db, current_user, goal.employee_id)
            or is_privileged(current_user, db)
            or (goal.employee_id == current_user.id
                and goal.source == GoalSourceEnum.EMPLOYEE)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    db.delete(goal)
    db.commit()
    audit.record(db, "goal.deleted", actor=current_user, request=request,
                 target_type="goal", target_id=goal_id)


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------

class SelfAssessmentIn(BaseModel):
    """
    The employee's half of the form.

    `scores` is keyed by competency: {"technical_quality": {"rating": 4,
    "comment": "..."}}. `sections` is keyed by section: {"achievements": "..."}.
    `goal_comments` lets them say something against each of their own goals
    without rating themselves on it.
    """
    self_assessment: Optional[str] = None
    self_rating: Optional[float] = Field(None, ge=1, le=5)
    scores: Optional[Dict[str, Dict[str, Any]]] = None
    sections: Optional[Dict[str, Optional[str]]] = None
    goal_comments: Optional[Dict[int, str]] = None
    submit: bool = True


class ManagerReviewIn(BaseModel):
    """The manager's half. `goal_ratings` is {goal_id: {rating, comment}}."""
    manager_assessment: Optional[str] = None
    overall_rating: Optional[float] = Field(None, ge=1, le=5)
    strengths: Optional[str] = None
    development_areas: Optional[str] = None
    calibration_note: Optional[str] = None
    recommendation: Optional[RecommendationEnum] = None
    scores: Optional[Dict[str, Dict[str, Any]]] = None
    sections: Optional[Dict[str, Optional[str]]] = None
    goal_ratings: Optional[Dict[int, Dict[str, Any]]] = None
    submit: bool = True


class AcknowledgeIn(BaseModel):
    employee_comment: Optional[str] = None


class ReviewOut(BaseModel):
    id: int
    cycle_id: int
    cycle_name: Optional[str] = None
    employee_id: int
    employee_name: Optional[str] = None
    manager_id: Optional[int] = None
    manager_name: Optional[str] = None
    status: ReviewStatusEnum
    self_assessment: Optional[str] = None
    self_rating: Optional[float] = None
    self_submitted_at: Optional[datetime] = None
    manager_assessment: Optional[str] = None
    overall_rating: Optional[float] = None
    strengths: Optional[str] = None
    development_areas: Optional[str] = None
    manager_submitted_at: Optional[datetime] = None
    shared_at: Optional[datetime] = None
    employee_comment: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    calibration_note: Optional[str] = None
    template_id: Optional[int] = None
    template_name: Optional[str] = None
    recommendation: Optional[RecommendationEnum] = None
    goal_score: Optional[float] = None
    competency_score: Optional[float] = None
    final_score: Optional[float] = None
    band: Optional[str] = None
    goals: List[GoalOut] = Field(default_factory=list)

    class Config:
        from_attributes = True


def _review_out(db: Session, r: Review, viewer: User) -> ReviewOut:
    o = ReviewOut.model_validate(r, from_attributes=True)
    o.cycle_name = r.cycle.name if r.cycle else None
    o.employee_name = r.employee.full_name if r.employee else None
    o.manager_name = r.manager.full_name if r.manager else None
    o.goals = [_goal_out(g) for g in db.query(Goal).options(joinedload(Goal.employee))
               .filter(Goal.employee_id == r.employee_id,
                       Goal.cycle_id == r.cycle_id).order_by(Goal.created_at).all()]

    o.band = band_for(r.final_score)
    if r.template_id:
        tpl = db.query(ReviewTemplate).filter(ReviewTemplate.id == r.template_id).first()
        o.template_name = tpl.name if tpl else None

    is_subject = viewer.id == r.employee_id
    if is_subject:
        # Calibration notes are for the people running the cycle, never for the
        # person being calibrated.
        o.calibration_note = None
        # Nothing of the manager's is visible until it is deliberately shared.
        if r.status not in (ReviewStatusEnum.SHARED, ReviewStatusEnum.CLOSED):
            o.manager_assessment = None
            o.overall_rating = None
            o.strengths = None
            o.development_areas = None
            o.recommendation = None
            o.goal_score = o.competency_score = o.final_score = None
            o.band = None
            o.goals = [g.model_copy(update={"rating": None, "rating_comment": None})
                       for g in o.goals]
    return o


def _load_review(db: Session, review_id: int) -> Review:
    r = (db.query(Review)
         .options(joinedload(Review.cycle), joinedload(Review.employee),
                  joinedload(Review.manager))
         .filter(Review.id == review_id).first())
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    return r


@router.get("/reviews", response_model=List[ReviewOut])
def list_reviews(cycle_id: Optional[int] = None,
                 scope: str = Query("mine", pattern="^(mine|team|all)$"),
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    q = (db.query(Review).options(joinedload(Review.cycle), joinedload(Review.employee),
                                  joinedload(Review.manager)))
    if cycle_id is not None:
        q = q.filter(Review.cycle_id == cycle_id)
    if scope == "mine":
        q = q.filter(Review.employee_id == current_user.id)
    elif scope == "team":
        q = q.filter(Review.manager_id == current_user.id)
    else:
        if not is_privileged(current_user, db):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Only HR and administrators see every review")
    return [_review_out(db, r, current_user)
            for r in q.order_by(Review.cycle_id.desc(), Review.id).all()]


@router.get("/reviews/{review_id}", response_model=ReviewOut)
def get_review(review_id: int, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    r = _load_review(db, review_id)
    if not (current_user.id in (r.employee_id, r.manager_id)
            or is_privileged(current_user, db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    return _review_out(db, r, current_user)


@router.put("/reviews/{review_id}/self", response_model=ReviewOut)
def write_self_assessment(review_id: int, payload: SelfAssessmentIn, request: Request,
                          db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    r = _load_review(db, review_id)
    if r.employee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the employee writes their own self-assessment")
    if r.status not in (ReviewStatusEnum.DRAFT, ReviewStatusEnum.SELF_SUBMITTED):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Your manager has already started their review")
    if r.cycle and r.cycle.status != CycleStatusEnum.OPEN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This review cycle is not open")

    template = (db.query(ReviewTemplate).filter(ReviewTemplate.id == r.template_id).first()
                if r.template_id else None)
    if payload.self_assessment is not None:
        r.self_assessment = payload.self_assessment
    if payload.self_rating is not None:
        r.self_rating = payload.self_rating
    if payload.scores is not None:
        r.self_scores = _merge_scores(r.self_scores, payload.scores, template, "both")
    if payload.sections is not None:
        r.self_sections = _merge_sections(r.self_sections, payload.sections,
                                          template, "employee")
    if payload.goal_comments:
        allowed = {g.id: g for g in goals_for(db, r)}
        for goal_id, comment in payload.goal_comments.items():
            if goal_id in allowed:
                allowed[goal_id].employee_comment = comment

    if payload.submit:
        _require_something(r.self_assessment, r.self_scores, r.self_sections,
                           "self-assessment")
        _require_required_sections(template, r.self_sections, "employee")
        r.self_submitted_at = datetime.utcnow()
        r.status = ReviewStatusEnum.SELF_SUBMITTED
        if r.manager_id:
            create_notification(
                db, r.manager_id, f"{r.employee.full_name} submitted their self-assessment",
                f"Ready for your review for {r.cycle.name if r.cycle else 'this cycle'}.",
                NotificationTypeEnum.INFO, related_id=r.id)
    db.commit()
    db.refresh(r)
    audit.record(db, "review.self_submitted", actor=current_user, request=request,
                 target_type="review", target_id=r.id, detail={"submitted": payload.submit})
    return _review_out(db, r, current_user)


@router.put("/reviews/{review_id}/manager", response_model=ReviewOut)
def write_manager_review(review_id: int, payload: ManagerReviewIn, request: Request,
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    r = _load_review(db, review_id)
    if not (r.manager_id == current_user.id or is_privileged(current_user, db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the line manager, HR or an administrator writes this")
    if r.employee_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You cannot write your own manager review")
    if r.status == ReviewStatusEnum.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wait for the self-assessment. Writing first anchors the review on "
                   "your own view, which is the thing a self-assessment exists to avoid.")
    if r.status in (ReviewStatusEnum.SHARED, ReviewStatusEnum.CLOSED):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This review has been shared and can no longer be edited")

    template = (db.query(ReviewTemplate).filter(ReviewTemplate.id == r.template_id).first()
                if r.template_id else None)
    for field in ("manager_assessment", "overall_rating", "strengths",
                  "development_areas", "calibration_note", "recommendation"):
        value = getattr(payload, field)
        if value is not None:
            setattr(r, field, value)
    if payload.scores is not None:
        r.manager_scores = _merge_scores(r.manager_scores, payload.scores,
                                         template, "both")
    if payload.sections is not None:
        r.manager_sections = _merge_sections(r.manager_sections, payload.sections,
                                             template, "manager")
    if payload.goal_ratings:
        allowed = {g.id: g for g in goals_for(db, r)}
        for goal_id, value in payload.goal_ratings.items():
            goal = allowed.get(goal_id)
            if not goal:
                continue
            rating = value.get("rating")
            if rating is not None:
                if not 1 <= float(rating) <= 5:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"A rating is 1 to 5. Goal {goal_id} got {rating}.")
                goal.rating = float(rating)
            if "comment" in value:
                goal.rating_comment = value.get("comment")
            if value.get("status"):
                goal.status = GoalStatusEnum(value["status"])

    # Scored on every save, not only on submit, so a manager can see the number
    # moving as they fill the form in rather than after they commit to it.
    db.flush()
    working = score_review(db, r, template)

    if payload.submit:
        _require_something(r.manager_assessment, r.manager_scores, r.manager_sections,
                           "manager review")
        _require_required_sections(template, r.manager_sections, "manager")
        r.manager_submitted_at = datetime.utcnow()
        r.status = ReviewStatusEnum.MANAGER_SUBMITTED
    db.commit()
    db.refresh(r)
    audit.record(db, "review.manager_submitted", actor=current_user, request=request,
                 target_type="review", target_id=r.id,
                 detail={"overall_rating": payload.overall_rating,
                         "final_score": r.final_score,
                         "recommendation": (payload.recommendation.value
                                            if payload.recommendation else None)})
    out = _review_out(db, r, current_user)
    return out


@router.post("/reviews/{review_id}/share", response_model=ReviewOut)
def share_review(review_id: int, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Release the review to the employee. Separate from writing it, on purpose."""
    r = _load_review(db, review_id)
    if not (r.manager_id == current_user.id or is_privileged(current_user, db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if r.status != ReviewStatusEnum.MANAGER_SUBMITTED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The manager review has to be submitted first")
    r.status = ReviewStatusEnum.SHARED
    r.shared_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    create_notification(
        db, r.employee_id, "Your performance review is ready",
        f"{r.cycle.name if r.cycle else 'Your review'} has been shared with you. "
        "Read it and acknowledge when you have.",
        NotificationTypeEnum.INFO, related_id=r.id)
    audit.record(db, "review.shared", actor=current_user, request=request,
                 target_type="review", target_id=r.id)
    return _review_out(db, r, current_user)


@router.post("/reviews/{review_id}/acknowledge", response_model=ReviewOut)
def acknowledge_review(review_id: int, payload: AcknowledgeIn, request: Request,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    r = _load_review(db, review_id)
    if r.employee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the employee acknowledges their own review")
    if r.status != ReviewStatusEnum.SHARED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This review has not been shared with you yet")
    r.employee_comment = payload.employee_comment
    r.acknowledged_at = datetime.utcnow()
    r.status = ReviewStatusEnum.CLOSED
    db.commit()
    db.refresh(r)
    if r.manager_id:
        create_notification(db, r.manager_id,
                            f"{r.employee.full_name} acknowledged their review",
                            payload.employee_comment or "No further comment was added.",
                            NotificationTypeEnum.SUCCESS, related_id=r.id)
    audit.record(db, "review.acknowledged", actor=current_user, request=request,
                 target_type="review", target_id=r.id)
    return _review_out(db, r, current_user)


# --------------------------------------------------------------------------
# One-to-ones
# --------------------------------------------------------------------------

class OneOnOneIn(BaseModel):
    employee_id: int
    meeting_date: date
    agenda: Optional[str] = None
    shared_notes: Optional[str] = None
    private_notes: Optional[str] = None
    action_items: List[Dict[str, Any]] = Field(default_factory=list)


class OneOnOneUpdate(BaseModel):
    meeting_date: Optional[date] = None
    agenda: Optional[str] = None
    shared_notes: Optional[str] = None
    private_notes: Optional[str] = None
    action_items: Optional[List[Dict[str, Any]]] = None


class OneOnOneOut(BaseModel):
    id: int
    manager_id: int
    manager_name: Optional[str] = None
    employee_id: int
    employee_name: Optional[str] = None
    meeting_date: date
    agenda: Optional[str] = None
    shared_notes: Optional[str] = None
    private_notes: Optional[str] = None
    action_items: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime

    class Config:
        from_attributes = True


def _one_on_one_out(m: OneOnOne, viewer: User) -> OneOnOneOut:
    o = OneOnOneOut.model_validate(m, from_attributes=True)
    o.manager_name = m.manager.full_name if m.manager else None
    o.employee_name = m.employee.full_name if m.employee else None
    if viewer.id != m.manager_id:
        # The manager's own notes stay the manager's own — including from HR,
        # who would otherwise turn a private aide-memoire into a company record.
        o.private_notes = None
    return o


@router.get("/one-on-ones", response_model=List[OneOnOneOut])
def list_one_on_ones(employee_id: Optional[int] = None, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    q = (db.query(OneOnOne).options(joinedload(OneOnOne.employee),
                                    joinedload(OneOnOne.manager)))
    if employee_id:
        _require_visible(db, current_user, employee_id)
        q = q.filter(OneOnOne.employee_id == employee_id)
    else:
        q = q.filter((OneOnOne.employee_id == current_user.id)
                     | (OneOnOne.manager_id == current_user.id))
    return [_one_on_one_out(m, current_user)
            for m in q.order_by(OneOnOne.meeting_date.desc()).all()]


@router.post("/one-on-ones", response_model=OneOnOneOut,
             status_code=status.HTTP_201_CREATED)
def create_one_on_one(payload: OneOnOneIn, request: Request,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    if payload.employee_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A one-to-one is with somebody else")
    if not (_manages(db, current_user, payload.employee_id)
            or is_privileged(current_user, db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the line manager records a one-to-one")
    meeting = OneOnOne(manager_id=current_user.id, **payload.model_dump())
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    audit.record(db, "one_on_one.created", actor=current_user, request=request,
                 target_type="one_on_one", target_id=meeting.id,
                 detail={"employee_id": payload.employee_id})
    return _one_on_one_out(meeting, current_user)


@router.put("/one-on-ones/{meeting_id}", response_model=OneOnOneOut)
def update_one_on_one(meeting_id: int, payload: OneOnOneUpdate, request: Request,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    meeting = (db.query(OneOnOne).options(joinedload(OneOnOne.employee),
                                          joinedload(OneOnOne.manager))
               .filter(OneOnOne.id == meeting_id).first())
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if meeting.manager_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the manager who recorded it can edit it")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(meeting, field, value)
    db.commit()
    db.refresh(meeting)
    return _one_on_one_out(meeting, current_user)


# ==========================================================================
# Review templates — the form each kind of role is reviewed on
# ==========================================================================

class TemplateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    designations: List[str] = Field(default_factory=list)
    competencies: List[Dict[str, Any]] = Field(default_factory=list)
    sections: List[Dict[str, Any]] = Field(default_factory=list)
    goal_weight: float = Field(60.0, ge=0, le=100)
    competency_weight: float = Field(40.0, ge=0, le=100)
    is_default: bool = False
    is_active: bool = True


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = None
    designations: Optional[List[str]] = None
    competencies: Optional[List[Dict[str, Any]]] = None
    sections: Optional[List[Dict[str, Any]]] = None
    goal_weight: Optional[float] = Field(None, ge=0, le=100)
    competency_weight: Optional[float] = Field(None, ge=0, le=100)
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class TemplateOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    designations: List[str] = Field(default_factory=list)
    competencies: List[Dict[str, Any]] = Field(default_factory=list)
    sections: List[Dict[str, Any]] = Field(default_factory=list)
    goal_weight: float
    competency_weight: float
    is_default: bool
    is_active: bool
    competency_weight_total: float = 0.0
    employee_count: int = 0

    class Config:
        from_attributes = True


def _template_out(db: Session, t: ReviewTemplate) -> TemplateOut:
    o = TemplateOut.model_validate(t, from_attributes=True)
    o.competency_weight_total = round(
        sum(float(c.get("weight") or 0) for c in (t.competencies or [])), 2)
    o.employee_count = (db.query(User)
                        .filter(User.is_active.is_(True),
                                User.designation.in_(t.designations or ["\x00"]))
                        .count()) if t.designations else 0
    return o


def _check_weights(competencies, goal_weight, competency_weight) -> None:
    """
    Weights that do not total 100 make a score nobody can interpret.

    Checked here rather than left to the interface, because the number this
    produces is used to rank people against each other.
    """
    if competencies:
        total = round(sum(float(c.get("weight") or 0) for c in competencies), 2)
        if abs(total - 100.0) > 0.01:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Competency weights add up to {total}, not 100.")
        for c in competencies:
            if not c.get("key") or not c.get("title"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Every competency needs a key and a title.")
    split = round(float(goal_weight) + float(competency_weight), 2)
    if abs(split - 100.0) > 0.01:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Goals and competencies add up to {split}, not 100.")


@router.get("/templates", response_model=List[TemplateOut])
def list_templates(include_inactive: bool = False, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    q = db.query(ReviewTemplate)
    if not include_inactive:
        q = q.filter(ReviewTemplate.is_active.is_(True))
    return [_template_out(db, t) for t in q.order_by(ReviewTemplate.name).all()]


@router.get("/templates/for/{designation}", response_model=Optional[TemplateOut])
def template_for_designation(designation: str, db: Session = Depends(get_db),
                             current_user: User = Depends(get_current_user)):
    """Which form this role is reviewed on. Used to preview before a cycle opens."""
    t = template_for(db, designation)
    return _template_out(db, t) if t else None


@router.post("/templates", response_model=TemplateOut,
             status_code=status.HTTP_201_CREATED)
def create_template(payload: TemplateIn, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    if db.query(ReviewTemplate).filter(ReviewTemplate.name == payload.name).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"A template called '{payload.name}' already exists")
    _check_weights(payload.competencies, payload.goal_weight, payload.competency_weight)
    t = ReviewTemplate(**payload.model_dump(), created_by=current_user.id)
    if t.is_default:
        db.query(ReviewTemplate).update({"is_default": False})
    db.add(t)
    db.commit()
    db.refresh(t)
    audit.record(db, "review_template.created", actor=current_user, request=request,
                 target_type="review_template", target_id=t.id, detail={"name": t.name})
    return _template_out(db, t)


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: int, payload: TemplateUpdate, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    """
    Edit a form. Reviews already answered on it keep the version they were
    answered on, because the template id is stored per review — but a cycle
    that is already open will pick up the change for anyone still to start.
    """
    t = db.query(ReviewTemplate).filter(ReviewTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    changes = payload.model_dump(exclude_unset=True)
    _check_weights(changes.get("competencies", t.competencies),
                   changes.get("goal_weight", t.goal_weight),
                   changes.get("competency_weight", t.competency_weight))
    if changes.get("is_default"):
        db.query(ReviewTemplate).filter(ReviewTemplate.id != template_id).update(
            {"is_default": False})
    for field, value in changes.items():
        setattr(t, field, value)
    db.commit()
    db.refresh(t)
    audit.record(db, "review_template.updated", actor=current_user, request=request,
                 target_type="review_template", target_id=t.id,
                 detail={k: str(v)[:120] for k, v in changes.items()})
    return _template_out(db, t)


@router.get("/rating-scale")
def rating_scale(current_user: User = Depends(get_current_user)):
    """The words that go with 1 to 5, so both sides read the same meaning."""
    return {"scale": RATING_SCALE}


# ==========================================================================
# The form itself — what to show, who fills it, and what is already filled
# ==========================================================================

@router.get("/reviews/{review_id}/form")
def review_form(review_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """
    Everything needed to render one review, with each field marked by who
    answers it and whether the person asking may answer it right now.

    The rules about who fills what and when live here rather than in the
    frontend, so the screen and the write endpoints cannot disagree.
    """
    r = _load_review(db, review_id)
    is_manager = (r.manager_id == current_user.id) or is_privileged(current_user, db)
    if not (current_user.id == r.employee_id or is_manager):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    template = (db.query(ReviewTemplate).filter(ReviewTemplate.id == r.template_id).first()
                if r.template_id else template_for(db, r.employee.designation
                                                   if r.employee else None))
    form = build_form(db, r, template, current_user, is_manager)
    form["employee_name"] = r.employee.full_name if r.employee else None
    form["designation"] = r.employee.designation if r.employee else None
    form["cycle_name"] = r.cycle.name if r.cycle else None
    return form


@router.get("/reviews/{review_id}/score")
def review_score(review_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """
    The score with its working shown: every competency, both ratings, the gap.

    Withheld from the employee until the review is shared — a score is the one
    thing nobody should learn about before the conversation.
    """
    r = _load_review(db, review_id)
    is_manager = (r.manager_id == current_user.id) or is_privileged(current_user, db)
    if not (current_user.id == r.employee_id or is_manager):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if (current_user.id == r.employee_id and not is_manager
            and r.status not in (ReviewStatusEnum.SHARED, ReviewStatusEnum.CLOSED)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Your review has not been shared with you yet")
    template = (db.query(ReviewTemplate).filter(ReviewTemplate.id == r.template_id).first()
                if r.template_id else None)
    working = score_review(db, r, template)
    db.commit()
    return working


# ==========================================================================
# Ranking and charts
# ==========================================================================

class LeaderboardRow(BaseModel):
    rank: Optional[int] = None
    employee_id: int
    employee_name: str
    designation: Optional[str] = None
    department: Optional[str] = None
    manager_name: Optional[str] = None
    status: ReviewStatusEnum
    goal_score: Optional[float] = None
    competency_score: Optional[float] = None
    final_score: Optional[float] = None
    band: Optional[str] = None
    recommendation: Optional[RecommendationEnum] = None
    goals_achieved: int = 0
    goals_total: int = 0


@router.get("/leaderboard", response_model=List[LeaderboardRow])
def leaderboard(cycle_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(require_admin_or_hr)):
    """
    Everyone in a cycle, best first.

    Unscored reviews are listed too, at the bottom and without a rank. A
    ranking that silently omits the people whose managers have not finished is
    a ranking of managers, not of employees.
    """
    reviews = (db.query(Review)
               .options(joinedload(Review.employee), joinedload(Review.manager))
               .filter(Review.cycle_id == cycle_id).all())
    rows = []
    for r in reviews:
        goals = goals_for(db, r)
        rows.append(LeaderboardRow(
            employee_id=r.employee_id,
            employee_name=r.employee.full_name if r.employee else f"#{r.employee_id}",
            designation=r.employee.designation if r.employee else None,
            department=r.employee.department if r.employee else None,
            manager_name=r.manager.full_name if r.manager else None,
            status=r.status, goal_score=r.goal_score,
            competency_score=r.competency_score, final_score=r.final_score,
            band=band_for(r.final_score), recommendation=r.recommendation,
            goals_achieved=sum(1 for g in goals
                               if g.status == GoalStatusEnum.ACHIEVED),
            goals_total=len(goals)))

    scored = sorted([r for r in rows if r.final_score is not None],
                    key=lambda x: -x.final_score)
    for i, row in enumerate(scored, start=1):
        row.rank = i
    return scored + [r for r in rows if r.final_score is None]


@router.get("/charts/cycle/{cycle_id}")
def cycle_charts(cycle_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    """Chart data for one cycle. Parallel arrays and {label, value} pairs."""
    cycle = db.query(ReviewCycle).filter(ReviewCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    reviews = (db.query(Review).options(joinedload(Review.employee))
               .filter(Review.cycle_id == cycle_id).all())

    progress: Dict[str, int] = {}
    for r in reviews:
        progress[r.status.value] = progress.get(r.status.value, 0) + 1

    scored = [r for r in reviews if r.final_score is not None]
    buckets = [("1.0 – 1.9", 1.0, 2.0), ("2.0 – 2.9", 2.0, 3.0),
               ("3.0 – 3.4", 3.0, 3.5), ("3.5 – 3.9", 3.5, 4.0),
               ("4.0 – 4.4", 4.0, 4.5), ("4.5 – 5.0", 4.5, 5.01)]
    histogram = [{"label": label,
                  "value": sum(1 for r in scored if lo <= r.final_score < hi)}
                 for label, lo, hi in buckets]

    dept: Dict[str, List[float]] = {}
    for r in scored:
        key = (r.employee.department if r.employee else None) or "Unassigned"
        dept.setdefault(key, []).append(r.final_score)

    # Average manager rating per competency, across everyone on each template.
    competency_totals: Dict[str, List[float]] = {}
    titles: Dict[str, str] = {}
    for r in reviews:
        if not r.template_id:
            continue
        tpl = db.query(ReviewTemplate).filter(ReviewTemplate.id == r.template_id).first()
        for c in (tpl.competencies if tpl else []):
            rating = ((r.manager_scores or {}).get(c["key"]) or {}).get("rating")
            if rating is not None:
                competency_totals.setdefault(c["key"], []).append(float(rating))
                titles[c["key"]] = c.get("title", c["key"])

    gaps = []
    for r in reviews:
        if not (r.self_scores and r.manager_scores):
            continue
        pairs = [((r.self_scores.get(k) or {}).get("rating"),
                  (r.manager_scores.get(k) or {}).get("rating"))
                 for k in r.manager_scores]
        pairs = [(s, m) for s, m in pairs if s is not None and m is not None]
        if pairs:
            gaps.append(sum(s - m for s, m in pairs) / len(pairs))

    return {
        "cycle": {"id": cycle.id, "name": cycle.name, "status": cycle.status.value},
        "progress": [{"label": k.replace("_", " "), "value": v}
                     for k, v in sorted(progress.items())],
        "completion_percent": round(
            100.0 * sum(1 for r in reviews if r.status in
                        (ReviewStatusEnum.SHARED, ReviewStatusEnum.CLOSED))
            / len(reviews), 1) if reviews else 0.0,
        "score_histogram": histogram,
        "average_score": round(sum(r.final_score for r in scored) / len(scored), 2)
        if scored else None,
        "by_department": sorted(
            [{"label": k, "value": round(sum(v) / len(v), 2)} for k, v in dept.items()],
            key=lambda d: -d["value"]),
        "by_competency": sorted(
            [{"label": titles[k], "value": round(sum(v) / len(v), 2)}
             for k, v in competency_totals.items()], key=lambda d: -d["value"]),
        # Positive means people rate themselves above their managers, which is
        # normal; a large number is worth a conversation about the scale.
        "average_self_assessment_gap": round(sum(gaps) / len(gaps), 2) if gaps else None,
        "reviewed": len(scored),
        "total": len(reviews),
    }


@router.get("/charts/employee/{employee_id}")
def employee_charts(employee_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """
    One person over time: score per cycle, their latest competency profile, and
    how much of what they were asked to do actually landed.
    """
    _require_visible(db, current_user, employee_id)
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    reviews = (db.query(Review).options(joinedload(Review.cycle))
               .filter(Review.employee_id == employee_id)
               .order_by(Review.cycle_id).all())
    # The employee sees only what has been shared with them; their manager and
    # HR see the lot.
    if current_user.id == employee_id and not is_privileged(current_user, db):
        reviews = [r for r in reviews
                   if r.status in (ReviewStatusEnum.SHARED, ReviewStatusEnum.CLOSED)]

    trend, goal_completion = [], []
    for r in reviews:
        label = r.cycle.name if r.cycle else f"Cycle {r.cycle_id}"
        if r.final_score is not None:
            trend.append({"label": label, "value": r.final_score})
        goals = goals_for(db, r)
        if goals:
            achieved = sum(1 for g in goals if g.status == GoalStatusEnum.ACHIEVED)
            goal_completion.append({"label": label,
                                    "value": round(100.0 * achieved / len(goals), 1)})

    radar = []
    latest = next((r for r in reversed(reviews) if r.manager_scores), None)
    if latest and latest.template_id:
        tpl = db.query(ReviewTemplate).filter(
            ReviewTemplate.id == latest.template_id).first()
        for c in (tpl.competencies if tpl else []):
            radar.append({
                "label": c.get("title", c["key"]),
                "manager": ((latest.manager_scores or {}).get(c["key"]) or {}).get("rating"),
                "self": ((latest.self_scores or {}).get(c["key"]) or {}).get("rating"),
            })

    return {
        "employee": {"id": employee.id, "name": employee.full_name,
                     "designation": employee.designation,
                     "department": employee.department},
        "score_trend": trend,
        "goal_completion_percent": goal_completion,
        "competency_profile": radar,
        "latest_score": trend[-1]["value"] if trend else None,
        "latest_band": band_for(trend[-1]["value"]) if trend else None,
    }


# ==========================================================================
# Employee of the month
# ==========================================================================

class AwardOpenIn(BaseModel):
    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2000, le=2100)
    nominations_close_on: Optional[date] = None


class NominateIn(BaseModel):
    nominee_id: int
    reason: str = Field(..., min_length=15, max_length=1000)


class DecideAwardIn(BaseModel):
    winner_id: Optional[int] = None
    citation: Optional[str] = Field(None, max_length=2000)
    # A month with no winner is a real outcome and is recorded as one.
    no_award: bool = False
    announce: bool = True


class NominationOut(BaseModel):
    id: int
    nominee_id: int
    nominee_name: Optional[str] = None
    # Who wrote it. Null for everyone but the people who decide, and the
    # author reading their own — see _award_out(): a nomination is written in
    # confidence, and naming the author to the whole company afterwards turns
    # it into a public act of alliance.
    nominated_by: Optional[int] = None
    nominator_name: Optional[str] = None
    reason: str
    created_at: datetime

    class Config:
        from_attributes = True


class AwardOut(BaseModel):
    id: int
    month: int
    year: int
    status: AwardStatusEnum
    winner_id: Optional[int] = None
    winner_name: Optional[str] = None
    citation: Optional[str] = None
    decided_at: Optional[datetime] = None
    announced_at: Optional[datetime] = None
    nominations_close_on: Optional[date] = None
    nomination_count: int = 0
    # Names and counts, best-supported first. Visible to whoever decides; an
    # employee sees only their own nominations until a winner is announced.
    tally: List[Dict[str, Any]] = Field(default_factory=list)
    nominations: List[NominationOut] = Field(default_factory=list)
    you_have_nominated: bool = False


def _award_out(db: Session, a: MonthlyAward, viewer: User) -> AwardOut:
    o = AwardOut.model_validate(a, from_attributes=True)
    o.winner_name = a.winner.full_name if a.winner else None
    noms = a.nominations or []
    o.nomination_count = len(noms)
    o.you_have_nominated = any(n.nominated_by == viewer.id for n in noms)

    decided = a.status == AwardStatusEnum.DECIDED
    can_see_all = is_privileged(viewer, db) or decided
    # Who nominated is separate from what was written. Whoever decides needs
    # both — three nominations from three teams is not three from one
    # person's own — but nobody else does, and once an award is decided the
    # nominations become readable by everybody.
    #
    # Deliberately narrower than is_privileged: HR runs the process and may
    # nominate like anyone else, so HR reading the authors would let a
    # nominator see who else backed whom.
    may_see_authors = viewer.role == RoleEnum.ADMIN

    if can_see_all:
        counts: Dict[int, Dict[str, Any]] = {}
        for n in noms:
            row = counts.setdefault(n.nominee_id, {
                "employee_id": n.nominee_id,
                "employee_name": n.nominee.full_name if n.nominee else None,
                "nominations": 0, "reasons": []})
            row["nominations"] += 1
            row["reasons"].append(n.reason)
        o.tally = sorted(counts.values(), key=lambda r: -r["nominations"])
        o.nominations = [
            _nomination_out(n, named=may_see_authors or n.nominated_by == viewer.id)
            for n in sorted(noms, key=lambda n: n.created_at)]
    else:
        # While it is open, you see what you wrote and nothing else — a running
        # tally would turn nominating into voting for the front-runner.
        o.nominations = [_nomination_out(n, named=True) for n in noms
                         if n.nominated_by == viewer.id]
    return o


def _nomination_out(n: AwardNomination, named: bool = False) -> NominationOut:
    """
    One nomination. `named` carries the author; without it the reason stands
    on its own, which is what everybody except the deciders and the author
    should see.
    """
    o = NominationOut.model_validate(n, from_attributes=True)
    o.nominee_name = n.nominee.full_name if n.nominee else None
    if named:
        o.nominator_name = n.nominator.full_name if n.nominator else None
    else:
        # Both fields, not just the name: an id is an identity to anyone who
        # can also read /users.
        o.nominator_name = None
        o.nominated_by = None
    return o


def _load_award(db: Session, award_id: int) -> MonthlyAward:
    a = (db.query(MonthlyAward)
         .options(joinedload(MonthlyAward.nominations)
                  .joinedload(AwardNomination.nominee),
                  joinedload(MonthlyAward.nominations)
                  .joinedload(AwardNomination.nominator),
                  joinedload(MonthlyAward.winner))
         .filter(MonthlyAward.id == award_id).first())
    if not a:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Award not found")
    return a


@router.get("/awards", response_model=List[AwardOut])
def list_awards(year: Optional[int] = None, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    q = db.query(MonthlyAward).options(
        joinedload(MonthlyAward.nominations).joinedload(AwardNomination.nominee),
        joinedload(MonthlyAward.nominations).joinedload(AwardNomination.nominator),
        joinedload(MonthlyAward.winner))
    if year:
        q = q.filter(MonthlyAward.year == year)
    awards = q.order_by(MonthlyAward.year.desc(), MonthlyAward.month.desc()).all()
    return [_award_out(db, a, current_user) for a in awards]


@router.get("/awards/current", response_model=Optional[AwardOut])
def current_award(db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """This month's, if it has been opened. Null if nobody has opened it yet."""
    today = date.today()
    a = (db.query(MonthlyAward)
         .options(joinedload(MonthlyAward.nominations).joinedload(AwardNomination.nominee),
                  joinedload(MonthlyAward.nominations).joinedload(AwardNomination.nominator),
                  joinedload(MonthlyAward.winner))
         .filter(MonthlyAward.month == today.month, MonthlyAward.year == today.year)
         .first())
    return _award_out(db, a, current_user) if a else None


@router.post("/awards", response_model=AwardOut, status_code=status.HTTP_201_CREATED)
def open_award(payload: AwardOpenIn, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(require_admin)):
    """Open nominations for a month."""
    existing = (db.query(MonthlyAward)
                .filter(MonthlyAward.month == payload.month,
                        MonthlyAward.year == payload.year).first())
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{calendar.month_name[payload.month]} {payload.year} is already open.")
    award = MonthlyAward(**payload.model_dump())
    db.add(award)
    db.commit()
    db.refresh(award)

    closes = (f" Nominations close on "
              f"{payload.nominations_close_on.strftime('%d %B')}."
              if payload.nominations_close_on else "")
    for u in db.query(User).filter(User.is_active.is_(True)).all():
        create_notification(
            db, u.id, f"Employee of the Month — {calendar.month_name[payload.month]}",
            "Nominations are open. Put forward a colleague and say why." + closes,
            NotificationTypeEnum.INFO, related_id=award.id,
            related_type=RelatedTypeEnum.AWARD)
    audit.record(db, "award.opened", actor=current_user, request=request,
                 target_type="award", target_id=award.id,
                 detail={"month": payload.month, "year": payload.year})
    return _award_out(db, _load_award(db, award.id), current_user)


@router.post("/awards/{award_id}/nominate", response_model=AwardOut)
def nominate(award_id: int, payload: NominateIn, request: Request,
             db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    """
    Put a colleague forward. The reason is required and has a minimum length,
    because a nomination with nothing in it gives whoever decides nothing to
    weigh and turns the award into a headcount.
    """
    award = _load_award(db, award_id)
    if award.status != AwardStatusEnum.OPEN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Nominations for that month are closed.")
    if award.nominations_close_on and date.today() > award.nominations_close_on:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nominations closed on "
                   f"{award.nominations_close_on.strftime('%d %B %Y')}.")
    if payload.nominee_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="You cannot nominate yourself.")
    nominee = db.query(User).filter(User.id == payload.nominee_id,
                                    User.is_active.is_(True)).first()
    if not nominee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="That employee was not found, or is no longer active.")
    already = next((n for n in award.nominations
                    if n.nominated_by == current_user.id
                    and n.nominee_id == payload.nominee_id), None)
    if already:
        # Editing rather than refusing: somebody improving their reason is a
        # better outcome than a second row that counts twice.
        already.reason = payload.reason
    else:
        db.add(AwardNomination(award_id=award.id, nominee_id=nominee.id,
                               nominated_by=current_user.id, reason=payload.reason))
    db.commit()
    audit.record(db, "award.nominated", actor=current_user, request=request,
                 target_type="award", target_id=award.id,
                 detail={"nominee_id": nominee.id})
    return _award_out(db, _load_award(db, award_id), current_user)


@router.delete("/awards/{award_id}/nominate/{nominee_id}", response_model=AwardOut)
def withdraw_nomination(award_id: int, nominee_id: int, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    award = _load_award(db, award_id)
    row = next((n for n in award.nominations if n.nominated_by == current_user.id
                and n.nominee_id == nominee_id), None)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="You have not nominated that person.")
    if award.status != AwardStatusEnum.OPEN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That month has been decided.")
    db.delete(row)
    db.commit()
    return _award_out(db, _load_award(db, award_id), current_user)


@router.get("/awards/{award_id}/signals")
def award_signals(award_id: int, db: Session = Depends(get_db),
                  current_user: User = Depends(require_admin)):
    """
    What the month's record says about each person, next to their nominations.

    Not a formula and not a ranking. Whoever decides still decides — but they
    should be able to see that the most-nominated person was on leave for three
    weeks, and this is where that shows up.
    """
    import calendar as _cal
    from datetime import datetime as _dt
    from app.models.attendance import Attendance
    from app.models.leave import Leave, LeaveStatusEnum
    from app.models.overtime import OvertimeRecord, OvertimeStatusEnum
    from app.utils.leave_policy import calculate_working_days
    from app.utils.shifts import punctuality, shift_for

    award = _load_award(db, award_id)
    first = date(award.year, award.month, 1)
    last = date(award.year, award.month,
                _cal.monthrange(award.year, award.month)[1])
    # A month still running is measured to today, not to its end. Counting
    # attendance over ten elapsed days against the month's full twenty-two
    # made a perfect record read as 10/22, and made everyone look worse the
    # earlier in the month somebody opened this. A past month is unaffected:
    # its end has already been and gone.
    today = date.today()
    if last > today:
        last = today
    start = _dt.combine(first, _dt.min.time())
    end = _dt.combine(last, _dt.max.time())

    noms: Dict[int, int] = {}
    for n in award.nominations:
        noms[n.nominee_id] = noms.get(n.nominee_id, 0) + 1

    working_days = calculate_working_days(first, last)

    # Everything below used to be fetched inside the loop — attendance, the
    # shift, overtime, leave, the last review and the goal count, once per
    # employee. Twenty people cost 150 queries and half a minute. Each is now
    # one query for the whole company, assembled in memory.
    staff = db.query(User).filter(User.is_active.is_(True)).all()
    ids = [u.id for u in staff]

    # Lateness needs each person's shift, and the per-employee cache behind
    # shift_for() would still be one query each across the whole company.
    from app.utils.shifts import prime_assignments
    prime_assignments(db, ids)

    from collections import defaultdict

    attendance_by: Dict[int, list] = defaultdict(list)
    if ids:
        for a in (db.query(Attendance)
                  .filter(Attendance.employee_id.in_(ids),
                          Attendance.check_in_time >= start,
                          Attendance.check_in_time <= end).all()):
            attendance_by[a.employee_id].append(a)

    overtime_by: Dict[int, Any] = {}
    if ids:
        for o in (db.query(OvertimeRecord)
                  .filter(OvertimeRecord.employee_id.in_(ids),
                          OvertimeRecord.month == award.month,
                          OvertimeRecord.year == award.year,
                          OvertimeRecord.status == OvertimeStatusEnum.APPROVED).all()):
            overtime_by[o.employee_id] = o

    leave_days_by: Dict[int, float] = defaultdict(float)
    if ids:
        for l in (db.query(Leave)
                  .filter(Leave.employee_id.in_(ids),
                          Leave.status == LeaveStatusEnum.APPROVED,
                          Leave.start_date <= end, Leave.end_date >= start).all()):
            leave_days_by[l.employee_id] += float(l.total_days or 0)

    # Highest cycle_id per employee wins, which is what ordering by cycle
    # descending and taking the first did one row at a time.
    score_by: Dict[int, Any] = {}
    if ids:
        for r in (db.query(Review)
                  .filter(Review.employee_id.in_(ids),
                          Review.final_score.isnot(None))
                  .order_by(Review.cycle_id.desc()).all()):
            score_by.setdefault(r.employee_id, r.final_score)

    goals_by: Dict[int, int] = defaultdict(int)
    if ids:
        for employee_id, achieved in (db.query(Goal.employee_id, func.count(Goal.id))
                                      .filter(Goal.employee_id.in_(ids),
                                              Goal.status == GoalStatusEnum.ACHIEVED)
                                      .group_by(Goal.employee_id).all()):
            goals_by[employee_id] = achieved

    rows = []
    for u in staff:
        if getattr(u, "attendance_exempt", False):
            attendance_days = late_days = None
            hours = None
        else:
            records = attendance_by.get(u.id, [])
            attendance_days = len({a.check_in_time.date() for a in records})
            hours = round(sum(a.duration_hours or 0.0 for a in records), 1)
            # Lateness is only answerable against a shift. With none defined it
            # is reported as unknown rather than as zero, which would read as
            # "never late" for a company that has never said when the day starts.
            late_days = 0 if shift_for(db, u.id, first) else None
            if late_days is not None:
                for a in records:
                    day = a.check_in_time.date()
                    shift = shift_for(db, u.id, day)
                    if shift and shift.works_on(day) and punctuality(
                            shift, a.check_in_time, a.check_out_time)["is_late"]:
                        late_days += 1

        overtime = overtime_by.get(u.id)
        leave_days = leave_days_by.get(u.id, 0.0)

        rows.append({
            "employee_id": u.id, "employee_name": u.full_name,
            "designation": u.designation, "department": u.department,
            "nominations": noms.get(u.id, 0),
            "attendance_days": attendance_days,
            "working_days_in_month": working_days,
            "hours_worked": hours,
            "late_days": late_days,
            "approved_overtime_hours": round(overtime.overtime_hours, 1) if overtime else 0.0,
            "leave_days": leave_days,
            "goals_achieved_to_date": goals_by.get(u.id, 0),
            "last_review_score": score_by.get(u.id),
            "attendance_exempt": bool(getattr(u, "attendance_exempt", False)),
        })

    rows.sort(key=lambda r: (-r["nominations"], -(r["last_review_score"] or 0)))
    return {"award_id": award.id, "month": award.month, "year": award.year,
            "period": f"{first.isoformat()} to {last.isoformat()}",
            "working_days": working_days, "candidates": rows}


@router.delete("/awards/{award_id}")
def delete_award(award_id: int, request: Request,
                 remove_notifications: bool = Query(
                     True, description="Also delete the notifications this award "
                                       "put in everybody's list"),
                 remove_announcement: bool = Query(True),
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin)):
    """
    Take a month back off the board.

    For a month opened by mistake or abandoned. The nominations go with it. What
    it sent out is separate and optional, because the two are genuinely
    different decisions: erasing the record while leaving the notifications
    means sixteen people still see an award that no longer exists, and erasing
    the notifications rewrites what people were told. Default is to remove both;
    turn either off to keep it.

    A month that was genuinely run and genuinely had no winner is better decided
    with no_award — that is a fact worth keeping, and this erases it.

    The audit trail is untouched either way. It is the record of what was done,
    including the undoing.
    """
    from app.models.announcement import Announcement

    award = _load_award(db, award_id)
    month_name = calendar.month_name[award.month]
    had_winner = award.winner_id is not None
    nominations = len(award.nominations)

    removed_notifications = 0
    if remove_notifications:
        # Two matches: the related_type set on newer notifications, and the
        # exact title older ones carry from before that type existed.
        removed_notifications = db.query(Notification).filter(
            or_(and_(Notification.related_type == RelatedTypeEnum.AWARD.value,
                     Notification.related_id == award.id),
                and_(Notification.title == f"Employee of the Month — {month_name}",
                     Notification.related_id == award.id))
        ).delete(synchronize_session=False)

    removed_announcements = 0
    if remove_announcement:
        removed_announcements = db.query(Announcement).filter(
            Announcement.title == f"Employee of the Month — {month_name} {award.year}"
        ).delete(synchronize_session=False)

    db.delete(award)
    db.commit()

    audit.record(db, "award.deleted", actor=current_user, request=request,
                 target_type="award", target_id=award_id,
                 detail={"month": award.month, "year": award.year,
                         "had_winner": had_winner, "nominations_removed": nominations,
                         "notifications_removed": removed_notifications,
                         "announcements_removed": removed_announcements})
    return {"deleted_award": f"{award.year}-{award.month:02d}",
            "nominations_removed": nominations,
            "notifications_removed": removed_notifications,
            "announcements_removed": removed_announcements}


@router.post("/awards/{award_id}/decide", response_model=AwardOut)
def decide_award(award_id: int, payload: DecideAwardIn, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin)):
    """
    Pick the winner, or record that no award was made this month.

    The citation is required with a winner: an award announced with no reason
    tells the other fifteen people nothing about what the company values.
    """
    award = _load_award(db, award_id)
    if award.status == AwardStatusEnum.DECIDED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That month has already been decided.")
    month_name = calendar.month_name[award.month]

    if payload.no_award:
        award.status = AwardStatusEnum.DECIDED
        award.winner_id = None
        award.citation = payload.citation
    else:
        if not payload.winner_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Name a winner, or send no_award: true.")
        if not (payload.citation or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Write the citation. An award with no stated reason tells "
                       "everybody else nothing about what the company values.")
        winner = db.query(User).filter(User.id == payload.winner_id,
                                       User.is_active.is_(True)).first()
        if not winner:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="That employee was not found, or is no longer active.")
        award.winner_id = winner.id
        award.citation = payload.citation
        award.status = AwardStatusEnum.DECIDED

    award.decided_by = current_user.id
    award.decided_at = datetime.utcnow()

    if payload.announce and award.winner_id:
        from app.models.announcement import Announcement
        winner = db.query(User).filter(User.id == award.winner_id).first()
        db.add(Announcement(
            title=f"Employee of the Month — {month_name} {award.year}",
            content=f"{winner.full_name} ({winner.designation or 'the company'}).\n\n"
                    f"{award.citation}",
            created_by=current_user.id))
        award.announced_at = datetime.utcnow()
        for u in db.query(User).filter(User.is_active.is_(True)).all():
            create_notification(
                db, u.id, f"Employee of the Month — {month_name}",
                (f"Congratulations! {award.citation}" if u.id == award.winner_id
                 else f"{winner.full_name} is {month_name}'s Employee of the Month."),
                NotificationTypeEnum.SUCCESS, related_id=award.id,
                related_type=RelatedTypeEnum.AWARD)

    db.commit()
    audit.record(db, "award.decided", actor=current_user, request=request,
                 target_type="award", target_id=award.id,
                 detail={"winner_id": award.winner_id, "no_award": payload.no_award})
    return _award_out(db, _load_award(db, award_id), current_user)
