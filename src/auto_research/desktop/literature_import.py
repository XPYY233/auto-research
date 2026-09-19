"""Desktop PDF import orchestration using the existing scientific import rules."""
from __future__ import annotations

from auto_research.evidence.uploads import UploadService
from .routing import DesktopErrorDTO, DesktopFacadeError, RequestContext


class LiteratureImportController:
    def __init__(self, service: UploadService) -> None:
        self.service = service

    def __call__(self, request: RequestContext) -> dict:
        def one(name: str, default=None):
            values = request.query.get(name)
            return values[0] if values else default

        try:
            year_text = one('year')
            year = int(year_text) if year_text else None
            if not isinstance(request.payload, bytes) or len(request.payload) != request.body_size:
                raise ValueError('invalid binary payload')
            return self.service.upload(
                request.payload, one('filename', 'uploaded.pdf'),
                title=one('title'), doi=one('doi'), year=year,
                first_author=one('first_author'),
                corresponding_author=one('corresponding_author'),
            )
        except ValueError:
            raise DesktopFacadeError(DesktopErrorDTO(
                'literature_import_invalid', 'PDF 或文献信息无效，请核对文件后重试。', 400,
            )) from None
