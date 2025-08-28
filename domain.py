from __future__ import annotations
from dataclasses import dataclass
from datetime import date

@dataclass
class PatientDTO:
    id: int | None
    cin: str
    first_name: str
    last_name: str
    birth_date: date | None
    phone: str | None
    email: str | None
    notes: str | None
