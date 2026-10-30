"""Column mapping for a stock list whose headers are not Loreg's own. This route may ask a model, so it lives apart from the numbers routes.

It stores nothing: it proposes a mapping for the owner to confirm, and POST /api/upload checks the confirmed mapping again.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.agents import columns as column_agent
from app.api.deps import get_conn, get_shop_id
from app.api.deps_ai import get_llm
from app.core import columns
from app.ingest import MAX_BYTES, UploadError, read_table
from app.storage import repo_ai

router = APIRouter(prefix="/api")


@router.post("/upload/mapping")
async def propose_mapping(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id), llm=Depends(get_llm)):
    """Multipart field `file`. Returns each header with the field code matched it to (exact name or alias), or the model's suggestion
    for what code could not place, how each was found, and whether the mapping is complete. Nothing is stored."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES + 4096:
        raise HTTPException(413, "file is larger than 5 MB")
    form = await request.form()
    f = form.get("file")
    if f is None or not hasattr(f, "read"):
        raise HTTPException(422, "send the CSV in a form field named file")
    try:
        headers, rows = read_table(await f.read())
    except UploadError as e:
        raise HTTPException(422, str(e)) from None
    known, _ = columns.match_known(headers)
    mapping, how, res = column_agent.propose(llm, headers, rows[:5], known)
    if res is not None:
        repo_ai.record_agent_calls(conn, shop_id, uuid.uuid4().hex, "column_mapping", getattr(llm, "backend", None) or "unknown",
                                   [("columns", res.outcome, 0, False)])
    problems = columns.validate(mapping, headers, rows[:20])
    return {"headers": headers, "mapping": {h: mapping.get(h) for h in headers}, "how": {h: how.get(h, "none") for h in headers},
            "complete": not problems, "problems": problems,
            "model": None if res is None else {"source": res.source, "outcome": res.outcome},
            "fields": [{"name": n, "label": columns.LABELS[n], "required": n in columns.REQUIRED} for n in columns.FIELDS],
            "sample": [{h: str(r.get(h) or "")[:40] for h in headers} for r in rows[:3]]}
