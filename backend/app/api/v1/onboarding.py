"""
A new joiner's first week as a checklist they can see.

Documents to sign, details to supply, things for HR to do — one list, because
a new joiner tracking their own start across three screens is how items get
missed.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, is_privileged, require_admin_or_hr
from app.database import get_db
from app.models.document import DocumentTemplate, EmployeeDocument, DocumentStatusEnum
from app.models.lifecycle import (OnboardingPacket, OnboardingTask,
                                  OnboardingTaskKindEnum, TaskStatusEnum)
from app.models.user import User
from app.utils import audit

router = APIRouter()

# Non-document steps every joiner has, regardless of role.
STANDARD_TASKS = [
    (OnboardingTaskKindEnum.DATA, "Complete your profile",
     "CNIC number and scans, date of birth, address, emergency contact."),
    (OnboardingTaskKindEnum.DATA, "Add your bank details",
     "Account title, number and branch code — payroll pays into these."),
    (OnboardingTaskKindEnum.ACTION, "Introduction to your team",
     "Meet your reporting head and the people you will work with."),
]


class TaskOut(BaseModel):
    id: int
    kind: OnboardingTaskKindEnum
    label: str
    detail: Optional[str] = None
    status: TaskStatusEnum
    employee_document_id: Optional[int] = None
    completed_at: Optional[datetime] = None
    class Config:
        from_attributes = True


class PacketOut(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    tasks: List[TaskOut] = Field(default_factory=list)
    total: int = 0
    done: int = 0
    percent_complete: int = 0


class StartPacket(BaseModel):
    employee_id: int


def _to_packet(db: Session, packet: OnboardingPacket) -> PacketOut:
    out = PacketOut(
        id=packet.id, employee_id=packet.employee_id,
        employee_name=packet.employee.full_name if packet.employee else None,
        started_at=packet.started_at, completed_at=packet.completed_at,
        tasks=[TaskOut.model_validate(t) for t in
               sorted(packet.tasks, key=lambda x: (x.sort_order, x.id))],
    )
    out.total = len(out.tasks)
    out.done = sum(1 for t in out.tasks if t.status != TaskStatusEnum.PENDING)
    out.percent_complete = round(100 * out.done / out.total) if out.total else 0
    return out


def complete_document_task(db: Session, employee_document_id: int) -> None:
    """
    Called when a document is signed. The checklist item closes itself rather
    than asking the employee to tick it — they already did the thing.
    """
    task = db.query(OnboardingTask).filter(
        OnboardingTask.employee_document_id == employee_document_id,
        OnboardingTask.status == TaskStatusEnum.PENDING,
    ).first()
    if not task:
        return
    task.status = TaskStatusEnum.COMPLETED
    task.completed_at = datetime.utcnow()
    db.commit()
    _close_if_done(db, task.packet_id)


def _close_if_done(db: Session, packet_id: int) -> None:
    packet = db.query(OnboardingPacket).filter(OnboardingPacket.id == packet_id).first()
    if not packet or packet.completed_at:
        return
    if all(t.status != TaskStatusEnum.PENDING for t in packet.tasks):
        packet.completed_at = datetime.utcnow()
        db.commit()


@router.post("/start", response_model=PacketOut, status_code=status.HTTP_201_CREATED)
def start_onboarding(payload: StartPacket, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin_or_hr)):
    """
    Open a joiner's checklist: issue every onboarding document and add the
    standard steps.

    Which documents those are is a data question — any active template flagged
    include_in_onboarding — so adding the harassment policy to the pack is an
    edit, not a deployment.
    """
    from app.api.v1.documents import issue_document

    employee = db.query(User).filter(User.id == payload.employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if db.query(OnboardingPacket).filter(
            OnboardingPacket.employee_id == employee.id).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"{employee.full_name} already has an onboarding checklist.")

    packet = OnboardingPacket(employee_id=employee.id, created_by=current_user.id)
    db.add(packet); db.flush()

    order = 0
    templates = db.query(DocumentTemplate).filter(
        DocumentTemplate.include_in_onboarding == True,   # noqa: E712
        DocumentTemplate.is_active == True,               # noqa: E712
    ).order_by(DocumentTemplate.kind).all()
    for tpl in templates:
        doc = issue_document(db, employee, tpl, current_user)
        db.flush()
        verb = "Sign" if tpl.requires_signature else "Read and acknowledge"
        db.add(OnboardingTask(packet_id=packet.id, kind=OnboardingTaskKindEnum.DOCUMENT,
                              label=f"{verb}: {tpl.title}", employee_document_id=doc.id,
                              sort_order=order))
        order += 1

    for kind, label, detail in STANDARD_TASKS:
        db.add(OnboardingTask(packet_id=packet.id, kind=kind, label=label,
                              detail=detail, sort_order=order))
        order += 1

    db.commit(); db.refresh(packet)
    audit.record(db, "onboarding.started", actor=current_user, request=request,
                 target_type="user", target_id=employee.id,
                 detail={"documents": len(templates), "tasks": order})
    return _to_packet(db, packet)


@router.get("/me", response_model=Optional[PacketOut])
def my_onboarding(db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    packet = (db.query(OnboardingPacket).options(joinedload(OnboardingPacket.tasks))
              .filter(OnboardingPacket.employee_id == current_user.id).first())
    return _to_packet(db, packet) if packet else None


@router.get("/employee/{employee_id}", response_model=Optional[PacketOut])
def employee_onboarding(employee_id: int, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    if employee_id != current_user.id and not is_privileged(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
    packet = db.query(OnboardingPacket).filter(
        OnboardingPacket.employee_id == employee_id).first()
    return _to_packet(db, packet) if packet else None


@router.get("/", response_model=List[PacketOut])
def list_onboarding(db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    packets = (db.query(OnboardingPacket).options(joinedload(OnboardingPacket.employee))
               .order_by(OnboardingPacket.started_at.desc()).all())
    return [_to_packet(db, p) for p in packets]


class TaskUpdate(BaseModel):
    status: TaskStatusEnum


@router.put("/tasks/{task_id}", response_model=PacketOut)
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db),
                current_user: User = Depends(require_admin_or_hr)):
    """
    Tick or waive a task. Document tasks are not settled here — they close when
    the document is actually signed, and marking one done by hand would record
    a signature that never happened.
    """
    task = db.query(OnboardingTask).filter(OnboardingTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.kind == OnboardingTaskKindEnum.DOCUMENT and payload.status == TaskStatusEnum.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A document task completes when the document is signed. Waive it instead if it does not apply.")
    task.status = payload.status
    task.completed_at = datetime.utcnow() if payload.status != TaskStatusEnum.PENDING else None
    task.completed_by = current_user.id
    db.commit()
    _close_if_done(db, task.packet_id)
    packet = db.query(OnboardingPacket).filter(OnboardingPacket.id == task.packet_id).first()
    return _to_packet(db, packet)
