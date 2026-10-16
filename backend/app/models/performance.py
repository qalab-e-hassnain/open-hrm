"""
Goals, reviews and one-to-ones.

Every contract the company issues embeds role-specific KPIs, taken from the job
profile for that designation. Nothing measured anyone against them, and the
increments payroll carries forward year to year rested on no written record at
all. Goals here seed from that same job profile, so the KPI somebody signed is
the KPI they are reviewed on.
"""
from sqlalchemy import (Column, Integer, String, Text, Date, DateTime, Float,
                        Boolean, Enum, ForeignKey, JSON, Index)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base


class CycleStatusEnum(str, enum.Enum):
    DRAFT = "draft"      # being set up, invisible to employees
    OPEN = "open"        # self-assessments and reviews accepted
    CLOSED = "closed"


class GoalSourceEnum(str, enum.Enum):
    JOB_PROFILE = "job_profile"   # a KPI from the signed contract
    MANAGER = "manager"
    EMPLOYEE = "employee"


class GoalStatusEnum(str, enum.Enum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    PARTIAL = "partial"
    MISSED = "missed"
    DROPPED = "dropped"           # overtaken by events, not failed


class RatedByEnum(str, enum.Enum):
    """Who answers a given item on the form."""
    EMPLOYEE = "employee"      # the employee only
    MANAGER = "manager"        # the manager only
    BOTH = "both"              # both, separately — the gap is the useful part


class RecommendationEnum(str, enum.Enum):
    NONE = "none"
    INCREMENT = "increment"
    PROMOTION = "promotion"
    DEVELOPMENT_PLAN = "development_plan"


class ReviewStatusEnum(str, enum.Enum):
    DRAFT = "draft"                      # nobody has written anything
    SELF_SUBMITTED = "self_submitted"    # with the manager
    MANAGER_SUBMITTED = "manager_submitted"  # written, not yet shared
    SHARED = "shared"                    # the employee can read it
    CLOSED = "closed"                    # acknowledged, cycle done


class ReviewTemplate(Base):
    """
    The form a review actually is, for a given kind of role.

    A hardware engineer and a product manager are not doing the same job, and
    rating both against one generic list produces a score that means nothing.
    Each template names the competencies that matter for the roles it covers,
    what each one means, and what it is worth.

    `competencies` is a list of:
        {"key": "design_quality",
         "title": "Design quality",
         "description": "what good looks like, in this company's terms",
         "weight": 20,                       # percent, across the template
         "rated_by": "both"}                 # RatedByEnum

    `sections` is the written part, a list of:
        {"key": "achievements", "title": "...", "prompt": "...",
         "rated_by": "employee", "required": true}
    """
    __tablename__ = "review_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=True)

    # Designations this template covers. Empty means it is the fallback used
    # for any role no other template claims — so a new job title never leaves
    # somebody without a review form.
    designations = Column(JSON, nullable=False, default=list)
    is_default = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    competencies = Column(JSON, nullable=False, default=list)
    sections = Column(JSON, nullable=False, default=list)

    # How the final score is made up. Goals are what someone was asked to
    # achieve; competencies are how they went about it. Both matter, and the
    # split is a company decision rather than a constant.
    goal_weight = Column(Float, default=60.0, nullable=False)
    competency_weight = Column(Float, default=40.0, nullable=False)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ReviewCycle(Base):
    __tablename__ = "review_cycles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)          # "H1 2026"
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    self_assessment_due = Column(Date, nullable=True)
    manager_review_due = Column(Date, nullable=True)
    status = Column(Enum(CycleStatusEnum), default=CycleStatusEnum.DRAFT,
                    nullable=False, index=True)
    instructions = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    reviews = relationship("Review", back_populates="cycle",
                           cascade="all, delete-orphan")


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Null for a standing goal that is not tied to any one review period.
    cycle_id = Column(Integer, ForeignKey("review_cycles.id"), nullable=True, index=True)

    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    source = Column(Enum(GoalSourceEnum), default=GoalSourceEnum.MANAGER, nullable=False)
    # Relative importance, as a percentage. Not enforced to total 100: a
    # half-written goal set is normal mid-cycle, and refusing to save it would
    # only teach people to write nonsense weights.
    weight = Column(Float, default=0.0, nullable=False)
    target = Column(String, nullable=True)         # "Ship v2 by 30 June"

    status = Column(Enum(GoalStatusEnum), default=GoalStatusEnum.ACTIVE,
                    nullable=False, index=True)
    # 1 to 5, set at review time. Null until then.
    rating = Column(Float, nullable=True)
    rating_comment = Column(Text, nullable=True)
    employee_comment = Column(Text, nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("User", foreign_keys=[employee_id])


class Review(Base):
    """
    One employee's appraisal for one cycle.

    Self-assessment first, then the manager, then it is shared. The order is
    enforced: a manager who writes first anchors on their own view, and an
    employee who reads the manager's rating before writing their own stops
    writing anything useful.
    """
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    cycle_id = Column(Integer, ForeignKey("review_cycles.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    status = Column(Enum(ReviewStatusEnum), default=ReviewStatusEnum.DRAFT,
                    nullable=False, index=True)

    # The form this review was answered on, snapshotted by id. A template
    # edited next year must not silently rewrite what somebody was asked.
    template_id = Column(Integer, ForeignKey("review_templates.id"), nullable=True)

    self_assessment = Column(Text, nullable=True)
    self_rating = Column(Float, nullable=True)
    self_submitted_at = Column(DateTime, nullable=True)

    # Answers keyed by the template's competency and section keys:
    #   {"design_quality": {"rating": 4, "comment": "..."}}
    #   {"achievements": "..."}
    self_scores = Column(JSON, nullable=False, default=dict)
    self_sections = Column(JSON, nullable=False, default=dict)
    manager_scores = Column(JSON, nullable=False, default=dict)
    manager_sections = Column(JSON, nullable=False, default=dict)

    manager_assessment = Column(Text, nullable=True)
    overall_rating = Column(Float, nullable=True)
    strengths = Column(Text, nullable=True)
    development_areas = Column(Text, nullable=True)
    manager_submitted_at = Column(DateTime, nullable=True)

    # Computed, never typed. goal_score and competency_score are each out of
    # five; final_score is the two combined at the template's weighting, and is
    # the only figure that is comparable between people.
    goal_score = Column(Float, nullable=True)
    competency_score = Column(Float, nullable=True)
    final_score = Column(Float, nullable=True)
    recommendation = Column(Enum(RecommendationEnum), nullable=True)

    shared_at = Column(DateTime, nullable=True)
    employee_comment = Column(Text, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)

    # Only ever read by HR and the reviewing manager — kept off the shared
    # view so a calibration note is not mistaken for feedback to the employee.
    calibration_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    cycle = relationship("ReviewCycle", back_populates="reviews")
    employee = relationship("User", foreign_keys=[employee_id])
    manager = relationship("User", foreign_keys=[manager_id])


class OneOnOne(Base):
    """
    A recorded conversation between a manager and someone who reports to them.

    `private_notes` is the manager's own; it is never returned to the employee.
    Keeping it in the same row as the shared notes is deliberate — a manager
    who has to keep private notes somewhere else keeps them nowhere.
    """
    __tablename__ = "one_on_ones"

    id = Column(Integer, primary_key=True, index=True)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    meeting_date = Column(Date, nullable=False, index=True)

    agenda = Column(Text, nullable=True)
    shared_notes = Column(Text, nullable=True)
    private_notes = Column(Text, nullable=True)
    # [{"text": ..., "owner": "employee"|"manager", "done": false}]
    action_items = Column(JSON, default=list, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("User", foreign_keys=[employee_id])
    manager = relationship("User", foreign_keys=[manager_id])


class AwardStatusEnum(str, enum.Enum):
    OPEN = "open"            # nominations being taken
    DECIDED = "decided"      # a winner has been chosen
    CANCELLED = "cancelled"


class MonthlyAward(Base):
    """
    Employee of the month, as a decision with a record behind it.

    One row per month whatever happens, including months nobody won — an award
    that quietly stops being given is worse than one that is recorded as not
    awarded, because nobody can tell which happened.
    """
    __tablename__ = "monthly_awards"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    status = Column(Enum(AwardStatusEnum), default=AwardStatusEnum.OPEN,
                    nullable=False, index=True)

    winner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    citation = Column(Text, nullable=True)          # why, in public
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    announced_at = Column(DateTime, nullable=True)

    nominations_close_on = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    winner = relationship("User", foreign_keys=[winner_id])
    nominations = relationship("AwardNomination", back_populates="award",
                               cascade="all, delete-orphan")


class AwardNomination(Base):
    """
    One person putting another's name forward, with a reason.

    The reason is required and not optional politeness: a nomination with no
    stated reason gives whoever decides nothing to weigh, and turns the award
    into a popularity count.
    """
    __tablename__ = "award_nominations"

    id = Column(Integer, primary_key=True, index=True)
    award_id = Column(Integer, ForeignKey("monthly_awards.id"), nullable=False, index=True)
    nominee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    nominated_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    award = relationship("MonthlyAward", back_populates="nominations")
    nominee = relationship("User", foreign_keys=[nominee_id])
    nominator = relationship("User", foreign_keys=[nominated_by])


Index("ix_reviews_cycle_employee", Review.cycle_id, Review.employee_id, unique=True)
Index("ix_monthly_awards_period", MonthlyAward.year, MonthlyAward.month, unique=True)
# One nomination per person per nominee per month; a second is a repeat vote.
Index("ix_award_nominations_unique", AwardNomination.award_id,
      AwardNomination.nominee_id, AwardNomination.nominated_by, unique=True)
Index("ix_goals_employee_cycle", Goal.employee_id, Goal.cycle_id)
