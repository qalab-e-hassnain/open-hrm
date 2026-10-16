from pydantic import BaseModel
from typing import Optional


class LeaveBalanceResponse(BaseModel):
    """Leave balance response schema"""
    id: int
    employee_id: int
    year: int
    
    # Annual Leave
    annual_total: float
    annual_used: float
    annual_carryover: float
    annual_remaining: float
    
    # Sick Leave
    sick_total: float
    sick_used: float
    sick_remaining: float
    
    # Personal Leave
    personal_total: float
    personal_used: float
    personal_remaining: float
    
    # Maternity Leave
    maternity_total: float
    maternity_used: float
    maternity_remaining: float
    
    # Paternity Leave
    paternity_total: float
    paternity_used: float
    paternity_remaining: float
    
    # Bereavement Leave
    bereavement_total: float
    bereavement_used: float
    bereavement_remaining: float
    
    class Config:
        from_attributes = True

