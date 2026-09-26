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


SCHEMA = """
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
);
CREATE TABLE IF NOT EXISTS frozen_packages (
    id serial PRIMARY KEY,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS frozen_items (
    id serial PRIMARY KEY,
    package_id integer NOT NULL REFERENCES frozen_packages(id),
    job_id integer NOT NULL UNIQUE,
    sheet text NOT NULL,
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    verdict text NOT NULL,
    reason text NOT NULL
);
"""


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    cyan_mm: float
    magenta_mm: float


class FreezeIn(BaseModel):
    job_ids: list[int]


class ReviseIn(BaseModel):
    cyan_mm: float
    magenta_mm: float


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


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可送复核")
    return user


def require_signer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可签发冻结包")
    return user


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(SCHEMA)
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
            """SELECT j.id, j.sheet, j.cyan_mm, j.magenta_mm, j.status, j.verdict, j.reason,
                      j.created_by, f.package_id AS frozen_package_id
               FROM jobs j
               LEFT JOIN frozen_items f ON f.job_id = j.id
               ORDER BY j.id DESC"""
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


@app.post("/api/jobs/{job_id}/revise")
def revise(job_id: int, body: ReviseIn, user: dict = Depends(require_writer)):
    # 模拟现场改动某行偏差：重填新数值并重新送判定，冻结包内快照不受影响
    with connect() as conn:
        row = conn.execute(
            """UPDATE jobs SET cyan_mm = %s, magenta_mm = %s, status = 'pending',
                              verdict = '', reason = ''
               WHERE id = %s
               RETURNING id, sheet, status, verdict""",
            (body.cyan_mm, body.magenta_mm, job_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="印张不存在")
        conn.commit()
    return row


@app.get("/api/freeze/candidates")
def freeze_candidates(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT j.id, j.sheet, j.cyan_mm, j.magenta_mm, j.verdict, j.reason
               FROM jobs j
               LEFT JOIN frozen_items f ON f.job_id = j.id
               WHERE j.status = 'done' AND f.job_id IS NULL
               ORDER BY j.id"""
        ).fetchall()


@app.post("/api/freeze", status_code=201)
def freeze(body: FreezeIn, user: dict = Depends(require_signer)):
    job_ids = sorted(set(body.job_ids))
    if not job_ids:
        raise HTTPException(status_code=400, detail="未选择要签发的印张")
    with connect() as conn:
        rows = conn.execute(
            """SELECT j.id, j.sheet, j.cyan_mm, j.magenta_mm, j.status, j.verdict, j.reason,
                      f.package_id AS frozen_package_id
               FROM jobs j
               LEFT JOIN frozen_items f ON f.job_id = j.id
               WHERE j.id = ANY(%s)
               ORDER BY j.id
               FOR UPDATE OF j""",
            (job_ids,),
        ).fetchall()
        found = {row["id"] for row in rows}
        missing = [i for i in job_ids if i not in found]
        if missing:
            raise HTTPException(status_code=404, detail=f"印张不存在: {missing}")
        not_done = [row["id"] for row in rows if row["status"] != "done"]
        if not_done:
            raise HTTPException(status_code=409, detail=f"印张尚未出结论: {not_done}")
        already = [row["id"] for row in rows if row["frozen_package_id"] is not None]
        if already:
            raise HTTPException(status_code=409, detail=f"印张已在冻结包内: {already}")
        package = conn.execute(
            "INSERT INTO frozen_packages (created_by, created_at) VALUES (%s, %s) RETURNING id",
            (user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        for row in rows:
            conn.execute(
                """INSERT INTO frozen_items
                       (package_id, job_id, sheet, cyan_mm, magenta_mm, verdict, reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    package["id"],
                    row["id"],
                    row["sheet"],
                    row["cyan_mm"],
                    row["magenta_mm"],
                    row["verdict"],
                    row["reason"],
                ),
            )
        conn.commit()
    return {"id": package["id"], "count": len(rows)}


@app.get("/api/freeze/packages")
def list_packages(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT p.id, p.created_by, p.created_at, COUNT(i.id) AS item_count
               FROM frozen_packages p
               LEFT JOIN frozen_items i ON i.package_id = p.id
               GROUP BY p.id
               ORDER BY p.id DESC"""
        ).fetchall()


@app.get("/api/freeze/packages/{package_id}")
def package_detail(package_id: int, _user: dict = Depends(current_user)):
    with connect() as conn:
        package = conn.execute(
            "SELECT id, created_by, created_at FROM frozen_packages WHERE id = %s",
            (package_id,),
        ).fetchone()
        if package is None:
            raise HTTPException(status_code=404, detail="冻结包不存在")
        items = conn.execute(
            """SELECT id, job_id, sheet, cyan_mm, magenta_mm, verdict, reason
               FROM frozen_items WHERE package_id = %s ORDER BY id""",
            (package_id,),
        ).fetchall()
    return {**package, "items": items}
