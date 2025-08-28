from __future__ import annotations
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Date, Text, func

class Base(DeclarativeBase):
    pass

class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Business key shown in UI; enforced unique
    cin: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(120))
    last_name:  Mapped[str] = mapped_column(String(120))
    birth_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    phone:      Mapped[str | None] = mapped_column(String(60), nullable=True)
    email:      Mapped[str | None] = mapped_column(String(160), nullable=True)
    notes:      Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(server_default=func.datetime("now"))
    updated_at: Mapped[str] = mapped_column(server_default=func.datetime("now"), onupdate=func.datetime("now"))
