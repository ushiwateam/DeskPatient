# ui/table_model.py
from __future__ import annotations
from typing import List
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from domain import PatientDTO

class PatientTableModel(QAbstractTableModel):
    """
    Model backed by a list of PatientDTO.
    Column order matches ManagePatients: 0=ID, 1=CIN, 2=First, 3=Last, 4=Birth, 5=Phone, 6=Email, 7=Notes
    """
    headers = ["ID", "CIN", "First name", "Last name", "Birth date", "Phone", "Email", "Notes"]

    def __init__(self, rows: List[PatientDTO] | None = None, parent=None):
        super().__init__(parent)
        self.rows: List[PatientDTO] = rows or []

    # external helpers
    def set_rows(self, rows: List[PatientDTO] | None):
        self.beginResetModel()
        self.rows = rows or []
        self.endResetModel()

    def at(self, row: int) -> PatientDTO:
        return self.rows[row]

    # Qt model API
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, idx: QModelIndex, role=Qt.DisplayRole):
        if not idx.isValid() or idx.row() < 0 or idx.row() >= len(self.rows):
            return None
        p = self.rows[idx.row()]
        c = idx.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            return [
                p.id,                                              # 0 ID (DB PK)
                p.cin,                                             # 1 CIN (user key)
                p.first_name,                                      # 2
                p.last_name,                                       # 3
                p.birth_date.isoformat() if p.birth_date else "",  # 4
                p.phone or "",                                     # 5
                p.email or "",                                     # 6
                (p.notes or "")[:120],                             # 7
            ][c]
        return None

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.headers[section]
        return section + 1  # row header: 1-based

    def flags(self, idx: QModelIndex):
        if not idx.isValid():
            return Qt.ItemIsEnabled
        # read-only (edits happen in the form)
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable
