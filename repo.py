from __future__ import annotations
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from models import Patient as PatientORM
from domain import PatientDTO

def _to_dto(p: PatientORM) -> PatientDTO:
    return PatientDTO(
        id=p.id, cin=p.cin, first_name=p.first_name, last_name=p.last_name,
        birth_date=p.birth_date, phone=p.phone, email=p.email, notes=p.notes
    )

def _apply(dto: PatientDTO, orm: PatientORM | None = None) -> PatientORM:
    t = orm or PatientORM()
    t.cin = dto.cin
    t.first_name, t.last_name = dto.first_name, dto.last_name
    t.birth_date, t.phone, t.email, t.notes = dto.birth_date, dto.phone, dto.email, dto.notes
    return t

class PatientRepo:
    def __init__(self, s: Session):
        self.s = s

    def exists_cin(self, cin: str, exclude_id: int | None = None) -> bool:
        q = select(func.count(PatientORM.id)).where(func.lower(PatientORM.cin) == func.lower(cin))
        if exclude_id is not None:
            q = q.where(PatientORM.id != exclude_id)
        return (self.s.scalar(q) or 0) > 0

    def create(self, dto: PatientDTO) -> int:
        if self.exists_cin(dto.cin):
            raise ValueError(f"CIN '{dto.cin}' already exists.")
        orm = _apply(dto)
        self.s.add(orm)
        try:
            self.s.commit()
        except IntegrityError as e:
            self.s.rollback()
            raise ValueError(f"CIN '{dto.cin}' already exists.") from e
        return orm.id

    def update(self, dto: PatientDTO) -> None:
        assert dto.id is not None
        if self.exists_cin(dto.cin, exclude_id=dto.id):
            raise ValueError(f"CIN '{dto.cin}' already exists.")
        orm = self.s.get(PatientORM, dto.id)
        if not orm:
            return
        _apply(dto, orm)
        try:
            self.s.commit()
        except IntegrityError as e:
            self.s.rollback()
            raise ValueError(f"CIN '{dto.cin}' already exists.") from e

    def get(self, pid: int) -> PatientDTO | None:
        orm = self.s.get(PatientORM, pid)
        return _to_dto(orm) if orm else None

    def list(self, q: str | None = None) -> list[PatientDTO]:
        stmt = select(PatientORM).order_by(PatientORM.last_name, PatientORM.first_name)
        if q:
            q = f"%{q.lower()}%"
            stmt = stmt.where(or_(
                func.lower(PatientORM.cin).like(q),
                func.lower(PatientORM.first_name).like(q),
                func.lower(PatientORM.last_name).like(q),
                func.lower(func.ifnull(PatientORM.phone, "")).like(q),
                func.lower(func.ifnull(PatientORM.email, "")).like(q),
                func.lower(func.ifnull(PatientORM.notes, "")).like(q),
                func.lower(func.ifnull(cast(PatientORM.birth_date, String), "")).like(q),
            ))
        return [_to_dto(r) for r in self.s.scalars(stmt).all()]

    def delete(self, pid: int) -> None:
        orm = self.s.get(PatientORM, pid)
        if orm:
            self.s.delete(orm)
            self.s.commit()
