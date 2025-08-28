from __future__ import annotations
import sys, csv
from pathlib import Path
from datetime import date, datetime

from PySide6.QtCore import Qt, QSortFilterProxyModel, QModelIndex, QTimer, QDate, QSize, QPoint
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QFont, QPolygon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTableView,
    QPushButton, QLineEdit, QLabel, QFormLayout, QSplitter, QPlainTextEdit,
    QAbstractItemView, QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
    QFileDialog, QStatusBar, QFrame, QDateEdit, QMessageBox
)

# absolute imports (project root)
from database import make_engine, make_session_factory, init_db
from models import Base
from domain import PatientDTO
from repo import PatientRepo
from ui.table_model import PatientTableModel

APP_DIR = Path(__file__).resolve().parents[1]
DB_PATH = APP_DIR / "patients.db"

CSV_HEADERS = ["cin", "first_name", "last_name", "birth_date", "phone", "email", "notes"]

PALETTE = {
    "sidebar": "#2563EB",   # blue (inspired by your screenshot)
    "sidebar_text": "#FFFFFF",
    "blue": "#2563EB",
    "green": "#22C55E",
    "red": "#EF4444",
    "muted": "#94A3B8",
    "card": "#FFFFFF",
    "border": "#E5E7EB",
    "surface": "#F8FAFC",
}


# ------------------ Helpers ------------------

class DateField(QDateEdit):
    def __init__(self, placeholder: str | None = None):
        super().__init__(calendarPopup=True)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setDateRange(QDate(1900,1,1), QDate(2999,12,31))
        self.clear_date()
        if placeholder:
            self.setToolTip(placeholder)
    def set_date(self, d: date | None): self.setDate(QDate(d.year, d.month, d.day) if d else self.minimumDate())
    def get_date(self) -> date | None:
        if self.date() == self.minimumDate(): return None
        d = self.date(); return date(d.year(), d.month(), d.day())
    def clear_date(self): self.setDate(self.minimumDate())


def parse_birth_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s: return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try: return datetime.strptime(s, fmt).date()
        except ValueError: pass
    raise ValueError("Invalid birth_date. Use YYYY-MM-DD, MM/DD/YYYY, or DD/MM/YYYY.")


# ------------------ Filter proxy (text + date + inclusion lists) ------------------

class PatientFilterProxy(QSortFilterProxyModel):
    """
    Columns: 0=ID, 1=CIN, 2=First, 3=Last, 4=Birth, 5=Phone, 6=Email, 7=Notes
    Text filters (cin supports =EXACT / PREFIX* / contains).
    Inclusion lists per column (Excel checklist).
    """
    def __init__(self):
        super().__init__()
        self.f_cin = ""
        self.f_first = ""
        self.f_last = ""
        self.f_phone = ""
        self.f_email = ""
        self.f_birth_from: date | None = None
        self.f_birth_to:   date | None = None
        self.include_values: dict[int, set[str]] = {}
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)

    def set_inclusion_values(self, col: int, values: set[str] | None):
        if values: self.include_values[col] = set(values)
        else: self.include_values.pop(col, None)
        self.invalidateFilter()

    def set_filters(self, **kw):
        self.f_cin   = (kw.get("cin", "")).strip()
        self.f_first = (kw.get("first", "")).strip().lower()
        self.f_last  = (kw.get("last", "")).strip().lower()
        self.f_phone = (kw.get("phone", "")).strip().lower()
        self.f_email = (kw.get("email", "")).strip().lower()
        self.f_birth_from = kw.get("birth_from")
        self.f_birth_to   = kw.get("birth_to")
        self.invalidateFilter()

    def _match_cin(self, cell: str) -> bool:
        p = self.f_cin
        if not p: return True
        cell_low = (cell or "").lower()
        p_low = p.lower()
        if p_low.startswith("="):   # exact
            return cell_low == p_low[1:]
        if p_low.endswith("*"):     # prefix
            return cell_low.startswith(p_low[:-1])
        return p_low in cell_low    # contains

    def filterAcceptsRow(self, source_row: int, parent: QModelIndex) -> bool:
        m = self.sourceModel()
        def at(col):
            idx = m.index(source_row, col, parent)
            v = m.data(idx, Qt.DisplayRole)
            return "" if v is None else str(v)

        id_   = at(0)
        cin   = at(1)
        first = at(2).lower()
        last  = at(3).lower()
        birth = at(4)  # yyyy-mm-dd or ""
        phone = at(5).lower()
        email = at(6).lower()
        notes = at(7).lower()

        # Inclusion (Excel checklist)
        for col, allowed in self.include_values.items():
            if allowed:
                cell = [id_, cin, first, last, birth, phone, email, notes][col]
                cell_cmp = cell if col == 4 else str(cell).lower()  # date stays as text
                allowed_cmp = {a if col == 4 else a.lower() for a in allowed}
                if cell_cmp not in allowed_cmp:
                    return False

        if not self._match_cin(cin): return False
        if self.f_first and self.f_first not in first: return False
        if self.f_last  and self.f_last  not in last:  return False
        if self.f_phone and self.f_phone not in phone: return False
        if self.f_email and self.f_email not in email: return False

        if (self.f_birth_from or self.f_birth_to) and birth:
            try:
                bd = datetime.strptime(birth, "%Y-%m-%d").date()
                if self.f_birth_from and bd < self.f_birth_from: return False
                if self.f_birth_to   and bd > self.f_birth_to:   return False
            except Exception:
                pass
        return True


# ------------------ Pagination proxy ------------------

class PageProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._page = 1
        self._page_size = 25

    def set_page(self, page: int):
        self._page = max(1, page); self.invalidateFilter()
    def set_page_size(self, size: int):
        self._page_size = max(1, size); self.invalidateFilter()
    def page(self) -> int: return self._page
    def page_size(self) -> int: return self._page_size
    def total_rows(self) -> int: return self.sourceModel().rowCount() if self.sourceModel() else 0
    def total_pages(self) -> int:
        n, k = self.total_rows(), self._page_size
        return max(1, (n + k - 1) // k)

    def filterAcceptsRow(self, source_row: int, parent: QModelIndex) -> bool:
        start = (self._page - 1) * self._page_size
        end = start + self._page_size
        return start <= source_row < end


# ------------------ Main Window ------------------

class ManagePatientsWindow(QMainWindow):
    def __init__(self, session):
        super().__init__()
        self.setWindowTitle("Manage Patients")
        self.setMinimumSize(1240, 760)
        self.setStatusBar(QStatusBar(self))
        self.s = session
        self.repo = PatientRepo(self.s)
        self.current_patient_id: int | None = None
        self._funnel_icon = self._make_funnel_icon()

        self._build_ui()
        self._install_styles()
        self._refresh()
        self.page_proxy.set_page(1)
        self._update_pagination_labels()
        self._load_to_form(None)
        self._set_edit_enabled(False)

    # ----- UI -----
    def _build_ui(self):
        host = QWidget(); self.setCentralWidget(host)
        root = QHBoxLayout(host); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # Sidebar (Menus)
        side = QFrame(); side.setObjectName("sidebar")
        sv = QVBoxLayout(side); sv.setContentsMargins(16,16,16,16); sv.setSpacing(10)
        lbl_menus = QLabel("Menus"); lbl_menus.setObjectName("section")
        btn_pat = QPushButton("Patients"); btn_pat.setObjectName("navActive")
        btn_ses = QPushButton("Sessions"); btn_ses.setObjectName("navDisabled"); btn_ses.setEnabled(False)
        sv.addWidget(lbl_menus); sv.addWidget(btn_pat); sv.addWidget(btn_ses); sv.addStretch(1)
        root.addWidget(side)

        # Main panel
        main = QWidget(); mv = QVBoxLayout(main); mv.setContentsMargins(12,12,12,12); mv.setSpacing(10)
        root.addWidget(main, 1)

        # Global search
        top = QHBoxLayout(); lbl = QLabel("Search:"); lbl.setObjectName("muted")
        self.search = QLineEdit(); self.search.setPlaceholderText("Search any field …")
        self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self._on_global_search)
        top.addWidget(lbl); top.addWidget(self.search, 1)
        mv.addLayout(top)

        # Table models
        self.base_model = PatientTableModel([])
        self.filter_proxy = PatientFilterProxy(); self.filter_proxy.setSourceModel(self.base_model)
        self.page_proxy = PageProxy(); self.page_proxy.setSourceModel(self.filter_proxy)

        # Left column with table + pagination + import/export actions
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0,0,0,0); lv.setSpacing(6)

        self.table = QTableView()
        self.table.setModel(self.page_proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(2, Qt.AscendingOrder)  # default sort by First name
        lv.addWidget(self.table, 1)

        # install header filter buttons (Excel-like) + stacked sort icon
        self._install_header_filter_buttons()
        self.table.selectionModel().selectionChanged.connect(self._on_select)

        # Pagination footer (rows/page)
        foot = QHBoxLayout(); foot.addStretch(1)
        lbl_rpp = QLabel("Rows/page")
        self.e_page_size = QLineEdit("25"); self.e_page_size.setFixedWidth(48); self.e_page_size.setAlignment(Qt.AlignCenter)
        self.btn_prev = QPushButton("« Prev")
        self.lbl_page = QLabel("Page 1 / 1")
        self.btn_next = QPushButton("Next »")
        self.lbl_range = QLabel("")
        for lab in (self.lbl_page, self.lbl_range): lab.setObjectName("muted")

        def on_page_size():
            try: size = int(self.e_page_size.text())
            except ValueError:
                size = 25; self.e_page_size.setText("25")
            self.page_proxy.set_page_size(size)
            if self.page_proxy.page() > self.page_proxy.total_pages():
                self.page_proxy.set_page(self.page_proxy.total_pages())
            self._update_pagination_labels()

        self.e_page_size.editingFinished.connect(on_page_size)
        self.btn_prev.clicked.connect(lambda: (self.page_proxy.set_page(self.page_proxy.page()-1),
                                              self._update_pagination_labels()))
        self.btn_next.clicked.connect(lambda: (self.page_proxy.set_page(self.page_proxy.page()+1),
                                              self._update_pagination_labels()))

        foot.addWidget(lbl_rpp); foot.addWidget(self.e_page_size); foot.addSpacing(12)
        foot.addWidget(self.btn_prev); foot.addWidget(self.lbl_page); foot.addWidget(self.btn_next)
        foot.addSpacing(16); foot.addWidget(self.lbl_range)
        lv.addLayout(foot)

        # Import/export actions (below pagination)
        actions = QHBoxLayout()
        self.btn_import  = QPushButton("⬆  Import CSV");  self.btn_import.setObjectName("btnBlueFlat")
        self.btn_export_page = QPushButton("⬇  Export CSV (page)"); self.btn_export_page.setObjectName("btnBlueFlat")
        self.btn_export_all  = QPushButton("⬇  Export CSV (all filtered)"); self.btn_export_all.setObjectName("btnBlueFlat")
        self.btn_template = QPushButton("📄  Get CSV Template"); self.btn_template.setObjectName("btnGreyFlat")
        for b in (self.btn_import, self.btn_export_page, self.btn_export_all, self.btn_template):
            actions.addWidget(b)
        actions.addStretch(1)
        lv.addLayout(actions)

        # Right form
        form_wrap = QFrame(); form_wrap.setObjectName("card")
        form = QFormLayout(form_wrap)
        self.e_id = QLineEdit(); self.e_id.setReadOnly(True)
        self.e_cin = QLineEdit(); self.e_cin.setPlaceholderText("Unique CIN (auto-uppercase)")
        self.e_cin.textChanged.connect(lambda s: self._force_upper(self.e_cin, s))
        self.e_first = QLineEdit(); self.e_last = QLineEdit()
        self.e_bd = DateField()
        self.e_phone = QLineEdit(); self.e_email = QLineEdit()
        self.e_notes = QPlainTextEdit()
        form.addRow("ID", self.e_id)
        form.addRow("CIN *", self.e_cin)
        form.addRow("First name *", self.e_first)
        form.addRow("Last name *",  self.e_last)
        form.addRow("Birth date",   self.e_bd)
        form.addRow("Phone",        self.e_phone)
        form.addRow("Email",        self.e_email)
        form.addRow("Notes",        self.e_notes)

        split = QSplitter(); split.addWidget(left); split.addWidget(form_wrap)
        split.setStretchFactor(0, 5); split.setStretchFactor(1, 4)
        mv.addWidget(split, 3)

        # Bottom actions
        actions2 = QHBoxLayout(); actions2.addStretch(1)
        self.btn_new = QPushButton("➕  New");    self.btn_new.setObjectName("btnGreen")
        self.btn_save = QPushButton("💾  Save");  self.btn_save.setObjectName("btnBlue")
        self.btn_del = QPushButton("🗑  Delete"); self.btn_del.setObjectName("btnRed")
        for b in (self.btn_new, self.btn_save, self.btn_del): actions2.addWidget(b)
        mv.addLayout(actions2)

        # Wire actions
        self.btn_import.clicked.connect(self._import_csv)
        self.btn_export_page.clicked.connect(self._export_csv_current_page)
        self.btn_export_all.clicked.connect(self._export_csv_all_filtered)
        self.btn_template.clicked.connect(self._save_csv_template)
        self.btn_new.clicked.connect(self._new)
        self.btn_save.clicked.connect(self._save)
        self.btn_del.clicked.connect(self._delete)

    # ----- Styles -----
    def _install_styles(self):
        self.setStyleSheet(f"""
        QWidget {{ background:{PALETTE['surface']}; }}
        QFrame#sidebar {{ background:{PALETTE['sidebar']}; color:{PALETTE['sidebar_text']}; }}
        QLabel#section {{ color:white; font-weight:700; margin-bottom:8px; }}
        QPushButton#navActive {{
            background:{PALETTE['sidebar']}; color:white; border:0; text-align:left; padding:12px 14px;
            border-radius:10px; font-weight:700;
        }}
        QPushButton#navDisabled {{ background:{PALETTE['sidebar']}; color:rgba(255,255,255,0.65);
            border:0; text-align:left; padding:12px 14px; border-radius:10px; }}

        QFrame#card {{ background:{PALETTE['card']}; border:1px solid {PALETTE['border']}; border-radius:12px; }}
        QLabel#muted {{ color:{PALETTE['muted']}; }}
        QTableView {{ gridline-color:{PALETTE['border']}; selection-background-color:#E3F2FD; }}

        QPushButton#btnGreen {{ background:{PALETTE['green']}; color:white; border:0; border-radius:10px; padding:10px 16px; font-weight:700; }}
        QPushButton#btnBlue  {{ background:{PALETTE['blue']};  color:white; border:0; border-radius:10px; padding:10px 16px; font-weight:700; }}
        QPushButton#btnRed   {{ background:{PALETTE['red']};   color:white; border:0; border-radius:10px; padding:10px 16px; font-weight:700; }}

        QPushButton#btnBlueFlat {{
            background: transparent; color: {PALETTE['blue']}; border: 1px solid {PALETTE['blue']};
            border-radius: 8px; padding: 6px 10px; font-weight: 600;
        }}
        QPushButton#btnBlueFlat:hover {{ background: rgba(37,99,235,0.08); }}
        QPushButton#btnGreyFlat {{
            background: #eceff1; color: #37474F; border: 1px solid #cfd8dc;
            border-radius: 8px; padding: 6px 10px; font-weight: 600;
        }}

        QPushButton#hdrFilterBtn {{
            border: 0; background: transparent; padding: 0; min-width: 18px; min-height: 18px;
        }}
        QPushButton#hdrFilterBtn[active="true"] {{
            background: rgba(37,99,235,0.16); border-radius: 4px;
        }}
        QLabel#hdrSortLbl {{ color: #6b7280; font-size: 10px; }}
        """)

    # ----- Data flow -----
    def _debounced(self, fn, ms=250):
        if not hasattr(self, "_debounce"): self._debounce = QTimer(self); self._debounce.setSingleShot(True)
        try: self._debounce.timeout.disconnect()
        except Exception: pass
        self._debounce.timeout.connect(fn); self._debounce.start(ms)

    def _on_global_search(self, _):
        self._debounced(lambda: (self._refresh(), self.page_proxy.set_page(1), self._update_pagination_labels()), 200)

    def _refresh(self):
        rows = self.repo.list(self.search.text().strip() or None)
        if not hasattr(self, "base_model"): self.base_model = PatientTableModel(rows)
        self.base_model.set_rows(rows)

    def _update_pagination_labels(self):
        tp = self.page_proxy.total_pages()
        if self.page_proxy.page() > tp: self.page_proxy.set_page(tp)
        if self.page_proxy.page() < 1:  self.page_proxy.set_page(1)

        page = self.page_proxy.page()
        size = self.page_proxy.page_size()
        total_all = self.base_model.rowCount()
        total_filtered = self.filter_proxy.rowCount()
        start = (page - 1) * size
        end = min(start + size, total_filtered)

        self.lbl_page.setText(f"Page {page} / {tp}")
        if total_filtered == 0:
            self.lbl_range.setText(f"Showing 0 of 0 (filtered from {total_all})")
        else:
            self.lbl_range.setText(f"Showing {start+1}–{end} of {total_filtered} (filtered from {total_all})")

        self.btn_prev.setEnabled(page > 1)
        self.btn_next.setEnabled(page < tp)

        # header filter active-state highlight
        for col, btn in self._hdr_btns.items():
            active = "true" if col in self.filter_proxy.include_values or (col == 4 and (self.filter_proxy.f_birth_from or self.filter_proxy.f_birth_to)) else "false"
            btn.setProperty("active", active)
            btn.style().unpolish(btn); btn.style().polish(btn)

    # ----- Selection & form -----
    def _on_select(self, *_):
        idxs = self.table.selectionModel().selectedRows()
        if not idxs:
            self._load_to_form(None); self._set_edit_enabled(False); return
        idx_view = idxs[0]
        idx_in_filter = self.page_proxy.mapToSource(idx_view)
        src_row = self.filter_proxy.mapToSource(idx_in_filter).row()
        p = self.base_model.rows[src_row]
        self._load_to_form(p)
        self._set_edit_enabled(True)

    def _load_to_form(self, p: PatientDTO | None):
        self.current_patient_id = p.id if p else None
        self.e_id.setText(str(p.id) if p and p.id is not None else "")
        self.e_cin.setText(p.cin if p else "")
        self.e_first.setText(p.first_name if p else "")
        self.e_last.setText(p.last_name if p else "")
        self.e_bd.set_date(p.birth_date if p else None)
        self.e_phone.setText(p.phone or "" if p else "")
        self.e_email.setText(p.email or "" if p else "")
        self.e_notes.setPlainText(p.notes or "" if p else "")
        self.btn_del.setEnabled(self.current_patient_id is not None)

    def _set_edit_enabled(self, enabled: bool):
        for w in (self.e_cin, self.e_first, self.e_last, self.e_bd, self.e_phone, self.e_email, self.e_notes):
            w.setEnabled(enabled)
        self.btn_save.setEnabled(enabled)

    def _force_upper(self, w: QLineEdit, s: str):
        up = s.upper()
        if s != up:
            pos = w.cursorPosition()
            w.blockSignals(True); w.setText(up); w.setCursorPosition(pos); w.blockSignals(False)

    # ----- CRUD -----
    def _new(self):
        self.table.clearSelection()
        self._load_to_form(None)
        self._set_edit_enabled(True)
        self.e_cin.setFocus()

    def _collect(self) -> PatientDTO | None:
        cin = self.e_cin.text().strip().upper()
        fn = self.e_first.text().strip()
        ln = self.e_last.text().strip()
        if not cin or not fn or not ln:
            self._msg_critical("Validation", "CIN, First name and Last name are required.")
            return None
        return PatientDTO(
            id=self.current_patient_id, cin=cin, first_name=fn, last_name=ln,
            birth_date=self.e_bd.get_date(),
            phone=self.e_phone.text().strip() or None,
            email=self.e_email.text().strip() or None,
            notes=self.e_notes.toPlainText().strip() or None
        )

    def _save(self):
        dto = self._collect()
        if not dto: return
        try:
            if dto.id is None:
                self.repo.create(dto)
                self._msg_info("Patient created", f"Patient with CIN '{dto.cin}' has been created.")
            else:
                if not self._confirm("Confirm modification", "Save changes to this patient?"): return
                self.repo.update(dto)
        except ValueError as e:
            self._msg_critical("Duplicate CIN", str(e)); return
        self._refresh(); self.page_proxy.set_page(1); self._update_pagination_labels()
        self._reselect_cin(dto.cin)

    def _delete(self):
        if self.current_patient_id is None:
            self._msg_info("Delete", "Select a patient first."); return
        if not self._confirm("Confirm deletion", "Delete this patient?"): return
        self.repo.delete(self.current_patient_id)
        self._load_to_form(None); self._refresh(); self._set_edit_enabled(False)
        self._update_pagination_labels()

    def _reselect_cin(self, cin: str):
        for row in range(self.base_model.rowCount()):
            if self.base_model.at(row).cin == cin:
                src_idx = self.base_model.index(row, 0)
                idx_in_filter = self.filter_proxy.mapFromSource(src_idx)
                proxy_row = self.page_proxy.mapFromSource(idx_in_filter).row()
                if proxy_row >= 0: self.table.selectRow(proxy_row)
                break

    # ----- CSV -----
    def _export_csv_current_page(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export current page", f"patients_page_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", "CSV Files (*.csv)"
        )
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(CSV_HEADERS)
            for r in range(self.page_proxy.rowCount()):
                idx_page = self.page_proxy.index(r, 0)
                idx_filter = self.page_proxy.mapToSource(idx_page)
                src_row = self.filter_proxy.mapToSource(idx_filter).row()
                p = self.base_model.at(src_row)
                w.writerow([
                    p.cin, p.first_name, p.last_name,
                    p.birth_date.isoformat() if p.birth_date else "",
                    p.phone or "", p.email or "", p.notes or ""
                ])
        self._msg_info("Export", f"Exported {self.page_proxy.rowCount()} patient(s).")

    def _export_csv_all_filtered(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export filtered patients", f"patients_filtered_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", "CSV Files (*.csv)"
        )
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(CSV_HEADERS)
            for r in range(self.filter_proxy.rowCount()):
                idx_filter = self.filter_proxy.index(r, 0)
                src_row = self.filter_proxy.mapToSource(idx_filter).row()
                p = self.base_model.at(src_row)
                w.writerow([
                    p.cin, p.first_name, p.last_name,
                    p.birth_date.isoformat() if p.birth_date else "",
                    p.phone or "", p.email or "", p.notes or ""
                ])
        self._msg_info("Export", f"Exported {self.filter_proxy.rowCount()} patient(s).")

    def _save_csv_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV Template", "patients_template.csv", "CSV Files (*.csv)")
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write(",".join(CSV_HEADERS) + "\n")
            f.write("# SAMPLE: AA123456,John,Doe,1990-05-17,+212600000000,john@doe.com,Notes here\n")
        self._msg_info("Template", f"Template saved to:\n{path}")

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import patients from CSV", "", "CSV Files (*.csv)")
        if not path: return
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            raw = f.read().splitlines()
        if not raw: self._msg_warn("Import", "The CSV file is empty."); return

        kept = []
        for i, line in enumerate(raw):
            if i == 0: kept.append(line); continue
            if not line.strip(): continue
            if line.lstrip().startswith("#"): continue
            kept.append(line)

        reader = csv.DictReader(kept)
        missing = [h for h in CSV_HEADERS if h not in (reader.fieldnames or [])]
        if missing:
            self._msg_critical("Import", "Missing headers: " + ", ".join(missing) +
                               "\nExpected: " + ", ".join(CSV_HEADERS)); return

        created, errors = 0, []
        for idx, row in enumerate(reader, start=2):
            try:
                cin = (row.get("cin") or "").strip().upper()
                first = (row.get("first_name") or "").strip()
                last  = (row.get("last_name") or "").strip()
                if not cin or not first or not last:
                    raise ValueError("cin, first_name and last_name are required")
                bd = parse_birth_date(row.get("birth_date", ""))

                dto = PatientDTO(
                    id=None, cin=cin, first_name=first, last_name=last, birth_date=bd,
                    phone=(row.get("phone") or "").strip() or None,
                    email=(row.get("email") or "").strip() or None,
                    notes=(row.get("notes") or "").strip() or None
                )
                self.repo.create(dto); created += 1
            except Exception as e:
                errors.append({
                    "line": idx, "error": str(e),
                    "cin": (row.get("cin") or "").strip(),
                    "first_name": (row.get("first_name") or "").strip(),
                    "last_name":  (row.get("last_name") or "").strip(),
                    "birth_date": (row.get("birth_date") or "").strip(),
                    "phone":      (row.get("phone") or "").strip(),
                    "email":      (row.get("email") or "").strip(),
                    "notes":      (row.get("notes") or "").strip(),
                })

        self._refresh(); self.page_proxy.set_page(1); self._update_pagination_labels()
        self._show_import_result(created, errors)

    def _show_import_result(self, created: int, errors: list[dict]):
        if not errors:
            self._msg_info("Import complete", f"Imported {created} patient(s)."); return
        preview = [f"Imported {created} patient(s).", f"Encountered {len(errors)} error(s):", ""]
        for e in errors[:5]:
            preview.append(f"Line {e['line']}: {e['error']} (CIN='{e['cin']}', first='{e['first_name']}', last='{e['last_name']}')")
        if len(errors) > 5: preview.append(f"... and {len(errors)-5} more")
        choice = self._confirm("Import completed with errors", "\n".join(preview) + "\n\nSave a CSV error report?",
                               yes_no=True)
        if not choice: return
        path, _ = QFileDialog.getSaveFileName(self, "Save Import Error Report",
                f"patients_import_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", "CSV Files (*.csv)")
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["line","error",*CSV_HEADERS])
            for e in errors:
                w.writerow([e["line"],e["error"],e["cin"],e["first_name"],e["last_name"],
                            e["birth_date"],e["phone"],e["email"],e["notes"]])
        self._msg_info("Saved", f"Error report saved to:\n{path}")

    # ----- Header filter buttons (stacked: sort label + funnel button) -----
    def _install_header_filter_buttons(self):
        header = self.table.horizontalHeader()
        self._hdr_btns: dict[int, QPushButton] = {}
        self._hdr_sorts: dict[int, QLabel] = {}

        for col in range(self.table.model().columnCount()):
            # filter button (bottom-right)
            fbtn = QPushButton("", header.viewport())
            fbtn.setObjectName("hdrFilterBtn")
            fbtn.setIcon(self._funnel_icon)
            fbtn.setIconSize(QSize(14, 14))
            fbtn.setFixedSize(18, 18)
            fbtn.clicked.connect(lambda _, c=col: self._open_filter_popup(c))
            self._hdr_btns[col] = fbtn

            # sort label (top-right)
            slbl = QLabel("⇅", header.viewport())
            slbl.setObjectName("hdrSortLbl")
            slbl.setAlignment(Qt.AlignCenter)
            slbl.setFixedSize(18, 12)
            self._hdr_sorts[col] = slbl

        def reposition():
            for c in range(self.table.model().columnCount()):
                pos = header.sectionPosition(c)
                width = header.sectionSize(c)
                x_right = pos + width - 18
                self._hdr_sorts[c].move(x_right, 0)                               # top-right
                self._hdr_btns[c].move(x_right, header.height() - 18 - 2)         # bottom-right
                self._hdr_sorts[c].raise_(); self._hdr_btns[c].raise_()

        header.sectionResized.connect(reposition)
        header.geometriesChanged.connect(reposition)
        reposition()

    def _open_filter_popup(self, col: int):
        dlg = QDialog(self); dlg.setWindowTitle("Filter")
        lay = QVBoxLayout(dlg)

        # Birth date column (date range)
        if col == 4:
            from_lbl, to_lbl = QLabel("From"), QLabel("To")
            from_d, to_d = DateField(), DateField()
            from_d.set_date(self.filter_proxy.f_birth_from)
            to_d.set_date(self.filter_proxy.f_birth_to)
            hl = QHBoxLayout(); hl.addWidget(from_lbl); hl.addWidget(from_d); hl.addWidget(to_lbl); hl.addWidget(to_d)
            lay.addLayout(hl)

            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
            lay.addWidget(btns)

            def accept():
                self.filter_proxy.set_filters(
                    cin=self.filter_proxy.f_cin, first=self.filter_proxy.f_first, last=self.filter_proxy.f_last,
                    phone=self.filter_proxy.f_phone, email=self.filter_proxy.f_email,
                    birth_from=from_d.get_date(), birth_to=to_d.get_date()
                )
                self.page_proxy.set_page(1); self._update_pagination_labels(); dlg.accept()

            def reset():
                self.filter_proxy.set_filters(
                    cin=self.filter_proxy.f_cin, first=self.filter_proxy.f_first, last=self.filter_proxy.f_last,
                    phone=self.filter_proxy.f_phone, email=self.filter_proxy.f_email,
                    birth_from=None, birth_to=None
                )
                self.page_proxy.set_page(1); self._update_pagination_labels()

            btns.accepted.connect(accept); btns.rejected.connect(dlg.reject)
            btns.button(QDialogButtonBox.Reset).clicked.connect(reset)

        else:
            lst = QListWidget(dlg); lst.setSelectionMode(QListWidget.MultiSelection)
            seen = []
            # Build unique values from the CURRENT filtered set (so users can refine)
            for r in range(self.filter_proxy.rowCount()):
                idx = self.filter_proxy.index(r, col)
                val = self.filter_proxy.data(idx, Qt.DisplayRole) or ""
                sval = str(val)
                if sval not in seen: seen.append(sval)
            for v in seen:
                it = QListWidgetItem(v or "(blank)")
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                cur = self.filter_proxy.include_values.get(col)
                checked = (not cur) or ((v in cur) if v else ("" in cur))
                it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                lst.addItem(it)
            lay.addWidget(lst)

            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
            lay.addWidget(btns)

            def accept():
                chosen = set()
                for i in range(lst.count()):
                    item = lst.item(i)
                    if item.checkState() == Qt.Checked:
                        chosen.add("" if item.text() == "(blank)" else item.text())
                if len(chosen) == lst.count(): chosen = set()  # all checked => clear restriction
                self.filter_proxy.set_inclusion_values(col, chosen if chosen else None)
                self.page_proxy.set_page(1); self._update_pagination_labels(); dlg.accept()

            def reset():
                self.filter_proxy.set_inclusion_values(col, None)
                self.page_proxy.set_page(1); self._update_pagination_labels()

            btns.accepted.connect(accept); btns.rejected.connect(dlg.reject)
            btns.button(QDialogButtonBox.Reset).clicked.connect(reset)

        dlg.exec()

    # ----- dialogs -----
    def _msg_info(self, title, text): QMessageBox.information(self, title, text)
    def _msg_warn(self, title, text): QMessageBox.warning(self, title, text)
    def _msg_critical(self, title, text): QMessageBox.critical(self, title, text)
    def _confirm(self, title, text, yes_no=False):
        btns = QMessageBox.Yes | QMessageBox.No if yes_no else QMessageBox.Yes | QMessageBox.No
        return QMessageBox.question(self, title, text, btns) == QMessageBox.Yes

    # ----- tiny icon painter -----
    def _make_funnel_icon(self, size: int = 16, color: str = "#6b7280") -> QIcon:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        col = QColor(color)
        painter.setPen(Qt.NoPen)
        painter.setBrush(col)

        # Funnel shape (top wide, narrowing to a short stem)
        top = 3
        left = 3
        right = size - 3
        mid_y = int(size * 0.55)
        stem_top_x1 = int(size * 0.45)
        stem_top_x2 = int(size * 0.55)
        bottom = size - 3

        poly = QPolygon([
            QPoint(left, top),  # top-left
            QPoint(right, top),  # top-right
            QPoint(stem_top_x2, mid_y),  # right slope to throat
            QPoint(stem_top_x2, bottom),  # stem right
            QPoint(stem_top_x1, bottom),  # stem left
            QPoint(stem_top_x1, mid_y),  # left slope back to throat
        ])

        painter.drawPolygon(poly)
        painter.end()
        return QIcon(pm)


# ---- entrypoint (adds CIN unique index if missing) ----
def run():
    engine = make_engine(DB_PATH)
    init_db(engine, Base)
    with engine.begin() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(patients)").fetchall()]
        if "cin" not in cols:
            conn.exec_driver_sql("ALTER TABLE patients ADD COLUMN cin VARCHAR(64)")
        conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_patients_cin ON patients(cin)")
    SessionFactory = make_session_factory(engine)
    with SessionFactory() as s:
        app = QApplication(sys.argv)
        w = ManagePatientsWindow(s)
        w.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    run()
