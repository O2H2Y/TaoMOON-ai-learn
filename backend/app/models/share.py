"""分享相关数据模型"""

from typing import Optional

from pydantic import BaseModel


class PosterQrcodeResponse(BaseModel):
    """海报小程序码：返回可持久访问的图片地址。"""

    qrcode_url: str


class SharedQuizDetailResponse(BaseModel):
    """公开只读的分享详情：只包含展示所需字段，不包含任何用户身份信息。"""

    quiz_id: str
    title: str
    summary: str
    questions: list  # raw JSON
    answer_records: Optional[list] = None
    report: Optional[dict] = None
    created_at: str