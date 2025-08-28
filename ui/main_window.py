from __future__ import annotations
import sys
from pathlib import Path
from datetime import date
from PySide6.QtCore import Qt, QDate, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QSplitter,
    QFormLayout, QDateEdit, QPlainTextEdit, QTableView, QMessageBox, QFileDialog, QStatusBar,
    QTabWidget, QToolBar, QDialog, QDialogButtonBox, QDoubleSpinBox, QCheckBox, QGridLayout, QFrame
)
from sqlalchemy.orm import Session
from database import make_engine, make_session_factory, init_db
from models import Base
from domain import PatientDTO, SessionDTO, PatientStatsDTO
from repo import PatientRepo, SessionRepo
from ui.table_model import PatientTableModel, SessionTableModel

APP_DIR = Path(__file__).resolve().parents[1]
DB_PATH = APP_DIR / "patients.db"


# ---------- widgets ----------
class DateField(QDateEdit):
    def __init__(self, default_today=False):
        super().__init__(calendarPopup=True)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setDateRange(QDate(1900, 1, 1), QDate(2999, 12, 31))
        if default_today:
            self.setDate(QDate.currentDate())
        else:
            self.setDate(self.minimumDate())

    def set_date(self, d: date | None):
        self.setDate(QDate(d.year, d.month, d.day) if d else self.minimumDate())

    def get_date(self) -> date | None:
        if self.date() == self.minimumDate(): return None
        d = self.date();
        return date(d.year(), d.month(), d.day())


class StatCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel);
        self.setObjectName("card")
        l = QVBoxLayout(self);
        l.setContentsMargins(12, 10, 12, 10)
        self.title = QLabel(title);
        self.title.setStyleSheet("color:#555; font-size:12px;")
        self.value = QLabel("—");
        self.value.setStyleSheet("font-size:20px; font-weight:600;")
        l.addWidget(self.title);
        l.addWidget(self.value)

    def set_value(self, text: str): self.value.setText(text)


class SessionDialog(QDialog):
    def __init__(self, parent=None, initial: SessionDTO | None = None):
        super().__init__(parent);
        self.setWindowTitle("Session")
        self.date = DateField(default_today=True)
        self.price = QDoubleSpinBox();
        self.price.setRange(0, 1_000_000);
        self.price.setDecimals(2)
        self.attended = QCheckBox("Attended")
        self.notes = QPlainTextEdit()

        form = QFormLayout()
        form.addRow("Date *", self.date)
        form.addRow("Price", self.price)
        form.addRow("", self.attended)
        form.addRow("Notes", self.notes)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept);
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form);
        root.addWidget(btns)

        self._existing_id: int | None = None
        if initial:
            self._existing_id = initial.id
            self.date.set_date(initial.session_date)
            self.price.setValue(initial.price)
            self.attended.setChecked(initial.attended)
            self.notes.setPlainText(initial.notes or "")

    def collect(self, patient_id: int) -> SessionDTO | None:
        d = self.date.get_date()
        if not d:
            QMessageBox.critical(self, "Validation", "Date is required.");
            return None
        return SessionDTO(
            id=self._existing_id, patient_id=patient_id,
            session_date=d, price=float(self.price.value()),
            attended=self.attended.isChecked(),
            notes=self.notes.toPlainText().strip() or None
        )


# ---------- main window ----------
class MainWindow(QMainWindow):
    def __init__(self, session: Session):
        super().__init__()
        self.setWindowTitle("Patient Desk (PySide6 + SQLAlchemy)")
        self.setMinimumSize(1100, 680)
        self.s = session
        self.patients = PatientRepo(self.s)
        self.sessions = SessionRepo(self.s)
        self.current_patient_id: int | None = None

        # status bar
        self.setStatusBar(QStatusBar(self))

        # search with debounce
        top = QWidget();
        top_l = QHBoxLayout(top)
        self.search = QLineEdit();
        self.search.setPlaceholderText("Search patients by name or email…")
        self.search.setClearButtonEnabled(True)
        top_l.addWidget(QLabel("Search:"));
        top_l.addWidget(self.search, 1)

        # left: patients table
        self.pt_model = PatientTableModel(self.patients.list())
        self.pt_table = QTableView()
        self.pt_table.setModel(self.pt_model)
        self.pt_table.setSelectionBehavior(QTableView.SelectRows)
        self.pt_table.setSelectionMode(QTableView.SingleSelection)
        self.pt_table.verticalHeader().setVisible(False)
        self.pt_table.setAlternatingRowColors(True)

        # right: tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_overview_tab(), "Overview")
        self.tabs.addTab(self._build_sessions_tab(), "Sessions")

        # layout
        split = QSplitter();
        split.addWidget(self.pt_table);
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3);
        split.setStretchFactor(1, 5)

        central = QWidget();
        root = QVBoxLayout(central)
        root.addWidget(top);
        root.addWidget(split, 1)
        self.setCentralWidget(central)

        # toolbars / menus
        self._build_toolbars()

        # signals
        self.pt_table.selectionModel().selectionChanged.connect(self._on_patient_select)
        self.search.textChanged.connect(self._on_search_changed)

        # initial
        self._refresh_patients()
        self._update_actions()

        # light style for cards
        self.setStyleSheet("""
        QFrame#card { border:1px solid #e5e5e5; border-radius:10px; background:#fafafa; }
        """)

    # ----- overview tab -----
    def _build_overview_tab(self) -> QWidget:
        w = QWidget();
        l = QVBoxLayout(w);
        l.setContentsMargins(8, 8, 8, 8);
        l.setSpacing(8)
        self.ov_name = QLabel("Select a patient");
        self.ov_name.setStyleSheet("font-size:16px; font-weight:600;")
        l.addWidget(self.ov_name)

        grid = QGridLayout();
        grid.setHorizontalSpacing(12);
        grid.setVerticalSpacing(12)
        self.card_first = StatCard("First session")
        self.card_last = StatCard("Last session")
        self.card_total = StatCard("Total sessions")
        self.card_rate = StatCard("Attendance")
        self.card_revenu = StatCard("Total revenue")
        grid.addWidget(self.card_first, 0, 0)
        grid.addWidget(self.card_last, 0, 1)
        grid.addWidget(self.card_total, 0, 2)
        grid.addWidget(self.card_rate, 1, 0)
        grid.addWidget(self.card_revenu, 1, 1)
        grid.setColumnStretch(3, 1)
        l.addLayout(grid)
        self.ov_notes = QPlainTextEdit();
        self.ov_notes.setPlaceholderText("Patient notes…");
        self.ov_notes.setFixedHeight(140)
        l.addWidget(self.ov_notes)
        return w

    # ----- sessions tab -----
    def _build_sessions_tab(self) -> QWidget:
        w = QWidget();
        l = QVBoxLayout(w);
        l.setContentsMargins(8, 8, 8, 8);
        l.setSpacing(6)
        self.sess_model = SessionTableModel([])
        self.sess_table = QTableView();
        self.sess_table.setModel(self.sess_model)
        self.sess_table.setSelectionBehavior(QTableView.SelectRows)
        self.sess_table.setSelectionMode(QTableView.SingleSelection)
        self.sess_table.verticalHeader().setVisible(False)
        self.sess_table.setAlternatingRowColors(True)
        l.addWidget(self.sess_table, 1)
        self.sess_hint = QLabel("Use the Sessions toolbar to add a session.");
        self.sess_hint.setStyleSheet("color:#666;")
        l.addWidget(self.sess_hint)
        return w

    # ----- toolbars -----
    def _build_toolbars(self):
        # Patients toolbar
        tb_p = QToolBar("Patients");
        self.addToolBar(Qt.TopToolBarArea, tb_p)
        act_new = QAction("New", self);
        act_new.setShortcut("Ctrl+N");
        act_new.triggered.connect(self._new_patient)
        act_save = QAction("Save", self);
        act_save.setShortcut("Ctrl+S");
        act_save.triggered.connect(self._save_patient)
        act_del = QAction("Delete", self);
        act_del.setShortcut("Del");
        act_del.triggered.connect(self._delete_patient)
        act_export = QAction("Export CSV", self);
        act_export.triggered.connect(self._export_csv)
        tb_p.addAction(act_new);
        tb_p.addAction(act_save);
        tb_p.addAction(act_del);
        tb_p.addSeparator();
        tb_p.addAction(act_export)
        self.act_pat_delete = act_del  # for enable/disable

        # Sessions toolbar
        tb_s = QToolBar("Sessions");
        self.addToolBar(Qt.TopToolBarArea, tb_s)
        self.act_s_add = QAction("Add Session", self);
        self.act_s_add.setShortcut("Ctrl+Shift+A");
        self.act_s_add.triggered.connect(self._add_session)
        self.act_s_edit = QAction("Edit Session", self);
        self.act_s_edit.setShortcut("Ctrl+E");
        self.act_s_edit.triggered.connect(self._edit_session)
        self.act_s_del = QAction("Delete Session", self);
        self.act_s_del.setShortcut("Ctrl+D");
        self.act_s_del.triggered.connect(self._delete_session)
        tb_s.addAction(self.act_s_add);
        tb_s.addAction(self.act_s_edit);
        tb_s.addAction(self.act_s_del)

    # ----- helpers -----
    def _debounced(self, fn, ms=250):
        if not hasattr(self, "_debounce"): self._debounce = QTimer(self); self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(fn);
        self._debounce.start(ms)

    def _on_search_changed(self, _):
        self._debounced(self._refresh_patients, 250)

    def _refresh_patients(self):
        rows = self.patients.list(self.search.text().strip() or None)
        self.pt_model.set_rows(rows)
        self.statusBar().showMessage(f"{len(rows)} patient(s) loaded.", 1500)

    def _selected_session(self) -> SessionDTO | None:
        idxs = self.sess_table.selectionModel().selectedRows()
        if not idxs: return None
        return self.sess_model.at(idxs[0].row())

    def _update_actions(self):
        has_patient = self.current_patient_id is not None
        self.act_pat_delete.setEnabled(has_patient)
        self.act_s_add.setEnabled(has_patient)
        has_session = self._selected_session() is not None
        self.act_s_edit.setEnabled(has_session)
        self.act_s_del.setEnabled(has_session)

    # ----- patient flow -----
    def _on_patient_select(self, *_):
        idxs = self.pt_table.selectionModel().selectedRows()
        if not idxs:
            self._new_patient()
            self._update_actions()
            return
        p = self.pt_model.at(idxs[0].row())
        self.current_patient_id = p.id
        self._load_patient_to_ui(p)
        self._refresh_sessions()
        self._refresh_stats()
        self._update_actions()

    def _load_patient_to_ui(self, p: PatientDTO):
        self.ov_name.setText(f"{p.first_name} {p.last_name} — {p.email or ''}".strip())
        self.ov_notes.setPlainText(p.notes or "")

    def _collect_patient(self) -> PatientDTO | None:
        # collected from Overview tab (name in header + notes box)
        # we keep simple: inline editors not shown for name; use table selection + dialog in future
        # For now, save only notes change (name/contacts edited from table later if needed)
        idxs = self.pt_table.selectionModel().selectedRows()
        if not idxs:
            QMessageBox.information(self, "Save", "Select a patient to save changes.");
            return None
        p = self.pt_model.at(idxs[0].row())
        return PatientDTO(
            id=p.id, first_name=p.first_name, last_name=p.last_name,
            birth_date=p.birth_date, phone=p.phone, email=p.email,
            notes=self.ov_notes.toPlainText().strip() or None
        )

    def _new_patient(self):
        self.current_patient_id = None
        self.ov_name.setText("New patient")
        self.ov_notes.clear()
        self.sess_model.set_rows([])
        self.card_first.set_value("—");
        self.card_last.set_value("—")
        self.card_total.set_value("0");
        self.card_rate.set_value("—");
        self.card_revenu.set_value("0.00")

    def _save_patient(self):
        dto = self._collect_patient()
        if not dto: return
        self.patients.update(dto)
        self.statusBar().showMessage("Patient saved.", 1500)
        self._refresh_patients()

    def _delete_patient(self):
        if self.current_patient_id is None:
            QMessageBox.information(self, "Delete", "Select a patient first.");
            return
        if QMessageBox.question(self, "Confirm", "Delete this patient and all sessions?") != QMessageBox.Yes:
            return
        self.patients.delete(self.current_patient_id)
        self._new_patient()
        self._refresh_patients()
        self._update_actions()
        self.statusBar().showMessage("Patient deleted.", 1500)

    # ----- stats -----
    def _refresh_stats(self):
        if self.current_patient_id is None:
            for c in (self.card_first, self.card_last, self.card_total, self.card_rate, self.card_revenu):
                c.set_value("—")
            return
        st: PatientStatsDTO = self.patients.stats(self.current_patient_id)
        self.card_first.set_value(st.first_session.isoformat() if st.first_session else "—")
        self.card_last.set_value(st.last_session.isoformat() if st.last_session else "—")
        self.card_total.set_value(str(st.total_sessions))
        self.card_rate.set_value(f"{st.attendance_rate * 100:.0f}%")
        self.card_revenu.set_value(f"{st.total_revenue:.2f}")

    # ----- sessions flow -----
    def _refresh_sessions(self):
        if self.current_patient_id is None:
            self.sess_model.set_rows([]);
            return
        rows = self.sessions.list_by_patient(self.current_patient_id)
        self.sess_model.set_rows(rows)

    def _add_session(self):
        if self.current_patient_id is None:
            QMessageBox.information(self, "Sessions", "Select a patient first.");
            return
        dlg = SessionDialog(self)
        if dlg.exec() == QDialog.Accepted:
            dto = dlg.collect(self.current_patient_id)
            if not dto: return
            self.sessions.create(dto)
            self._refresh_sessions();
            self._refresh_stats();
            self._update_actions()
            self.statusBar().showMessage("Session added.", 1500)

    def _edit_session(self):
        s = self._selected_session()
        if not s:
            QMessageBox.information(self, "Sessions", "Select a session first.");
            return
        dlg = SessionDialog(self, initial=s)
        if dlg.exec() == QDialog.Accepted:
            dto = dlg.collect(s.patient_id)
            if not dto: return
            dto.id = s.id
            self.sessions.update(dto)
            self._refresh_sessions();
            self._refresh_stats()
            self.statusBar().showMessage("Session updated.", 1500)

    def _delete_session(self):
        s = self._selected_session()
        if not s:
            QMessageBox.information(self, "Sessions", "Select a session first.");
            return
        if QMessageBox.question(self, "Confirm", "Delete this session?") != QMessageBox.Yes:
            return
        self.sessions.delete(s.id)
        self._refresh_sessions();
        self._refresh_stats();
        self._update_actions()
        self.statusBar().showMessage("Session deleted.", 1500)

    # ----- export -----
    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export patients to CSV", "patients.csv", "CSV Files (*.csv)")
        if not path: return
        import csv
        rows = self.patients.list(self.search.text().strip() or None)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "first_name", "last_name", "birth_date", "phone", "email", "notes"])
            for p in rows:
                w.writerow([
                    p.id, p.first_name, p.last_name,
                    p.birth_date.isoformat() if p.birth_date else "",
                    p.phone or "", p.email or "", p.notes or ""
                ])
        self.statusBar().showMessage("Exported.", 1500)


# ---- entrypoint (keep main.py that calls run()) ----
def run():
    engine = make_engine(DB_PATH);
    init_db(engine, Base)
    SessionFactory = make_session_factory(engine)
    with SessionFactory() as s:
        app = QApplication(sys.argv)
        w = MainWindow(s);
        w.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    run()
