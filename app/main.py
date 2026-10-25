"""Local-first drug-label comparison and review application."""
from datetime import date
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.comparison import compare_sections
from app.database import Database, audit, encode, now
from app.demo import DRUG, NEW_TEXT, OLD_TEXT
from app.documents import extract_text, normalize_sections, parse_sections
from app.reports import export_report

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_TEXT = 300_000
STATIC_DIR = Path(__file__).parent / "static"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DrugInput(Input):
    name: str = Field(min_length=1, max_length=200)
    manufacturer: str = Field(default="", max_length=300)
    specification: str = Field(default="", max_length=300)
    approval_number: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=3000)


class TextVersionInput(Input):
    label: str = Field(min_length=1, max_length=120)
    version_date: date
    source: str = Field(default="", max_length=2000)
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class SectionInput(Input):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=MAX_TEXT)


class SectionsInput(Input):
    sections: list[SectionInput] = Field(min_length=1, max_length=200)


class ComparisonInput(Input):
    old_version_id: int = Field(gt=0)
    new_version_id: int = Field(gt=0)


class ReviewInput(Input):
    status: Literal["pending", "confirmed", "ignored"]
    note: str = Field(default="", max_length=3000)
    revision: int = Field(ge=0)


def required_row(connection, table, entity_id):
    # Table names are exclusively supplied by internal call sites.
    if table not in {"drugs", "versions", "changes", "comparisons"}:
        raise ValueError("Unknown table")
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "记录不存在")
    return row


def version_data(row, full=False):
    result = dict(row)
    sections = json.loads(result.pop("sections"))
    result.pop("storage_name", None)
    result["warnings"] = json.loads(result["warnings"])
    result["section_count"] = len(sections)
    if full:
        result["sections"] = sections
    else:
        result.pop("text", None)
    return result


def change_data(row):
    result = json.loads(row["payload"])
    result.update({key: row[key] for key in ("id", "comparison_id", "status", "note", "reviewer", "reviewed_at", "revision")})
    return result


def comparison_data(connection, entity_id, full=True):
    row = connection.execute(
        """SELECT c.*, d.name AS drug_name, ov.label AS old_label, nv.label AS new_label
        FROM comparisons c JOIN drugs d ON d.id=c.drug_id
        JOIN versions ov ON ov.id=c.old_version_id
        JOIN versions nv ON nv.id=c.new_version_id WHERE c.id=?""", (entity_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "比对记录不存在")
    result = dict(row)
    result["warnings"] = json.loads(result["warnings"])
    changes = [change_data(item) for item in connection.execute(
        "SELECT * FROM changes WHERE comparison_id=? ORDER BY id", (entity_id,)
    )]
    result["total_changes"] = len(changes)
    result["pending_changes"] = sum(item["status"] == "pending" for item in changes)
    result["status"] = "pending" if result["pending_changes"] else "completed"
    if full:
        result["changes"] = changes
        result["old_version"] = version_data(required_row(connection, "versions", result["old_version_id"]), True)
        result["new_version"] = version_data(required_row(connection, "versions", result["new_version_id"]), True)
    return result


def add_drug(connection, payload, actor):
    cursor = connection.execute(
        "INSERT INTO drugs(name,manufacturer,specification,approval_number,notes,created_at) VALUES(?,?,?,?,?,?)",
        (payload["name"], payload.get("manufacturer", ""), payload.get("specification", ""),
         payload.get("approval_number", ""), payload.get("notes", ""), now()),
    )
    audit(connection, actor, "创建药品档案", "drug", cursor.lastrowid, {"name": payload["name"]})
    return cursor.lastrowid


def add_version(connection, db, drug_id, label, version_date, source, filename, data, actor, created_files):
    required_row(connection, "drugs", drug_id)
    if not label.strip() or len(label.strip()) > 120:
        raise HTTPException(422, "版本名称须为 1–120 个字符")
    if len(source) > 2000:
        raise HTTPException(422, "来源信息不能超过 2000 个字符")
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, "文件不能超过 10 MB")
    if not data:
        raise HTTPException(422, "文件不能为空")
    try:
        parsed_date = date.fromisoformat(str(version_date)).isoformat()
    except (TypeError, ValueError):
        raise HTTPException(422, "版本日期格式应为 YYYY-MM-DD")
    clean_filename = Path(filename.replace("\\", "/")).name[:200]
    if not clean_filename:
        raise HTTPException(422, "文件名不能为空")
    try:
        text = extract_text(clean_filename, data)
        if len(text) > MAX_TEXT:
            raise ValueError("文档提取后不能超过 300000 个字符，请拆分文档")
        sections = parse_sections(text)
    except ValueError as error:
        raise HTTPException(422, str(error))
    warnings = []
    if len(sections) == 1:
        warnings.append("仅识别到一个章节，请在开始比对前核对章节划分。")
    storage_name = uuid4().hex + Path(clean_filename).suffix.lower()
    path = db.uploads / storage_name
    path.write_bytes(data)
    created_files.append(path)
    digest = hashlib.sha256(data).hexdigest()
    cursor = connection.execute(
        """INSERT INTO versions(drug_id,label,version_date,source,filename,sha256,storage_name,
        text,sections,warnings,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (drug_id, label.strip(), parsed_date, source.strip(), clean_filename, digest, storage_name,
         text, encode(sections), encode(warnings), now()),
    )
    audit(connection, actor, "导入说明书版本", "version", cursor.lastrowid,
          {"drug_id": drug_id, "label": label.strip(), "filename": clean_filename, "sha256": digest})
    return cursor.lastrowid


def add_comparison(connection, old_id, new_id, actor):
    if old_id == new_id:
        raise HTTPException(422, "请选择两个不同版本")
    old = required_row(connection, "versions", old_id)
    new = required_row(connection, "versions", new_id)
    if old["drug_id"] != new["drug_id"]:
        raise HTTPException(422, "只能比对同一药品档案下的版本")
    if old["version_date"] > new["version_date"]:
        raise HTTPException(422, "旧版本日期不能晚于新版本日期")
    try:
        diff = compare_sections(json.loads(old["sections"]), json.loads(new["sections"]))
    except ValueError as error:
        raise HTTPException(422, str(error))
    cursor = connection.execute(
        """INSERT INTO comparisons(drug_id,old_version_id,new_version_id,unchanged_sections,warnings,created_at)
        VALUES(?,?,?,?,?,?)""", (old["drug_id"], old_id, new_id, diff["unchanged_sections"], encode(diff.get("warnings", [])), now())
    )
    comparison_id = cursor.lastrowid
    for change in diff["changes"]:
        connection.execute("INSERT INTO changes(comparison_id,payload) VALUES(?,?)", (comparison_id, encode(change)))
    audit(connection, actor, "创建版本比对", "comparison", comparison_id,
          {"old_version_id": old_id, "new_version_id": new_id, "changes": len(diff["changes"])})
    return comparison_id


def create_app(data_dir=None):
    application = FastAPI(
        title="药品说明书版本比对与变更审核系统", version="1.0.0",
        description="本地单操作员版本比对工具。输出用于人工复核，不判断医学正确性。",
    )
    db = Database(data_dir or os.environ.get("MEDLABEL_DATA_DIR", "data"))
    actor = os.environ.get("MEDLABEL_REVIEWER", "本地审核员").strip()[:200] or "本地审核员"
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    # TestClient's synthetic hostname is only enabled for explicit app factories.
    if data_dir is not None:
        allowed_hosts.add("testserver")
    allowed_hosts.update(host.strip().lower() for host in os.environ.get("MEDLABEL_ALLOWED_HOSTS", "").split(",") if host.strip())
    application.state.db = db
    application.state.reviewer = actor

    @application.middleware("http")
    async def guard_requests(request: Request, call_next):
        # DNS rebinding must not make an attacker's origin a trusted local UI.
        host = request.headers.get("host", "")
        try:
            authority = urlsplit("//" + host)
            host_port = authority.port or (443 if request.url.scheme == "https" else 80)
            if authority.username or authority.password or authority.path or authority.query or authority.fragment:
                raise ValueError("Invalid authority")
            if (authority.hostname or "").lower() not in allowed_hosts:
                return JSONResponse({"detail": "不允许的访问主机，请使用 localhost 或 127.0.0.1"}, status_code=400)
        except ValueError:
            return JSONResponse({"detail": "无效的访问主机"}, status_code=400)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("X-Requested-With") != "MedLabelReview":
                return JSONResponse({"detail": "请求缺少本地应用校验头"}, status_code=403)
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed_origin = urlsplit(origin)
                    origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
                    same_origin = (parsed_origin.scheme == request.url.scheme
                                   and parsed_origin.hostname == authority.hostname
                                   and origin_port == host_port
                                   and not parsed_origin.username and not parsed_origin.password
                                   and not parsed_origin.path and not parsed_origin.query and not parsed_origin.fragment)
                except ValueError:
                    same_origin = False
                if not same_origin:
                    return JSONResponse({"detail": "不允许跨来源修改本地数据"}, status_code=403)
            length = request.headers.get("content-length")
            if length:
                try:
                    if int(length) > MAX_FILE_SIZE + 256 * 1024:
                        return JSONResponse({"detail": "请求内容过大，文件不能超过 10 MB"}, status_code=413)
                except ValueError:
                    return JSONResponse({"detail": "无效的请求长度"}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        fields = [".".join(str(part) for part in item["loc"]) for item in error.errors()]
        return JSONResponse({"detail": "输入格式不正确，请检查：" + "、".join(fields)}, status_code=422)

    @application.get("/api/health")
    def health():
        return {"status": "ok", "version": "1.0.0", "mode": "local-single-operator", "reviewer": actor}

    @application.get("/api/dashboard")
    def dashboard():
        with db.connect() as connection:
            result = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("drugs", "versions", "comparisons")}
            result["pending_changes"] = connection.execute("SELECT COUNT(*) FROM changes WHERE status='pending'").fetchone()[0]
            result["recent_activity"] = [dict(row) for row in connection.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 8")]
            return result

    @application.get("/api/drugs")
    def list_drugs():
        with db.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT d.*, (SELECT COUNT(*) FROM versions v WHERE v.drug_id=d.id) AS version_count FROM drugs d ORDER BY d.id DESC")]

    @application.post("/api/drugs", status_code=201)
    def create_drug(payload: DrugInput):
        with db.connect(write=True) as connection:
            entity_id = add_drug(connection, payload.model_dump(), actor)
            result = dict(required_row(connection, "drugs", entity_id))
            result["version_count"] = 0
            return result

    @application.get("/api/drugs/{drug_id}")
    def get_drug(drug_id: int):
        with db.connect() as connection:
            result = dict(required_row(connection, "drugs", drug_id))
            result["version_count"] = connection.execute("SELECT COUNT(*) FROM versions WHERE drug_id=?", (drug_id,)).fetchone()[0]
            return result

    @application.get("/api/drugs/{drug_id}/versions")
    def list_versions(drug_id: int):
        with db.connect() as connection:
            required_row(connection, "drugs", drug_id)
            return [version_data(row) for row in connection.execute(
                "SELECT * FROM versions WHERE drug_id=? ORDER BY version_date DESC,id DESC", (drug_id,))]

    @application.post("/api/drugs/{drug_id}/versions", status_code=201)
    async def upload_version(drug_id: int, file: UploadFile = File(...), label: str = Form(...),
                             version_date: str = Form(...), source: str = Form("")):
        created_files = []
        try:
            data = await file.read(MAX_FILE_SIZE + 1)
            with db.connect(write=True) as connection:
                entity_id = add_version(connection, db, drug_id, label, version_date, source,
                                        file.filename or "", data, actor, created_files)
                return version_data(required_row(connection, "versions", entity_id), True)
        except Exception:
            for path in created_files:
                path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @application.post("/api/drugs/{drug_id}/versions/text", status_code=201)
    def text_version(drug_id: int, payload: TextVersionInput):
        created_files = []
        try:
            with db.connect(write=True) as connection:
                entity_id = add_version(connection, db, drug_id, payload.label, payload.version_date,
                                        payload.source, "pasted-text.txt", payload.text.encode("utf-8"), actor, created_files)
                return version_data(required_row(connection, "versions", entity_id), True)
        except Exception:
            for path in created_files:
                path.unlink(missing_ok=True)
            raise

    @application.get("/api/versions/{version_id}")
    def get_version(version_id: int):
        with db.connect() as connection:
            return version_data(required_row(connection, "versions", version_id), True)

    @application.get("/api/versions/{version_id}/file")
    def download_version(version_id: int):
        with db.connect() as connection:
            row = required_row(connection, "versions", version_id)
            path = (db.uploads / row["storage_name"]).resolve()
            if path.parent != db.uploads.resolve() or not path.is_file():
                raise HTTPException(404, "原始文件不存在")
            return FileResponse(path, filename=row["filename"], media_type="application/octet-stream")

    @application.put("/api/versions/{version_id}/sections")
    def update_sections(version_id: int, payload: SectionsInput):
        if sum(len(item.title) + len(item.text) for item in payload.sections) > MAX_TEXT:
            raise HTTPException(422, "章节总长度不能超过 300000 个字符")
        with db.connect(write=True) as connection:
            row = required_row(connection, "versions", version_id)
            linked = connection.execute("SELECT id FROM comparisons WHERE old_version_id=? OR new_version_id=? LIMIT 1",
                                        (version_id, version_id)).fetchone()
            if linked:
                raise HTTPException(409, "此版本已用于比对，不能修改章节。请导入一个新版本进行校正。")
            try:
                sections = normalize_sections([item.model_dump() for item in payload.sections])
            except ValueError as error:
                raise HTTPException(422, str(error))
            for item in sections:
                item["start_line"] = None
                item["end_line"] = None
            warnings = ["章节经人工校正；原始文件和提取原文保持不变，章节位置不再等同于原文行号。"]
            connection.execute("UPDATE versions SET sections=?,warnings=? WHERE id=?", (encode(sections), encode(warnings), version_id))
            audit(connection, actor, "校正章节", "version", version_id,
                  {"before": json.loads(row["sections"]), "after": sections})
            return version_data(required_row(connection, "versions", version_id), True)

    @application.get("/api/comparisons")
    def list_comparisons():
        with db.connect() as connection:
            ids = [row[0] for row in connection.execute("SELECT id FROM comparisons ORDER BY id DESC")]
            return [comparison_data(connection, entity_id, False) for entity_id in ids]

    @application.post("/api/comparisons", status_code=201)
    def create_comparison(payload: ComparisonInput):
        with db.connect(write=True) as connection:
            entity_id = add_comparison(connection, payload.old_version_id, payload.new_version_id, actor)
            return comparison_data(connection, entity_id)

    @application.get("/api/comparisons/{comparison_id}")
    def get_comparison(comparison_id: int):
        with db.connect() as connection:
            return comparison_data(connection, comparison_id)

    @application.patch("/api/changes/{change_id}")
    def review_change(change_id: int, payload: ReviewInput):
        if payload.status == "ignored" and not payload.note:
            raise HTTPException(422, "忽略差异时必须填写原因")
        with db.connect(write=True) as connection:
            current = required_row(connection, "changes", change_id)
            if current["revision"] != payload.revision:
                raise HTTPException(409, "此差异已被更新，请刷新后重新审核")
            timestamp = now()
            connection.execute(
                "UPDATE changes SET status=?,note=?,reviewer=?,reviewed_at=?,revision=revision+1 WHERE id=? AND revision=?",
                (payload.status, payload.note, actor, timestamp, change_id, payload.revision),
            )
            audit(connection, actor, "审核变更", "change", change_id,
                  {"comparison_id": current["comparison_id"], "before": {"status": current["status"], "note": current["note"]},
                   "after": {"status": payload.status, "note": payload.note}, "revision": payload.revision + 1})
            return change_data(required_row(connection, "changes", change_id))

    @application.get("/api/audit")
    def list_audit():
        with db.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 500")]

    @application.get("/api/comparisons/{comparison_id}/export")
    def export_comparison(comparison_id: int, format: Literal["html", "csv", "json"] = "html"):
        with db.connect() as connection:
            comparison = comparison_data(connection, comparison_id)
        body, media_type, filename = export_report(comparison, format)
        return Response(body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @application.post("/api/demo")
    def seed_demo():
        created_files = []
        try:
            with db.connect(write=True) as connection:
                seeded = connection.execute("SELECT value FROM settings WHERE key='demo'").fetchone()
                if seeded:
                    return json.loads(seeded[0])
                drug_id = add_drug(connection, DRUG, actor)
                old_id = add_version(connection, db, drug_id, "模拟初始版", "2026-01-01", "项目内置虚构示例，不用于临床",
                                     "demo-v1.txt", OLD_TEXT.encode("utf-8"), actor, created_files)
                new_id = add_version(connection, db, drug_id, "模拟修订版", "2026-06-01", "项目内置虚构示例，不用于临床",
                                     "demo-v2.txt", NEW_TEXT.encode("utf-8"), actor, created_files)
                comparison_id = add_comparison(connection, old_id, new_id, actor)
                result = {"drug_id": drug_id, "comparison_id": comparison_id}
                connection.execute("INSERT INTO settings(key,value) VALUES('demo',?)", (encode(result),))
                return result
        except Exception:
            for path in created_files:
                path.unlink(missing_ok=True)
            raise

    @application.get("/", include_in_schema=False)
    def index():
        path = STATIC_DIR / "index.html"
        if not path.exists():
            raise HTTPException(503, "界面文件尚未安装")
        return FileResponse(path)

    application.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
    return application


app = create_app()
