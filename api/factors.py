"""因子特征库路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import repo_dependency
from models.factor import FactorCreate
from sandbox.ast_check import check_source

router = APIRouter(prefix="/api/factors", tags=["因子库"])


_repo = repo_dependency


@router.get("")
async def list_factors(status: str | None = None, symbol: str | None = None, repo=Depends(_repo)):
    return {"factors": [f.model_dump() for f in repo.list_factors(status=status, symbol=symbol)]}


@router.get("/{factor_id}")
async def get_factor(factor_id: str, repo=Depends(_repo)):
    factor = repo.get_factor(factor_id)
    if not factor:
        raise HTTPException(status_code=404, detail="因子不存在")
    return factor.model_dump()


@router.post("", status_code=201)
async def create_factor(data: FactorCreate, repo=Depends(_repo)):
    sandbox = check_source(data.code)
    if not sandbox.ok:
        raise HTTPException(status_code=400, detail=f"沙盒检查未通过：{'；'.join(sandbox.errors)}")
    factor = repo.create_factor(data)
    return factor.model_dump()


@router.post("/{factor_id}/disable")
async def disable_factor(factor_id: str, repo=Depends(_repo)):
    factor = repo.update_status(factor_id, "disabled")
    if not factor:
        raise HTTPException(status_code=404, detail="因子不存在")
    return {"message": f"已禁用因子：{factor.name}", "factor": factor.model_dump()}


@router.put("/{factor_id}")
async def update_factor(factor_id: str, data: FactorCreate, repo=Depends(_repo)):
    sandbox = check_source(data.code)
    if not sandbox.ok:
        raise HTTPException(status_code=400, detail=f"沙盒检查未通过：{'；'.join(sandbox.errors)}")
    factor = repo.update_factor(factor_id, data)
    if not factor:
        raise HTTPException(status_code=404, detail="因子不存在")
    return {"message": f"已更新因子：{factor.name}", "factor": factor.model_dump()}


@router.post("/clear")
async def clear_factors(repo=Depends(_repo)):
    count = repo.clear_factors()
    return {"message": f"已清空因子库（{count} 个因子）", "deleted": count}


@router.post("/{factor_id}/enable")
async def enable_factor(factor_id: str, repo=Depends(_repo)):
    factor = repo.update_status(factor_id, "active")
    if not factor:
        raise HTTPException(status_code=404, detail="因子不存在")
    return {"message": f"已启用因子：{factor.name}", "factor": factor.model_dump()}


@router.delete("/{factor_id}")
async def delete_factor(factor_id: str, repo=Depends(_repo)):
    if not repo.delete_factor(factor_id):
        raise HTTPException(status_code=404, detail="因子不存在")
    return {"message": "因子已删除"}
