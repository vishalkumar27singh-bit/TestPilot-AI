"""Build JSON / CSV / HTML / PDF reports from a stored run."""
from __future__ import annotations

import base64
import csv
import io
import json
from html import escape
from pathlib import Path

from database.db_manager import DB

COLORS = {"PASS": "#1F9D6B", "FAIL": "#D9482B", "SKIPPED": "#8A8F98"}


def build_report_data(run_id: int, db: DB | None = None) -> dict:
    db = db or DB()
    run = db.get_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")
    tests = db.get_test_cases(run_id)
    executed = run["passed"] + run["failed"]
    summary = {"total": run["total"], "passed": run["passed"], "failed": run["failed"], "skipped": run["skipped"],
               "pass_percentage": round(100 * run["passed"] / executed, 1) if executed else 0.0,
               "duration_seconds": round(run["duration"] or 0, 2)}
    run_public = {k: v for k, v in run.items() if k not in ("config_json", "profile_json", "log_text")}
    return {"generator": "TestPilot AI", "run": run_public, "summary": summary, "tests": tests,
            "failed_tests": [t for t in tests if t["status"] == "FAIL"], "bugs": db.list_bugs(run_id),
            "screenshots": db.list_screenshots(run_id), "ai_recommendations": run.get("ai_summary") or ""}


def to_json(data: dict) -> bytes:
    return json.dumps(data, indent=2, default=str).encode()


def to_csv(data: dict) -> bytes:
    buf = io.StringIO()
    cols = ["test_id", "name", "category", "severity", "status", "engine", "objective", "expected", "actual", "failed_step", "probable_cause", "suggested_fix", "duration", "screenshot"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for t in data["tests"]:
        w.writerow({c: ("" if t.get(c) is None else str(t.get(c)).replace("\n", " ")) for c in cols})
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens UTF-8 correctly


def _img_b64(path: str) -> str:
    try:
        p = Path(path)
        return base64.b64encode(p.read_bytes()).decode() if p.exists() else ""
    except Exception:
        return ""


def to_html(data: dict) -> bytes:
    r, s = data["run"], data["summary"]
    e = lambda x: escape(str(x if x is not None else ""))
    rows = "".join(
        f"<tr><td>{e(t['test_id'])}</td><td>{e(t['name'])}<br><small>{e(t['category'])}</small></td>"
        f"<td><span class='pill' style='background:{COLORS.get(t['status'], '#999')}'>{e(t['status'])}</span></td><td>{e(t.get('engine') or '')}</td><td>{e(t['severity'])}</td>"
        f"<td>{e(t['actual'])}</td><td>{t['duration'] or 0:.2f}s</td></tr>" for t in data["tests"])
    fails = ""
    for t in data["failed_tests"]:
        img = _img_b64(t.get("screenshot") or "")
        fails += (f"<div class='fail'><h3>{e(t['test_id'])} · {e(t['name'])} <span class='sev'>{e(t['severity'])}</span></h3>"
                  f"<p><b>Failed step:</b> {e(t['failed_step'])}</p><p><b>Details:</b> {e(t['error'] or t['actual'])}</p>"
                  f"<p><b>Probable cause:</b> {e(t['probable_cause'])}</p><p><b>Suggested fix:</b> {e(t['suggested_fix'])}</p>"
                  + (f"<p><b>AI explanation:</b> {e(t['ai_explanation'])}</p>" if t.get("ai_explanation") else "")
                  + (f"<img alt='Screenshot for {e(t['test_id'])}' src='data:image/png;base64,{img}'>" if img else "") + "</div>")
    bugs = "".join(f"<tr><td>{e(b['bug_id'])}</td><td>{e(b['title'])}</td><td>{e(b['severity'])}</td><td>{e(b['suggested_solution'])}</td></tr>" for b in data["bugs"])
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>TestPilot AI Report - Run {e(r['id'])}</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{{font-family:'IBM Plex Sans',system-ui,sans-serif;margin:0;background:#F4F3EE;color:#111827}}main{{max-width:1080px;margin:auto;padding:32px 20px}}
h1{{margin:0 0 4px}}.meta{{color:#555;margin-bottom:24px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}}
.card{{background:#fff;border:1px solid #e2e0d8;border-radius:10px;padding:14px}}.card b{{display:block;font-size:26px}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e0d8;margin-bottom:24px;font-size:14px}}th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top}}
th{{background:#111827;color:#fff}}.pill{{color:#fff;border-radius:99px;padding:2px 10px;font-size:12px}}.fail{{background:#fff;border-left:4px solid #D9482B;padding:4px 16px;margin-bottom:14px;border-radius:6px}}
.fail img{{max-width:100%;border:1px solid #ddd;border-radius:6px}}.sev{{font-size:12px;background:#fde8e3;color:#a02d15;padding:2px 8px;border-radius:99px}}pre{{white-space:pre-wrap;background:#fff;padding:14px;border-radius:8px;border:1px solid #e2e0d8}}
@media(max-width:700px){{table{{display:block;overflow-x:auto}}}}</style></head><body><main>
<h1>TestPilot AI — Test Report</h1><div class="meta">Run #{e(r['id'])} · {e(r['kind']).upper()} · {e(r['url'])} · {e(r['started_at'])} · Engine: {e(r.get('engine'))} · Mode: {e(r.get('ai_mode'))}</div>
<div class="cards"><div class="card">Total<b>{s['total']}</b></div><div class="card">Passed<b style="color:#1F9D6B">{s['passed']}</b></div>
<div class="card">Failed<b style="color:#D9482B">{s['failed']}</b></div><div class="card">Skipped<b>{s['skipped']}</b></div>
<div class="card">Pass rate<b>{s['pass_percentage']}%</b></div><div class="card">Duration<b>{s['duration_seconds']}s</b></div></div>
<h2>Recommendations</h2><pre>{e(data['ai_recommendations'])}</pre>
<h2>All test cases</h2><table><tr><th>ID</th><th>Test</th><th>Status</th><th>Engine</th><th>Severity</th><th>Actual result</th><th>Time</th></tr>{rows}</table>
<h2>Failed test details</h2>{fails or '<p>No failures.</p>'}
<h2>Bug reports</h2><table><tr><th>Bug</th><th>Title</th><th>Severity</th><th>Suggested solution</th></tr>{bugs or '<tr><td colspan=4>None</td></tr>'}</table>
</main></body></html>"""
    return html.encode()


def to_pdf(data: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=ss["BodyText"], fontSize=8, leading=10)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=13)
    p = lambda t, st=small: Paragraph(escape(str(t if t is not None else "")).replace("\n", "<br/>"), st)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title="TestPilot AI Report")
    r, s = data["run"], data["summary"]
    st = [Paragraph("TestPilot AI — Test Report", ss["Title"]),
          p(f"Run #{r['id']} · {r['kind'].upper()} · {r['url']} · {r['started_at']} · Engine: {r.get('engine')} · Mode: {r.get('ai_mode')}", body), Spacer(1, 6 * mm)]
    m = Table([["Total", "Passed", "Failed", "Skipped", "Pass rate", "Duration"],
               [s["total"], s["passed"], s["failed"], s["skipped"], f"{s['pass_percentage']}%", f"{s['duration_seconds']}s"]], colWidths=[30 * mm] * 6)
    m.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("FONTSIZE", (0, 1), (-1, 1), 14), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                           ("TOPPADDING", (0, 1), (-1, 1), 6), ("BOTTOMPADDING", (0, 1), (-1, 1), 6)]))
    st += [m, Spacer(1, 6 * mm), Paragraph("Recommendations", ss["Heading2"]), p(data["ai_recommendations"] or "n/a", body), Spacer(1, 4 * mm),
           Paragraph("Test cases", ss["Heading2"])]
    rows = [["ID", "Test", "Status", "Engine", "Severity", "Actual result"]]
    for t in data["tests"]:
        rows.append([p(t["test_id"]), p(t["name"]), p(t["status"]), p(t.get("engine") or ""), p(t["severity"]), p(t["actual"][:260])])
    tb = Table(rows, colWidths=[14 * mm, 38 * mm, 17 * mm, 20 * mm, 16 * mm, 77 * mm], repeatRows=1)
    sty = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, 0), 8),
           ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")), ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for i, t in enumerate(data["tests"], 1):
        sty.append(("BACKGROUND", (2, i), (2, i), colors.HexColor({"PASS": "#D6F0E4", "FAIL": "#F8D9D2", "SKIPPED": "#E6E7EA"}.get(t["status"], "#FFFFFF"))))
    tb.setStyle(TableStyle(sty))
    st += [tb, Spacer(1, 6 * mm), Paragraph("Failed test details", ss["Heading2"])]
    if not data["failed_tests"]:
        st.append(p("No failures.", body))
    for t in data["failed_tests"]:
        block = [Paragraph(escape(f"{t['test_id']} · {t['name']} [{t['severity']}]"), ss["Heading4"]),
                 p(f"Failed step: {t['failed_step']}", body), p(f"Details: {t['error'] or t['actual']}", body),
                 p(f"Probable cause: {t['probable_cause']}", body), p(f"Suggested fix: {t['suggested_fix']}", body)]
        if t.get("ai_explanation"):
            block.append(p(f"AI explanation: {t['ai_explanation']}", body))
        sp = t.get("screenshot")
        if sp and Path(sp).exists():
            try:
                from reportlab.lib.utils import ImageReader
                iw, ih = ImageReader(sp).getSize()
                w = min(150 * mm, iw * 0.264583 * mm / mm)
                block.append(Image(sp, width=w, height=w * ih / iw))
            except Exception:
                pass
        block.append(Spacer(1, 4 * mm))
        st.append(KeepTogether(block))
    doc.build(st)
    return buf.getvalue()


FORMATS = {"pdf": ("application/pdf", to_pdf), "csv": ("text/csv", to_csv), "json": ("application/json", to_json), "html": ("text/html", to_html)}


def generate(run_id: int, fmt: str, db: DB | None = None) -> tuple[bytes, str, str]:
    """Return (bytes, mime, filename)."""
    data = build_report_data(run_id, db)
    mime, fn = FORMATS[fmt]
    return fn(data), mime, f"testpilot_run_{run_id}.{fmt}"
