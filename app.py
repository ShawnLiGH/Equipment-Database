from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
from sqlmodel import SQLModel, Field, Session, create_engine, select

# -------------------------------------------------------------------
# Streamlit reruns your script frequently.
# SQLAlchemy/SQLModel can complain that tables are "already defined".
# Clearing metadata before model declarations prevents that.
# -------------------------------------------------------------------
SQLModel.metadata.clear()

# -------------------------------------------------------------------
# Database config
# NOTE: On Streamlit Community Cloud, the filesystem can be ephemeral.
# This demo DB may reset on redeploy/restart. That's OK for a demo.
# -------------------------------------------------------------------
DB_PATH = "equipment_demo.db"


@st.cache_resource
def get_engine():
    # check_same_thread helps avoid SQLite threading issues in web apps
    return create_engine(
        f"sqlite:///{DB_PATH}",
        echo=False,
        connect_args={"check_same_thread": False},
    )


engine = get_engine()


# ----------------------------
# Database models
# ----------------------------
class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    job_number: str = ""


class Equipment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    equipment_type: str = ""  # e.g., Pump, Blower, Valve
    subtype: str = ""  # e.g., Centrifugal
    manufacturer: str = ""
    model: str = ""

    # a few example "extracted" parameters (extend later per type)
    power_value: Optional[float] = None
    power_unit: str = ""  # hp / kW
    flow_value: Optional[float] = None
    flow_unit: str = ""  # gpm / m3/h
    head_value: Optional[float] = None
    head_unit: str = ""  # ft / m

    verified: bool = False


class ProjectEquipment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    equipment_id: int = Field(foreign_key="equipment.id")

    pid_tag: str = ""  # manual entry
    status: str = "TBD"  # existing/new/replace/remove/TBD
    quantity: int = 1
    location: str = ""
    notes: str = ""


class Quote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_equipment_id: int = Field(foreign_key="projectequipment.id")

    vendor: str = ""
    price_value: Optional[float] = None
    currency: str = "USD"
    lead_time: str = ""
    quote_date: Optional[date] = None


class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    equipment_id: int = Field(foreign_key="equipment.id")

    doc_type: str = ""  # cutsheet/spec/submittal/quote
    file_path: str = ""  # store path to pdf
    version: str = ""
    doc_date: Optional[date] = None


# ----------------------------
# Setup
# ----------------------------
def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    return Session(engine)


init_db()


# ----------------------------
# Helpers
# ----------------------------
def export_equipment_list(project_id: int, out_csv: str):
    with get_session() as s:
        rows = s.exec(
            select(ProjectEquipment, Equipment)
            .where(ProjectEquipment.project_id == project_id)
            .join(Equipment, Equipment.id == ProjectEquipment.equipment_id)
        ).all()

    data = []
    for pe, eq in rows:
        data.append(
            {
                "P&ID Tag": pe.pid_tag,
                "Status": pe.status,
                "Qty": pe.quantity,
                "Type": eq.equipment_type,
                "Subtype": eq.subtype,
                "Manufacturer": eq.manufacturer,
                "Model": eq.model,
                "Power": f"{eq.power_value or ''} {eq.power_unit}".strip(),
                "Flow": f"{eq.flow_value or ''} {eq.flow_unit}".strip(),
                "Head": f"{eq.head_value or ''} {eq.head_unit}".strip(),
                "Location": pe.location,
                "Notes": pe.notes,
            }
        )
    pd.DataFrame(data).to_csv(out_csv, index=False)


def export_cost_estimate(project_id: int, out_csv: str):
    with get_session() as s:
        rows = s.exec(
            select(ProjectEquipment, Equipment, Quote)
            .where(ProjectEquipment.project_id == project_id)
            .join(Equipment, Equipment.id == ProjectEquipment.equipment_id)
            .join(Quote, Quote.project_equipment_id == ProjectEquipment.id, isouter=True)
        ).all()

    data = []
    for pe, eq, qt in rows:
        unit_price = qt.price_value if (qt and qt.price_value is not None) else None
        ext = (unit_price * pe.quantity) if unit_price is not None else None
        data.append(
            {
                "P&ID Tag": pe.pid_tag,
                "Status": pe.status,
                "Qty": pe.quantity,
                "Manufacturer": eq.manufacturer,
                "Model": eq.model,
                "Vendor": qt.vendor if qt else "",
                "Unit Price": unit_price,
                "Currency": (qt.currency if qt else "USD"),
                "Lead Time": (qt.lead_time if qt else ""),
                "Extended": ext,
            }
        )
    pd.DataFrame(data).to_csv(out_csv, index=False)


def build_submittal_package(project_id: int, out_folder: str):
    out = Path(out_folder)
    out.mkdir(parents=True, exist_ok=True)

    with get_session() as s:
        rows = s.exec(
            select(ProjectEquipment, Equipment)
            .where(ProjectEquipment.project_id == project_id)
            .join(Equipment, Equipment.id == ProjectEquipment.equipment_id)
        ).all()

        for pe, eq in rows:
            # pick "submittal" doc if exists, else "cutsheet"
            sub = s.exec(
                select(Document)
                .where(Document.equipment_id == eq.id)
                .where(Document.doc_type.in_(["submittal", "cutsheet"]))
                .order_by(Document.doc_type.desc())
            ).first()

            if not sub or not sub.file_path or not os.path.exists(sub.file_path):
                continue

            safe_tag = (pe.pid_tag or f"EQ-{pe.id}").replace("/", "-").replace("\\", "-")
            safe_mfg = (eq.manufacturer or "MFG").replace("/", "-")
            safe_model = (eq.model or "MODEL").replace("/", "-")
            dst_name = f"{safe_tag}__{safe_mfg}__{safe_model}__{sub.doc_type}.pdf"
            shutil.copy2(sub.file_path, out / dst_name)


# ----------------------------
# UI
# ----------------------------
st.title("Equipment Database Demo (SQLite + Streamlit)")

# Projects
st.header("1) Project")
with get_session() as s:
    projects = s.exec(select(Project)).all()

proj_names = ["(create new)"] + [f"{p.id} - {p.name}" for p in projects]
choice = st.selectbox("Select project", proj_names)

if choice == "(create new)":
    name = st.text_input("Project name")
    job = st.text_input("Job number")
    if st.button("Create project"):
        with get_session() as s:
            p = Project(name=name, job_number=job)
            s.add(p)
            s.commit()
        st.rerun()
    st.stop()

project_id = int(choice.split(" - ")[0])

# Add Equipment
st.header("2) Add Equipment (simulate extracted JSON)")
col1, col2 = st.columns(2)
with col1:
    eq_type = st.text_input("Equipment Type", "Pump")
    subtype = st.text_input("Subtype", "Centrifugal")
    manufacturer = st.text_input("Manufacturer", "Xylem")
    model = st.text_input("Model", "1234")
with col2:
    power_value = st.number_input("Power value", value=0.0, step=1.0)
    power_unit = st.text_input("Power unit", "hp")
    flow_value = st.number_input("Flow value", value=0.0, step=1.0)
    flow_unit = st.text_input("Flow unit", "gpm")

head_value = st.number_input("Head value", value=0.0, step=1.0)
head_unit = st.text_input("Head unit", "ft")

if st.button("Add equipment to DB"):
    with get_session() as s:
        eq = Equipment(
            equipment_type=eq_type,
            subtype=subtype,
            manufacturer=manufacturer,
            model=model,
            power_value=(power_value if power_value > 0 else None),
            power_unit=power_unit.strip(),
            flow_value=(flow_value if flow_value > 0 else None),
            flow_unit=flow_unit.strip(),
            head_value=(head_value if head_value > 0 else None),
            head_unit=head_unit.strip(),
        )
        s.add(eq)
        s.commit()
        s.refresh(eq)

        pe = ProjectEquipment(project_id=project_id, equipment_id=eq.id)
        s.add(pe)
        s.commit()
    st.success("Added equipment and created a project instance.")

# Project equipment table + manual fields
st.header("3) Project Equipment (add P&ID tag / status / quote)")
with get_session() as s:
    rows = s.exec(
        select(ProjectEquipment, Equipment)
        .where(ProjectEquipment.project_id == project_id)
        .join(Equipment, Equipment.id == ProjectEquipment.equipment_id)
    ).all()

if not rows:
    st.info("No project equipment yet.")
    st.stop()

for pe, eq in rows:
    st.subheader(f"Item {pe.id}: {eq.equipment_type} - {eq.manufacturer} {eq.model}")
    c1, c2, c3, c4 = st.columns(4)
    pid_tag = c1.text_input("P&ID Tag", value=pe.pid_tag, key=f"tag_{pe.id}")
    status = c2.selectbox(
        "Status",
        ["TBD", "existing", "new", "replace", "remove"],
        index=["TBD", "existing", "new", "replace", "remove"].index(pe.status),
        key=f"st_{pe.id}",
    )
    qty = c3.number_input("Qty", value=pe.quantity, min_value=1, step=1, key=f"q_{pe.id}")
    location = c4.text_input("Location", value=pe.location, key=f"loc_{pe.id}")

    notes = st.text_area("Notes", value=pe.notes, key=f"notes_{pe.id}")

    with get_session() as s:
        qt = s.exec(select(Quote).where(Quote.project_equipment_id == pe.id)).first()

    qc1, qc2, qc3 = st.columns(3)
    vendor = qc1.text_input("Vendor", value=(qt.vendor if qt else ""), key=f"v_{pe.id}")
    price = qc2.number_input(
        "Unit Price",
        value=float(qt.price_value) if (qt and qt.price_value) else 0.0,
        step=100.0,
        key=f"p_{pe.id}",
    )
    lead = qc3.text_input("Lead Time", value=(qt.lead_time if qt else ""), key=f"l_{pe.id}")

    if st.button("Save changes", key=f"save_{pe.id}"):
        with get_session() as s:
            pe_db = s.get(ProjectEquipment, pe.id)
            pe_db.pid_tag = pid_tag
            pe_db.status = status
            pe_db.quantity = int(qty)
            pe_db.location = location
            pe_db.notes = notes
            s.add(pe_db)

            qt_db = s.exec(select(Quote).where(Quote.project_equipment_id == pe.id)).first()
            if not qt_db:
                qt_db = Quote(project_equipment_id=pe.id)

            qt_db.vendor = vendor
            qt_db.price_value = (price if price > 0 else None)
            qt_db.lead_time = lead
            s.add(qt_db)

            s.commit()
        st.success("Saved.")

# Exports
st.header("4) Generate deliverables")
out1 = st.text_input("Equipment List CSV path", value="equipment_list.csv")
out2 = st.text_input("Cost Estimate CSV path", value="cost_estimate.csv")
out_folder = st.text_input("Submittal package folder", value="submittal_package")

cA, cB, cC = st.columns(3)
if cA.button("Export Equipment List"):
    export_equipment_list(project_id, out1)
    st.success(f"Wrote {out1}")

if cB.button("Export Cost Estimate"):
    export_cost_estimate(project_id, out2)
    st.success(f"Wrote {out2}")

if cC.button("Build Submittal Package Folder"):
    build_submittal_package(project_id, out_folder)
    st.success(f"Built folder: {out_folder}")
