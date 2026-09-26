import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54394/printreg")
SECRET = os.environ.get("JWT_SECRET", "print-register-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "printer": {"role": "writer", "password_hash": pwd.hash("print123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = [
    """
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
)
""",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS frozen boolean NOT NULL DEFAULT false",
    """
CREATE TABLE IF NOT EXISTS freeze_packages (
    id serial PRIMARY KEY,
    package_no text NOT NULL UNIQUE,
    signed_by text NOT NULL,
    signed_at timestamptz NOT NULL
)
""",
    """
CREATE TABLE IF NOT EXISTS freeze_items (
    id serial PRIMARY KEY,
    package_id integer NOT NULL REFERENCES freeze_packages(id),
    job_id integer NOT NULL,
    sheet text NOT NULL,
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    verdict text NOT NULL,
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    job_created_at timestamptz NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS idx_freeze_items_package ON freeze_items(package_id)",
]


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    cyan_mm: float
    magenta_mm: float


class JobPatchIn(BaseModel):
    cyan_mm: float
    magenta_mm: float


class FreezeIn(BaseModel):
    job_ids: list[int]


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_role(role: str, detail: str):
    def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] != role:
            raise HTTPException(status_code=403, detail=detail)
        return user

    return dependency


require_writer = require_role("writer", "仅印刷员可送复核")
require_signer = require_role("writer", "仅印刷员可签发冻结包")


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            conn.execute(
                """INSERT INTO jobs (sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at)
                   VALUES
                   ('封面-01', 0.05, -0.04, 'pending', '', '', 'printer', %s),
                   ('内页-09', 0.40, 0.02, 'pending', '', '', 'printer', %s)""",
                (now, now),
            )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "print-register-review"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/jobs")
def list_jobs(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT id, sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by, frozen
               FROM jobs ORDER BY id DESC"""
        ).fetchall()


@app.post("/api/jobs", status_code=202)
def enqueue(body: JobIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO jobs (sheet, cyan_mm, magenta_mm, status, created_by, created_at)
               VALUES (%s, %s, %s, 'pending', %s, %s)
               RETURNING id, sheet, status, verdict""",
            (body.sheet.strip(), body.cyan_mm, body.magenta_mm, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
    return row


@app.patch("/api/jobs/{job_id}")
def edit_job(job_id: int, body: JobPatchIn, user: dict = Depends(require_writer)):
    """改现场某行偏差后重新入队；已冻结行也可改，只动总表当前行，冻结包快照不变。"""
    with connect() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = %s FOR UPDATE", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        if row["status"] != "done":
            raise HTTPException(status_code=409, detail="仅已出结论的记录可改偏差重判")
        conn.execute(
            """UPDATE jobs
               SET cyan_mm = %s, magenta_mm = %s, status = 'pending', verdict = '', reason = ''
               WHERE id = %s""",
            (body.cyan_mm, body.magenta_mm, job_id),
        )
        conn.commit()
    return {"ok": True}


def _fetch_package(conn, package_id: int) -> dict | None:
    pkg = conn.execute(
        "SELECT id, package_no, signed_by, signed_at FROM freeze_packages WHERE id = %s",
        (package_id,),
    ).fetchone()
    if pkg is None:
        return None
    pkg["items"] = conn.execute(
        """SELECT id, job_id, sheet, cyan_mm, magenta_mm, verdict, reason, created_by, job_created_at
           FROM freeze_items WHERE package_id = %s ORDER BY id""",
        (package_id,),
    ).fetchall()
    return pkg


@app.get("/api/freeze")
def freeze_overview(_user: dict = Depends(current_user)):
    with connect() as conn:
        signable = conn.execute(
            """SELECT id, sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by
               FROM jobs WHERE status = 'done' AND frozen = false ORDER BY id"""
        ).fetchall()
        packages = conn.execute(
            """SELECT p.id, p.package_no, p.signed_by, p.signed_at,
                      (SELECT count(*) FROM freeze_items i WHERE i.package_id = p.id) AS job_count
               FROM freeze_packages p ORDER BY p.id DESC"""
        ).fetchall()
    return {"signable": signable, "packages": packages}


@app.post("/api/freeze", status_code=201)
def sign_freeze(body: FreezeIn, user: dict = Depends(require_signer)):
    """印刷员签发：把勾选的已出结论记录复制成只读快照，原行标记已冻结。"""
    job_ids = list(dict.fromkeys(body.job_ids))
    if not job_ids:
        raise HTTPException(status_code=400, detail="请先勾选要冻结的结论")
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at, frozen
               FROM jobs WHERE id = ANY(%s) ORDER BY id FOR UPDATE""",
            (job_ids,),
        ).fetchall()
        if len(rows) != len(job_ids):
            raise HTTPException(status_code=400, detail="包含不存在的印张记录")
        bad = [r["sheet"] for r in rows if r["status"] != "done" or not r["verdict"] or r["frozen"]]
        if bad:
            raise HTTPException(status_code=409, detail="只能签发已出结论且未冻结的记录：%s" % "、".join(bad))
        now = datetime.now(timezone.utc)
        pkg_id = conn.execute(
            "INSERT INTO freeze_packages (package_no, signed_by, signed_at) VALUES ('', %s, %s) RETURNING id",
            (user["username"], now),
        ).fetchone()["id"]
        package_no = "F%s-%03d" % (now.strftime("%Y%m%d"), pkg_id)
        conn.execute("UPDATE freeze_packages SET package_no = %s WHERE id = %s", (package_no, pkg_id))
        for r in rows:
            conn.execute(
                """INSERT INTO freeze_items
                   (package_id, job_id, sheet, cyan_mm, magenta_mm, verdict, reason, created_by, job_created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    pkg_id,
                    r["id"],
                    r["sheet"],
                    r["cyan_mm"],
                    r["magenta_mm"],
                    r["verdict"],
                    r["reason"],
                    r["created_by"],
                    r["created_at"],
                ),
            )
        conn.execute("UPDATE jobs SET frozen = true WHERE id = ANY(%s)", (job_ids,))
        conn.commit()
        return _fetch_package(conn, pkg_id)


@app.get("/api/freeze/{package_id}")
def get_freeze(package_id: int, _user: dict = Depends(current_user)):
    """翻包：任何登录用户（含质检）都可查看只读快照。"""
    with connect() as conn:
        pkg = _fetch_package(conn, package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="冻结包不存在")
    return pkg
