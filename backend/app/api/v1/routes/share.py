"""分享路由：海报小程序码 + 免登录只读的完整报告。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.auth import get_optional_user
from app.models.common import ApiResponse
from app.models.share import SharedQuizDetailResponse
from app.repositories import quiz_repository
from app.services import wechat_qrcode_service

router = APIRouter(prefix="/share", tags=["share"])


@router.get("/poster-qrcode", response_model=ApiResponse)
async def get_poster_qrcode(
    quiz_id: str = Query(..., min_length=1, max_length=64),
    _user_id: Optional[int] = Depends(get_optional_user),
):
    """生成分享海报用的小程序码（扫码可进入完整版报告页）。"""
    if not await quiz_repository.quiz_exists(quiz_id):
        return ApiResponse.error(code=4004, message="闯关记录不存在或未保存，暂时无法生成分享码")

    qrcode_url = await wechat_qrcode_service.generate_poster_qrcode(quiz_id)
    return ApiResponse.success(data={"qrcode_url": qrcode_url})


@router.get("/quizzes/{quiz_id}", response_model=ApiResponse)
async def get_shared_quiz_detail(quiz_id: str):
    """公开只读的完整报告：无需登录，且只返回展示所需字段（不含任何用户身份信息）。"""
    detail = await quiz_repository.get_public_quiz_detail(quiz_id)
    if detail is None:
        return ApiResponse.error(code=4004, message="报告不存在或已失效")

    shared = SharedQuizDetailResponse(
        quiz_id=detail.get("quiz_id") or quiz_id,
        title=detail.get("title") or "",
        summary=detail.get("summary") or "",
        questions=detail.get("questions") or [],
        answer_records=detail.get("answer_records"),
        report=detail.get("report"),
        created_at=detail.get("created_at") or "",
    )
    return ApiResponse.success(data=shared.model_dump())