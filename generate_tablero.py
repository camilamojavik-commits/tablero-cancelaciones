#!/usr/bin/env python3
"""
Tablero de Cancelaciones — Coderhouse
Genera el HTML con datos frescos de la API.
Requiere: CODERHOUSE_API_URL, CODERHOUSE_API_KEY como variables de entorno.
"""

import os
import re
import json
import requests
from datetime import datetime, timezone, timedelta
from collections import Counter

ARGENTINA_TZ = timezone(timedelta(hours=-3))

def to_argentina_date(utc_iso):
        """Convert UTC ISO timestamp to Argentina date (UTC-3, no DST)."""
        try:
                    dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
                    return (dt - timedelta(hours=3)).strftime("%Y-%m-%d")
        except Exception:
                    return (utc_iso or "")[:10]

API_URL = os.environ["CODERHOUSE_API_URL"].rstrip("/")
API_KEY = os.environ["CODERHOUSE_API_KEY"]
FINANCE_API_KEY = os.environ.get("CODERHOUSE_FINANCE_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}
FINANCE_HEADERS = {"X-API-Key": FINANCE_API_KEY} if FINANCE_API_KEY else HEADERS
PASSWORD_HASH = os.environ.get("TABLERO_PASSWORD_HASH", "a7cd2c7716c339dec5e9d8da39c54c86aaffabe839287323ffd1bb97384a8217")


def api_get(path, headers=None):
    h = headers or HEADERS
    r = requests.get(f"{API_URL}{path}", headers=h, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_all_active_cohorts():
    """Fetch ALL IN_PROGRESS cohorts using the admin endpoint (student key has access)."""
    items, page = [], 1
    while True:
        try:
            data = api_get(
                f"/student/enrollment/m2m/admin/cohorts?status=IN_PROGRESS&page={page}&limit=100"
            )
            # Response shape: {"data": [...], "pagination": {"totalPages": N, ...}}
            batch = data.get("data") or data.get("items", [])
            if not batch:
                break
            items.extend(batch)
            total_pages = (data.get("pagination") or {}).get("totalPages") or data.get("totalPages", 1)
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            print(f"  Warning fetching all cohorts page {page}: {e}")
            break
    return items


def fetch_all_incidents(status):
    items, page = [], 1
    while True:
        data = api_get(f"/education/scheduling/m2m/incidents?status={status}&page={page}&pageSize=100")
        items.extend(data.get("items", []))
        if page >= data.get("totalPages", 1):
            break
        page += 1
    return items



def is_test_cohort(name):
    if not name:
        return True
    if re.match(r"^Cohort \d{4}/\d{2}/\d{2} \(\d+\)$", name):
        return True
    if "demo" in name.lower():
        return True
    return False


def fetch_cohort(cohort_id, cache={}):
    if cohort_id in cache:
        return cache[cohort_id]
    try:
        data = api_get(f"/student/enrollment/m2m/cohorts/{cohort_id}")
        result = {
            "name": data.get("name", ""),
            "commissionNumber": str(data.get("commissionNumber", "")),
            "weekDays": data.get("weekDays") or [],
            "startDate": (data.get("startDate") or "")[:10],
            "endDate": (data.get("endDate") or "")[:10],
        }
    except Exception:
        result = {"name": "", "commissionNumber": "", "weekDays": [], "startDate": "", "endDate": ""}
    cache[cohort_id] = result
    return result


def build_dataset():
    print("Fetching incidents...")
    all_incidents = []
    for status in ["OPEN", "IN_PROGRESS", "RESOLVED", "CANCELLED"]:
        items = fetch_all_incidents(status)
        print(f"  {status}: {len(items)}")
        all_incidents.extend(items)

    filtered = [i for i in all_incidents if i.get("type") in ("INSTRUCTOR_ABSENCE", "CLASS_ISSUES")]
    print(f"After type filter: {len(filtered)}")

    target_ids = set(i.get("targetId", "") for i in filtered if i.get("targetId"))
    print(f"Fetching {len(target_ids)} cohorts...")

    cohort_map = {cid: fetch_cohort(cid) for cid in target_ids}

    records = []
    for inc in filtered:
        cohort = cohort_map.get(inc.get("targetId", ""), {})
        name = cohort.get("name", "")
        if is_test_cohort(name):
            continue
        records.append({
            "id": inc["id"],
            "date": to_argentina_date(inc.get("createdAt", "")),  # UTC→ARG (UTC-3)
            "type": inc["type"],
            "status": inc["status"],
            "summary": inc.get("summary", ""),
            "description": inc.get("description", ""),
            "cohortName": name,
            "commissionNumber": cohort.get("commissionNumber", ""),
        })
    print(f"Final incident records: {len(records)}")

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Fetch ALL active cohorts for correct class density denominator
    print("Fetching ALL active cohorts for class density...")
    all_active_raw = fetch_all_active_cohorts()
    print(f"  All active cohorts fetched: {len(all_active_raw)}")

    if all_active_raw:
        # Use full universe — correct denominator
        source = all_active_raw
        denominator_label = "todas las cohortes activas"
    else:
        # Fallback: use only cohorts that had incidents
        print("  Fallback: using incident cohorts only")
        source = [
            {**v, "id": k} for k, v in cohort_map.items()
            if v.get("weekDays")
        ]
        denominator_label = "cohortes con incidents"

    cohort_schedule = []
    seen_commissions = set()
    for c in source:
        name = c.get("name", "")
        if is_test_cohort(name):
            continue
        week_days = c.get("weekDays") or []
        if not week_days:
            continue
        comm = str(c.get("commissionNumber", ""))
        if comm in seen_commissions:
            continue
        seen_commissions.add(comm)
        cohort_schedule.append({
            "commissionNumber": comm,
            "name": name,
            "weekDays": week_days,
            "startDate": (c.get("startDate") or "")[:10],
            "endDate": (c.get("endDate") or today_str)[:10],
        })
    print(f"Cohorts for density ({denominator_label}): {len(cohort_schedule)}")

    return records, cohort_schedule


def generate_html(records, cohort_schedule, password_hash):
    today = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    data_json = json.dumps(records, ensure_ascii=False)
    cohort_data_json = json.dumps(cohort_schedule, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tablero de Cancelaciones — Coderhouse</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Plus Jakarta Sans", sans-serif; background: #F8F2E8; color: #171717; min-height: 100vh; }}

  /* LOGIN OVERLAY */
  #login-overlay {{
    position: fixed; inset: 0; background: #260700; z-index: 9999;
    display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 24px;
  }}
  #login-overlay .logo {{ color: #FF632B; font-size: 28px; font-weight: 800; letter-spacing: 5px; }}
  #login-overlay h2 {{ color: #fff; font-size: 18px; font-weight: 600; letter-spacing: 1px; }}
  #login-overlay input {{
    background: #1a0400; border: 1.5px solid #444; color: #fff; border-radius: 8px;
    padding: 12px 20px; font-size: 15px; font-family: inherit; width: 280px; text-align: center;
    letter-spacing: 3px;
  }}
  #login-overlay input:focus {{ outline: none; border-color: #FF632B; }}
  #login-overlay button {{
    background: #FF632B; color: #fff; border: none; border-radius: 8px; padding: 12px 40px;
    font-family: inherit; font-size: 14px; font-weight: 700; letter-spacing: 1px;
    cursor: pointer; text-transform: uppercase; transition: opacity .2s; width: 280px;
  }}
  #login-overlay button:hover {{ opacity: .85; }}
  #login-error {{ color: #FE64A3; font-size: 13px; min-height: 18px; }}

  /* HEADER */
  .header {{ background: #260700; padding: 28px 40px; display: flex; justify-content: space-between; align-items: center; }}
  .header-left h1 {{ color: #fff; font-size: 22px; font-weight: 800; letter-spacing: 3px; text-transform: uppercase; }}
  .header-left .period-label {{ color: #FE872D; font-size: 13px; font-weight: 500; margin-top: 4px; letter-spacing: 1px; }}
  .logo {{ color: #FF632B; font-size: 20px; font-weight: 800; letter-spacing: 4px; }}

  /* FILTER BAR */
  .filter-bar {{ background: #fff; border-bottom: 2px solid #FF632B; padding: 16px 40px; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }}
  .filter-bar label {{ font-size: 12px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #313131; }}
  .filter-bar input[type="date"] {{
    border: 1.5px solid #BBBBBB; border-radius: 8px; padding: 8px 14px; font-size: 14px;
    font-family: inherit; color: #171717; background: #fff; cursor: pointer; transition: border-color .2s;
  }}
  .filter-bar input[type="date"]:focus {{ outline: none; border-color: #FF632B; }}
  .btn-filter {{ background: #FF632B; color: #fff; border: none; border-radius: 8px; padding: 9px 22px; font-family: inherit; font-size: 13px; font-weight: 700; letter-spacing: 1px; cursor: pointer; text-transform: uppercase; transition: opacity .2s; }}
  .btn-filter:hover {{ opacity: .85; }}
  .btn-reset {{ background: transparent; color: #FF632B; border: 1.5px solid #FF632B; border-radius: 8px; padding: 8px 18px; font-family: inherit; font-size: 12px; font-weight: 600; cursor: pointer; transition: all .2s; }}
  .btn-reset:hover {{ background: #FF632B; color: #fff; }}
  .filter-result-label {{ font-size: 12px; color: #BBBBBB; margin-left: auto; }}

  /* MAIN */
  .main {{ padding: 32px 40px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 32px; }}
  .kpi-card {{ background: #fff; border-radius: 8px; padding: 20px 18px; border-bottom: 3px solid #FF632B; box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
  .kpi-card.kpi-rate-card {{ border-bottom-color: #FE64A3; }}
  .kpi-card .kpi-label {{ font-size: 10px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; color: #BBBBBB; margin-bottom: 10px; }}
  .kpi-card .kpi-value {{ font-size: 36px; font-weight: 800; color: #FF632B; line-height: 1; }}
  .kpi-card.kpi-rate-card .kpi-value {{ color: #FE64A3; }}
  .kpi-card .kpi-sub {{ font-size: 11px; color: #313131; margin-top: 6px; }}
  .kpi-card .kpi-detail {{ font-size: 10px; color: #BBBBBB; margin-top: 3px; }}

  .charts-row {{ display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 24px; }}
  .chart-card {{ background: #fff; border-radius: 8px; padding: 24px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
  .chart-card h2 {{ font-size: 12px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; color: #BBBBBB; margin-bottom: 20px; }}
  .timeline-bars {{ display: flex; align-items: flex-end; gap: 4px; height: 160px; overflow-x: auto; padding-bottom: 4px; }}
  .timeline-bar-col {{ display: flex; flex-direction: column; align-items: center; flex-shrink: 0; }}
  .bar {{ width: 24px; border-radius: 4px 4px 0 0; cursor: pointer; transition: opacity .15s; min-height: 3px; }}
  .bar:hover {{ opacity: .8; }}
  .bar-label {{ font-size: 9px; color: #BBBBBB; margin-top: 4px; text-align: center; width: 28px; }}
  .tooltip-box {{ position: fixed; z-index: 9999; background: #260700; color: #fff; border-radius: 8px; padding: 12px 16px; font-size: 12px; max-width: 300px; pointer-events: none; display: none; line-height: 1.5; box-shadow: 0 4px 20px rgba(0,0,0,.3); }}
  .tooltip-box strong {{ color: #FE872D; display: block; margin-bottom: 6px; }}
  .tooltip-incident {{ border-left: 2px solid #FF632B; padding-left: 8px; margin-top: 6px; font-size: 11px; }}
  .tooltip-stat {{ color: #FE64A3; font-size: 11px; margin-top: 4px; }}

  .ranking-list {{ display: flex; flex-direction: column; gap: 10px; max-height: 280px; overflow-y: auto; }}
  .ranking-item {{ display: flex; flex-direction: column; gap: 4px; }}
  .rank-label {{ font-size: 12px; font-weight: 600; color: #171717; display: flex; justify-content: space-between; align-items: center; gap: 6px; }}
  .rank-count {{ color: #FF632B; font-weight: 700; white-space: nowrap; }}
  .rank-rate {{ background: #fff0f6; color: #FE64A3; font-size: 10px; font-weight: 700; border-radius: 10px; padding: 2px 7px; white-space: nowrap; }}
  .rank-bar-bg {{ background: #F8F2E8; border-radius: 4px; height: 8px; overflow: hidden; }}
  .rank-bar-fill {{ height: 100%; background: #FF632B; border-radius: 4px; }}

  .type-row {{ display: flex; gap: 12px; margin-bottom: 24px; }}
  .type-card {{ flex: 1; background: #fff; border-radius: 8px; padding: 20px 24px; box-shadow: 0 1px 4px rgba(0,0,0,.06); display: flex; align-items: center; gap: 16px; }}
  .type-dot {{ width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0; }}
  .type-name {{ font-size: 11px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #BBBBBB; }}
  .type-count {{ font-size: 28px; font-weight: 800; color: #171717; }}
  .type-pct {{ font-size: 12px; color: #BBBBBB; }}

  .table-section {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.06); overflow: hidden; }}
  .table-header {{ background: #260700; padding: 18px 24px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }}
  .table-header h2 {{ color: #fff; font-size: 11px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; }}
  .table-controls {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
  .search-input {{ border: 1.5px solid #444; background: #1a0400; color: #fff; border-radius: 8px; padding: 7px 14px; font-size: 13px; font-family: inherit; width: 220px; }}
  .search-input::placeholder {{ color: #777; }}
  .search-input:focus {{ outline: none; border-color: #FF632B; }}
  .filter-btn {{ background: transparent; color: #BBBBBB; border: 1px solid #444; border-radius: 20px; padding: 5px 14px; font-family: inherit; font-size: 11px; font-weight: 600; cursor: pointer; transition: all .15s; letter-spacing: .5px; }}
  .filter-btn.active, .filter-btn:hover {{ background: #FF632B; color: #fff; border-color: #FF632B; }}
  .table-result-info {{ color: #FE872D; font-size: 11px; padding: 10px 24px; font-weight: 600; letter-spacing: .5px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  thead th {{ background: #260700; color: #fff; font-size: 10px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; padding: 12px 16px; text-align: left; }}
  tbody tr {{ border-bottom: 1px solid #f0ebe2; transition: background .1s; }}
  tbody tr:hover {{ background: #fdf9f4; }}
  tbody tr.row-absence {{ border-left: 3px solid #FF632B; }}
  tbody tr.row-issues {{ border-left: 3px solid #FE64A3; }}
  td {{ padding: 12px 16px; font-size: 13px; vertical-align: top; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 10px; font-weight: 700; letter-spacing: .5px; text-transform: uppercase; white-space: nowrap; }}
  .badge-absence {{ background: #fff0ea; color: #FF632B; }}
  .badge-issues {{ background: #fff0f6; color: #FE64A3; }}
  .badge-open {{ background: #ffeaea; color: #e53935; }}
  .badge-in-progress {{ background: #fff8e0; color: #f9a825; }}
  .badge-resolved {{ background: #e8f5e9; color: #2e7d32; }}
  .badge-cancelled {{ background: #f0f0f0; color: #888; }}

  .footer {{ background: #171717; padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }}
  .footer-logo {{ color: #FF632B; font-size: 16px; font-weight: 800; letter-spacing: 3px; }}
  .footer-date {{ color: #555; font-size: 12px; }}

  @media (max-width: 900px) {{
    .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .charts-row {{ grid-template-columns: 1fr; }}
    .main {{ padding: 20px; }}
    .header, .filter-bar {{ padding: 16px 20px; }}
    .type-row {{ flex-direction: column; }}
  }}
</style>
</head>
<body>

<!-- LOGIN OVERLAY -->
<div id="login-overlay">
  <div class="logo">CODERHOUSE</div>
  <h2>Tablero de Cancelaciones</h2>
  <input type="password" id="pwd-input" placeholder="Contraseña" onkeydown="if(event.key==='Enter')checkPwd()" />
  <div id="login-error"></div>
  <button onclick="checkPwd()">Ingresar</button>
</div>

<div id="app" style="display:none">
  <div class="header">
    <div class="header-left">
      <h1>Tablero de Cancelaciones</h1>
      <div class="period-label" id="period-label">Cargando...</div>
    </div>
    <div class="logo">CODERHOUSE</div>
  </div>

  <div class="filter-bar">
    <label>Desde</label>
    <input type="date" id="date-from" />
    <label>Hasta</label>
    <input type="date" id="date-to" />
    <button class="btn-filter" onclick="applyFilters()">Filtrar</button>
    <button class="btn-reset" onclick="resetFilters()">Ver todo</button>
    <span class="filter-result-label" id="filter-info"></span>
  </div>

  <div class="main">
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Total cancelaciones</div>
        <div class="kpi-value" id="kpi-total">—</div>
        <div class="kpi-sub">incidents en el período</div>
      </div>
      <div class="kpi-card kpi-rate-card">
        <div class="kpi-label">Tasa de cancelación</div>
        <div class="kpi-value" id="kpi-rate">—</div>
        <div class="kpi-sub" id="kpi-rate-sub">cancel. / clases dictadas</div>
        <div class="kpi-detail" id="kpi-rate-detail"></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Comisiones afectadas</div>
        <div class="kpi-value" id="kpi-commissions">—</div>
        <div class="kpi-sub">comisiones únicas</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Días con incidents</div>
        <div class="kpi-value" id="kpi-days">—</div>
        <div class="kpi-sub">días distintos</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Top curso afectado</div>
        <div class="kpi-value" id="kpi-top-count">—</div>
        <div class="kpi-sub" id="kpi-top-name">—</div>
      </div>
    </div>

    <div class="type-row">
      <div class="type-card">
        <div class="type-dot" style="background:#FF632B"></div>
        <div>
          <div class="type-name">Ausencia de Instructor</div>
          <div class="type-count" id="type-absence-count">—</div>
          <div class="type-pct" id="type-absence-pct">—</div>
        </div>
      </div>
      <div class="type-card">
        <div class="type-dot" style="background:#FE64A3"></div>
        <div>
          <div class="type-name">Problemas de Clase</div>
          <div class="type-count" id="type-issues-count">—</div>
          <div class="type-pct" id="type-issues-pct">—</div>
        </div>
      </div>
    </div>

    <div class="charts-row">
      <div class="chart-card">
        <h2>Incidents por día</h2>
        <div class="timeline-bars" id="timeline-bars"></div>
      </div>
      <div class="chart-card">
        <h2>Top comisiones · cancelaciones / clases</h2>
        <div class="ranking-list" id="ranking-list"></div>
      </div>
    </div>

    <div class="table-section">
      <div class="table-header">
        <h2>Detalle de incidents</h2>
        <div class="table-controls">
          <input type="text" class="search-input" id="search-input" placeholder="Buscar curso, comisión..." oninput="renderTable()" />
          <button class="filter-btn active" data-type="ALL" onclick="setTypeFilter(this)">Todos</button>
          <button class="filter-btn" data-type="INSTRUCTOR_ABSENCE" onclick="setTypeFilter(this)">Ausencias</button>
          <button class="filter-btn" data-type="CLASS_ISSUES" onclick="setTypeFilter(this)">Class Issues</button>
        </div>
      </div>
      <div class="table-result-info" id="table-info"></div>
      <div style="overflow-x:auto">
        <table>
          <thead><tr><th>Fecha</th><th>Curso</th><th>Comisión</th><th>Tipo</th><th>Estado</th><th>Descripción</th></tr></thead>
          <tbody id="table-body"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="footer">
    <div class="footer-logo">CODERHOUSE</div>
    <div class="footer-date">Actualizado el {today} · GitHub Actions</div>
  </div>
</div>

<div class="tooltip-box" id="tooltip"></div>

<script>
const PWD_HASH = "{password_hash}";
const ALL_DATA = {data_json};
const COHORT_DATA = {cohort_data_json};

let filteredData = [...ALL_DATA];
let typeFilter = "ALL";

// ─── AUTH ──────────────────────────────────────────────────────
async function sha256(str) {{
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2,"0")).join("");
}}

async function checkPwd() {{
  const val = document.getElementById("pwd-input").value;
  const hash = await sha256(val);
  if (hash === PWD_HASH) {{
    document.getElementById("login-overlay").style.display = "none";
    document.getElementById("app").style.display = "block";
    sessionStorage.setItem("auth", "1");
    init();
  }} else {{
    document.getElementById("login-error").textContent = "Contraseña incorrecta";
    document.getElementById("pwd-input").value = "";
  }}
}}

if (sessionStorage.getItem("auth") === "1") {{
  document.getElementById("login-overlay").style.display = "none";
  document.getElementById("app").style.display = "block";
  setTimeout(init, 0);
}}

// ─── CLASS COUNT (cruce con cohortes activas) ───────────────────
function countTotalClasses(from, to) {{
  if (!from || !to || !COHORT_DATA || !COHORT_DATA.length) return null;
  let total = 0;
  const fromD = new Date(from + "T00:00:00");
  const toD   = new Date(to   + "T00:00:00");
  COHORT_DATA.forEach(c => {{
    if (!c.weekDays || !c.weekDays.length) return;
    const cohortStart = c.startDate ? new Date(c.startDate + "T00:00:00") : fromD;
    const cohortEnd   = c.endDate   ? new Date(c.endDate   + "T00:00:00") : toD;
    const rangeStart  = new Date(Math.max(fromD.getTime(), cohortStart.getTime()));
    const rangeEnd    = new Date(Math.min(toD.getTime(),   cohortEnd.getTime()));
    if (rangeStart > rangeEnd) return;
    for (let d = new Date(rangeStart); d <= rangeEnd; d.setDate(d.getDate() + 1)) {{
      if (c.weekDays.includes(d.getDay())) total++;
    }}
  }});
  return total || null;
}}

function countCommissionClasses(commNumber, from, to) {{
  if (!commNumber || !from || !to) return null;
  const c = COHORT_DATA.find(x => x.commissionNumber === String(commNumber));
  if (!c || !c.weekDays || !c.weekDays.length) return null;
  let total = 0;
  const fromD       = new Date(from + "T00:00:00");
  const toD         = new Date(to   + "T00:00:00");
  const cohortStart = c.startDate ? new Date(c.startDate + "T00:00:00") : fromD;
  const cohortEnd   = c.endDate   ? new Date(c.endDate   + "T00:00:00") : toD;
  const rangeStart  = new Date(Math.max(fromD.getTime(), cohortStart.getTime()));
  const rangeEnd    = new Date(Math.min(toD.getTime(),   cohortEnd.getTime()));
  if (rangeStart > rangeEnd) return null;
  for (let d = new Date(rangeStart); d <= rangeEnd; d.setDate(d.getDate() + 1)) {{
    if (c.weekDays.includes(d.getDay())) total++;
  }}
  return total || null;
}}

// ─── FILTERS ───────────────────────────────────────────────────
function getRange() {{
  const dates = ALL_DATA.map(d => d.date).sort();
  return {{ min: dates[0] || "", max: dates[dates.length-1] || "" }};
}}

function applyFilters() {{
  const from = document.getElementById("date-from").value;
  const to   = document.getElementById("date-to").value;
  filteredData = ALL_DATA.filter(d => (!from || d.date >= from) && (!to || d.date <= to));
  updateAll();
}}

function resetFilters() {{
  const r = getRange();
  document.getElementById("date-from").value = r.min;
  document.getElementById("date-to").value   = r.max;
  filteredData = [...ALL_DATA];
  updateAll();
}}

function setTypeFilter(btn) {{
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  typeFilter = btn.dataset.type;
  renderTable();
}}

function updateAll() {{
  const from = document.getElementById("date-from").value;
  const to   = document.getElementById("date-to").value;
  const fmt = d => d ? d.split("-").reverse().join("/") : "—";
  document.getElementById("period-label").textContent = `${{fmt(from)}} — ${{fmt(to)}}`;
  document.getElementById("filter-info").textContent  = `${{filteredData.length}} de ${{ALL_DATA.length}} incidents`;
  renderKPIs(); renderTypes(); renderTimeline(); renderRanking(); renderTable();
}}

// ─── KPIs ──────────────────────────────────────────────────────
function renderKPIs() {{
  const from  = document.getElementById("date-from").value;
  const to    = document.getElementById("date-to").value;
  const total = filteredData.length;
  const comms = new Set(filteredData.map(d => d.commissionNumber)).size;
  const days  = new Set(filteredData.map(d => d.date)).size;
  const cc    = {{}};
  filteredData.forEach(d => cc[d.cohortName] = (cc[d.cohortName] || 0) + 1);
  const top   = Object.entries(cc).sort((a,b) => b[1] - a[1])[0];

  document.getElementById("kpi-total").textContent       = total;
  document.getElementById("kpi-commissions").textContent = comms;
  document.getElementById("kpi-days").textContent        = days;
  if (top) {{
    document.getElementById("kpi-top-count").textContent = top[1];
    document.getElementById("kpi-top-name").textContent  = top[0].length > 28 ? top[0].slice(0,26)+"…" : top[0];
  }}

  // Tasa de cancelación vs clases dictadas
  const totalClasses = countTotalClasses(from, to);
  if (totalClasses && total > 0) {{
    const rate = ((total / totalClasses) * 100).toFixed(1);
    document.getElementById("kpi-rate").textContent       = rate + "%";
    document.getElementById("kpi-rate-sub").textContent   = "del total de clases";
    document.getElementById("kpi-rate-detail").textContent = `${{total}} cancel. / ${{totalClasses}} clases`;
  }} else if (totalClasses === 0) {{
    document.getElementById("kpi-rate").textContent        = "0%";
    document.getElementById("kpi-rate-detail").textContent = "Sin clases en el período";
  }} else {{
    document.getElementById("kpi-rate").textContent        = "—";
    document.getElementById("kpi-rate-sub").textContent    = "sin datos de horario";
    document.getElementById("kpi-rate-detail").textContent = "";
  }}
}}

// ─── TYPE BREAKDOWN ────────────────────────────────────────────
function renderTypes() {{
  const total = filteredData.length || 1;
  const ab    = filteredData.filter(d => d.type === "INSTRUCTOR_ABSENCE").length;
  const is    = filteredData.filter(d => d.type === "CLASS_ISSUES").length;
  document.getElementById("type-absence-count").textContent = ab;
  document.getElementById("type-absence-pct").textContent   = `${{((ab/total)*100).toFixed(1)}}% del total`;
  document.getElementById("type-issues-count").textContent  = is;
  document.getElementById("type-issues-pct").textContent    = `${{((is/total)*100).toFixed(1)}}% del total`;
}}

// ─── TIMELINE ──────────────────────────────────────────────────
function renderTimeline() {{
  const container = document.getElementById("timeline-bars");
  const byDate    = {{}};
  filteredData.forEach(d => {{ if (!byDate[d.date]) byDate[d.date] = []; byDate[d.date].push(d); }});
  const dates = Object.keys(byDate).sort();
  const maxH  = 130, maxC = Math.max(...dates.map(d => byDate[d].length), 1);
  const from  = document.getElementById("date-from").value;
  const to    = document.getElementById("date-to").value;
  const tt    = document.getElementById("tooltip");
  container.innerHTML = "";
  dates.forEach(date => {{
    const items = byDate[date];
    const h     = Math.max(6, (items.length / maxC) * maxH);
    const dp    = date.split("-"), short = `${{dp[2]}}/${{dp[1]}}`;
    const classCount = countTotalClasses(date, date);
    const col   = document.createElement("div");
    col.className = "timeline-bar-col";
    col.innerHTML = `<div style="display:flex;align-items:flex-end"><div class="bar" style="height:${{h}}px;background:#FF632B" data-date="${{date}}"></div></div><div class="bar-label">${{short}}</div>`;
    const bar = col.querySelector(".bar");
    bar.addEventListener("mouseenter", e => {{
      let html = `<strong>${{date.split("-").reverse().join("/")}} — ${{items.length}} incident${{items.length!==1?"s":""}}</strong>`;
      if (classCount) {{
        const dayRate = ((items.length / classCount) * 100).toFixed(1);
        html += `<div class="tooltip-stat">${{items.length}} / ${{classCount}} clases = ${{dayRate}}% ese día</div>`;
      }}
      items.slice(0,5).forEach(i => {{
        html += `<div class="tooltip-incident">${{i.cohortName||"—"}} #${{i.commissionNumber}}<br>${{(i.description||i.summary||"—").slice(0,60)}}</div>`;
      }});
      if (items.length > 5) html += `<div style="color:#888;font-size:10px;margin-top:6px">+${{items.length-5}} más...</div>`;
      tt.innerHTML = html; tt.style.display = "block"; posTooltip(e);
    }});
    bar.addEventListener("mousemove", posTooltip);
    bar.addEventListener("mouseleave", () => tt.style.display = "none");
    container.appendChild(col);
  }});
}}

function posTooltip(e) {{
  const tt = document.getElementById("tooltip"), m = 12;
  let x = e.clientX + m, y = e.clientY + m;
  if (x + (tt.offsetWidth  || 300) > window.innerWidth  - m) x = e.clientX - (tt.offsetWidth  || 300) - m;
  if (y + (tt.offsetHeight || 100) > window.innerHeight - m) y = e.clientY - (tt.offsetHeight || 100) - m;
  tt.style.left = x + "px"; tt.style.top = y + "px";
}}

// ─── RANKING ───────────────────────────────────────────────────
function renderRanking() {{
  const container = document.getElementById("ranking-list");
  const from      = document.getElementById("date-from").value;
  const to        = document.getElementById("date-to").value;
  const byCohort  = {{}};
  filteredData.forEach(d => {{
    const k = `${{d.cohortName}}|||${{d.commissionNumber}}`;
    byCohort[k] = (byCohort[k] || 0) + 1;
  }});
  const sorted = Object.entries(byCohort).sort((a,b) => b[1] - a[1]).slice(0,10);
  const maxV   = sorted[0]?.[1] || 1;
  container.innerHTML = sorted.map(([key, count]) => {{
    const [course, comm]  = key.split("|||");
    const short           = course.length > 28 ? course.slice(0,26)+"…" : course;
    const classCount      = countCommissionClasses(comm, from, to);
    const rateTag         = classCount ? `<span class="rank-rate">${{((count/classCount)*100).toFixed(0)}}%</span>` : "";
    return `<div class="ranking-item">
      <div class="rank-label">
        <span>${{short}} <span style="color:#BBBBBB;font-weight:400">#${{comm}}</span></span>
        <span style="display:flex;align-items:center;gap:6px"><span class="rank-count">${{count}}</span>${{rateTag}}</span>
      </div>
      <div class="rank-bar-bg"><div class="rank-bar-fill" style="width:${{(count/maxV)*100}}%"></div></div>
    </div>`;
  }}).join("") || "<p style='color:#bbb;font-size:13px'>Sin datos</p>";
}}

// ─── TABLE ─────────────────────────────────────────────────────
function renderTable() {{
  const search = (document.getElementById("search-input").value || "").toLowerCase();
  let rows = filteredData;
  if (typeFilter !== "ALL") rows = rows.filter(d => d.type === typeFilter);
  if (search) rows = rows.filter(d =>
    (d.cohortName      || "").toLowerCase().includes(search) ||
    (d.commissionNumber|| "").toString().includes(search)    ||
    (d.description     || "").toLowerCase().includes(search)
  );
  document.getElementById("table-info").textContent = `Mostrando ${{rows.length}} de ${{filteredData.length}} incidents`;
  const sBadge = s => {{
    const m = {{OPEN:"open",IN_PROGRESS:"in-progress",RESOLVED:"resolved",CANCELLED:"cancelled"}};
    const l = {{OPEN:"OPEN",IN_PROGRESS:"EN PROCESO",RESOLVED:"RESUELTO",CANCELLED:"CANCELADO"}};
    return `<span class="badge badge-${{m[s]||"open"}}">${{l[s]||s}}</span>`;
  }};
  document.getElementById("table-body").innerHTML = rows.map(r => {{
    const isA = r.type === "INSTRUCTOR_ABSENCE";
    return `<tr class="${{isA?"row-absence":"row-issues"}}">
      <td style="color:#313131;font-size:12px;white-space:nowrap">${{r.date.split("-").reverse().join("/")}}</td>
      <td><strong style="font-size:12px">${{r.cohortName||"—"}}</strong></td>
      <td style="font-weight:600">#${{r.commissionNumber||"—"}}</td>
      <td><span class="badge ${{isA?"badge-absence":"badge-issues"}}">${{isA?"AUSENCIA":"CLASS ISSUE"}}</span></td>
      <td>${{sBadge(r.status)}}</td>
      <td style="color:#555;font-size:12px;max-width:200px">${{(r.description||r.summary||"—").slice(0,80)}}</td>
    </tr>`;
  }}).join("") || "<tr><td colspan='6' style='text-align:center;padding:40px;color:#BBBBBB'>Sin resultados</td></tr>";
}}

// ─── INIT ──────────────────────────────────────────────────────
function init() {{
  const r = getRange();
  document.getElementById("date-from").value = r.min;
  document.getElementById("date-to").value   = r.max;
  filteredData = [...ALL_DATA];
  updateAll();
}}
</script>
</body>
</html>"""


if __name__ == "__main__":
    records, cohort_schedule = build_dataset()
    html = generate_html(records, cohort_schedule, PASSWORD_HASH)
    os.makedirs("output", exist_ok=True)
    with open("output/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ index.html generado con {len(records)} incidents y {len(cohort_schedule)} cohortes activas")
    if records:
        dates = sorted(r["date"] for r in records)
        print(f"📅 Rango incidents: {dates[0]} → {dates[-1]}")
        top3 = Counter(r["cohortName"] for r in records).most_common(3)
        print("🏆 Top 3 cursos:")
        for name, count in top3:
            print(f"   {count}x {name}")
